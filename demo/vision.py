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
    0.23 AP50 on SANPO; per pixel, on SANPO's held-out test walks, it marks about a third of the
    stair pixels (IoU 0.34, recall 0.36, precision 0.90)
    (docs/results.md), and a staircase half in view is still its visible pixels. region() then
    gives where the stairs are, as a box the floor and depth code can measure.

    Several models may be given (load("a.pt,b.pt")): a pixel is stairs when any of them says so.
    A model trained only on plain frames found no stairs at all in a dark copy of SANPO's
    validation frames; one also trained on dim frames found half; together they keep the first's
    IoU in good light (0.822 against 0.824) with more recall (0.904 against 0.873).

    A model trained with depth as a fourth channel (tools/train_stair_seg.py --depth) is given the
    demo's own depth for the frame (Depth Anything V2, raw metres) when there is one; without it,
    that model sits the frame out. On SANPO's held-out test walks, depth estimated that way gave
    about 3 IoU points over colour alone, less than the spread between training runs
    (docs/results.md), so none is run live."""

    def __init__(self, model, device: str, min_share: float = 0.02, size: int = 256):
        self.models = list(model) if isinstance(model, (list, tuple)) else [model]
        self.channels = [m.e1[0].in_channels if hasattr(m, "e1") else 3 for m in self.models]
        self.wants_depth = 4 in self.channels  # the server then passes the frame's depth
        self.device, self.min_share, self.size = device, min_share, size
        self.mask = None  # the last frame's stair probabilities, size x size

    @classmethod
    def load(cls, path: str) -> StairSegmenter | None:
        """path: one weights file, or several separated by commas."""
        try:
            import torch

            root = str(Path(__file__).resolve().parents[1])
            if root not in sys.path:
                sys.path.insert(0, root)
            from demo.depth import device
            from tools.train_stair_seg import Pretrained, UNet

            on = device()
            models = []
            for one in path.split(","):
                state = torch.load(one.strip(), map_location="cpu")
                if any(k.startswith("net.") for k in state):  # --arch pretrained; weights all here
                    model = Pretrained(pretrained=False)
                else:  # 3 colours, or 4 with depth
                    model = UNet(int(state["e1.0.weight"].shape[1]))
                model.load_state_dict(state)
                models.append(model.eval().to(on))
            return cls(models, on)
        except Exception as e:  # torch or the weights missing
            print(f"stairs segmentation not loaded ({path}): {e}", file=sys.stderr)
            return None

    def depth_channel(self, depth):
        """Depth in metres as the fourth channel, as training made it (train_stair_seg.CLIP_M)."""
        import numpy as np
        from PIL import Image

        d = np.nan_to_num(np.asarray(depth, dtype="float32"), nan=0.0, posinf=20.0, neginf=0.0)
        d = np.clip(d, 0.0, 20.0) / 20.0
        img = Image.fromarray((d * 255).astype("uint8")).resize((self.size, self.size))
        return np.asarray(img, dtype="float32")[None] / 255.0

    def segment(self, jpeg: bytes, depth=None):
        """Stair probability per pixel, at size x size: the highest any model gives. depth: the
        frame's depth in metres, for the models that take it."""
        import numpy as np
        import torch
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as im:
            rgb = np.asarray(im.convert("RGB").resize((self.size, self.size)), dtype="float32")
        colour = (rgb / 255.0).transpose(2, 0, 1)
        x = torch.from_numpy(colour[None]).to(self.device)
        x4 = None
        if depth is not None and self.wants_depth:
            both = np.concatenate([colour, self.depth_channel(depth)])
            x4 = torch.from_numpy(both[None].astype("float32")).to(self.device)
        with torch.inference_mode():
            probs = [
                torch.sigmoid(m(x4 if ch == 4 else x))[0, 0]
                for m, ch in zip(self.models, self.channels, strict=True)
                if ch == 3 or x4 is not None
            ]
            if probs:
                self.mask = torch.stack(probs).max(0).values.float().cpu().numpy()
            else:  # only depth models, and no depth for this frame
                self.mask = np.zeros((self.size, self.size), dtype="float32")
        return self.mask

    def classify(self, jpeg: bytes, depth=None) -> tuple[str, float]:
        mask = self.segment(jpeg, depth)
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


class GlassSegmenter(StairSegmenter):
    """Glass found pixel by pixel (tools/train_glass_seg.py, trained on GSD-S's glass masks). A
    3D camera's light mostly passes through glass, so a glass wall or a closed glass door ahead
    can read as open way; the phone's estimated depth mostly puts a surface there, but nothing
    says it is glass. Only glass in the path counts: the middle third of the picture, where the
    user is heading."""

    def __init__(self, model, device: str, min_share: float = 0.02, size: int = 320):
        super().__init__(model, device, min_share, size)

    def in_path(self, frame_w: int, frame_h: int) -> Detection | None:
        """The last frame's glass in the middle third of the picture, as a box in the frame
        (the 2nd to 98th percentile of its pixels); its bottom edge is where it meets the floor.
        None when there is less than min_share of it there."""
        import numpy as np

        if self.mask is None:
            return None
        third = self.size // 3
        glass = self.mask[:, third : 2 * third] > 0.5
        if glass.mean() < self.min_share:
            return None
        rows, cols = np.nonzero(glass)
        sx, sy = frame_w / self.size, frame_h / self.size
        y1, y2 = np.percentile(rows, [2, 98]) * sy
        x1, x2 = (np.percentile(cols, [2, 98]) + third) * sx
        conf = float(self.mask[:, third : 2 * third][glass].mean())
        return Detection("glass", conf, float(x1), float(y1), float(x2), float(y2 + sy))
