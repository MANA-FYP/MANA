"""Camera vision for the phone demo: obstacles from an object detector, and the ground classifier
hook. The maths is pure Python and testable; the models load lazily and are optional.

Honest limits, which the dashboard repeats:
- The default detector (YOLO26n) knows only its COCO classes, not stairs or curbs. The
  open-vocabulary YOLOE-26 from build_detector.py also names stairs, doors and poles, but only as
  labels: a distance is estimated only for the classes in OBSTACLE_HEIGHT_M, and only those reach
  the decision core.
- The distance is estimated from the size of the box and a typical height for the class. It is an
  estimate, not a measurement, and it depends on the camera calibration below.
- If a model is not installed, nothing is invented: the loader returns None and the caller marks
  the sensor as unavailable.
"""

from __future__ import annotations

import importlib
import io
import math
import sys
from pathlib import Path

# The detector and its maths live in mana_perception, so the ROS node gives the same obstacles.
_PERCEPTION = Path(__file__).resolve().parents[1] / "src" / "mana_perception"
if str(_PERCEPTION) not in sys.path:
    sys.path.insert(0, str(_PERCEPTION))

from mana_perception.detection import (  # noqa: E402, F401  (re-exported for the demo)
    DEFAULT_HFOV_RAD,
    DEFAULT_VFOV_RAD,
    OBSTACLE_HEIGHT_M,
    OBSTACLE_WIDTH_M,
    Detection,
    Detector,
    Obstacle,
    bearing_rad,
    brightness,
    calibrate_focal,
    camera_trust_factor,
    distance_m,
    focal_from_fov,
    to_obstacles,
)

# StairNet-style classes, in the order the model was trained with. Change it to match the model.
STAIR_CLASSES = ("level", "to_stairs", "stairs", "to_level")


def stair_ground(label: str) -> str:
    """The ground type a classifier label stands for: stairs, or flat. Only level ground is flat:
    at the top of the stairs (to_level, StairNet's IS-LG) the last steps are still ahead, and going
    back to wheels waits for the IMU to say the chair is level (docs/datasets.md)."""
    return "flat" if label == "level" else "stairs"


def preprocess(image, size: int = 224):
    """An RGB PIL image as a 1x3xSxS float32 array with ImageNet normalisation."""
    import numpy as np

    arr = np.asarray(image.convert("RGB").resize((size, size)), dtype="float32") / 255.0
    arr = (arr - np.array([0.485, 0.456, 0.406], dtype="float32")) / np.array(
        [0.229, 0.224, 0.225], dtype="float32"
    )
    return arr.transpose(2, 0, 1)[None, ...].astype("float32")


class GroundClassifier:
    """The stairs / level classifier (MobileNetV2 exported to ONNX). Needs a trained model file."""

    def __init__(self, session, classes=STAIR_CLASSES):
        self.session, self.classes = session, classes

    @classmethod
    def load(cls, path: str, classes=STAIR_CLASSES) -> GroundClassifier | None:
        try:
            ort = importlib.import_module("onnxruntime")
            return cls(ort.InferenceSession(path, providers=["CPUExecutionProvider"]), classes)
        except Exception:  # onnxruntime or the model file is missing
            return None

    def classify(self, jpeg: bytes) -> tuple[str, float]:
        from PIL import Image

        x = preprocess(Image.open(io.BytesIO(jpeg)))
        logits = self.session.run(None, {self.session.get_inputs()[0].name: x})[0][0]
        e = [math.exp(v - max(logits)) for v in logits]
        probs = [v / sum(e) for v in e]
        best = max(range(len(probs)), key=probs.__getitem__)
        return self.classes[best], probs[best]


class StairSegmenter:
    """Stairs found pixel by pixel (tools/train_stair_seg.py, a U-Net trained on SANPO's own stair
    masks), in the classifier's place: classify() says "stairs" or "level" as the ONNX classifier
    did. A box around a whole staircase fits it badly, and the detector's boxes reach only about
    0.23 AP50 on SANPO; per pixel the same frames give 0.77 IoU and 0.85 recall
    (docs/results.md), and a staircase half in view is still its visible pixels. region() then
    gives where the stairs are, as a box the floor and depth code can measure."""

    def __init__(self, model, device: str, min_share: float = 0.02, size: int = 256):
        self.model, self.device, self.min_share, self.size = model, device, min_share, size
        self.mask = None  # the last frame's stair probabilities, size x size

    @classmethod
    def load(cls, path: str) -> StairSegmenter | None:
        try:
            import torch

            root = str(Path(__file__).resolve().parents[1])
            if root not in sys.path:
                sys.path.insert(0, root)
            from demo.depth import device
            from tools.train_stair_seg import UNet

            on = device()
            model = UNet(3)
            model.load_state_dict(torch.load(path, map_location="cpu"))
            return cls(model.eval().to(on), on)
        except Exception as e:  # torch or the weights missing
            print(f"stairs segmentation not loaded ({path}): {e}", file=sys.stderr)
            return None

    def segment(self, jpeg: bytes):
        """Stair probability per pixel, at size x size."""
        import numpy as np
        import torch
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as im:
            rgb = np.asarray(im.convert("RGB").resize((self.size, self.size)), dtype="float32")
        x = torch.from_numpy((rgb / 255.0).transpose(2, 0, 1)[None]).to(self.device)
        with torch.inference_mode():
            self.mask = torch.sigmoid(self.model(x))[0, 0].float().cpu().numpy()
        return self.mask

    def classify(self, jpeg: bytes) -> tuple[str, float]:
        mask = self.segment(jpeg)
        stairs = mask > 0.5
        if stairs.mean() < self.min_share:
            return "level", float(1.0 - mask.mean())
        return "stairs", float(mask[stairs].mean())

    def region(self, frame_w: int, frame_h: int) -> Detection | None:
        """The last frame's stairs as a box in the frame: the 2nd to 98th percentile of their
        pixels, so stray pixels do not stretch it. None when there are no stairs."""
        import numpy as np

        if self.mask is None:
            return None
        stairs = self.mask > 0.5
        if stairs.mean() < self.min_share:
            return None
        rows, cols = np.nonzero(stairs)
        sx, sy = frame_w / self.size, frame_h / self.size
        y1, y2 = np.percentile(rows, [2, 98]) * sy
        x1, x2 = np.percentile(cols, [2, 98]) * sx
        conf = float(self.mask[stairs].mean())
        return Detection("stairs", conf, float(x1), float(y1), float(x2), float(y2 + sy))
