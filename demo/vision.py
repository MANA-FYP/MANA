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
    Detection,
    Detector,
    Obstacle,
    bearing_rad,
    calibrate_focal,
    distance_m,
    focal_from_fov,
    to_obstacles,
)

# StairNet-style classes, in the order the model was trained with. Change it to match the model.
STAIR_CLASSES = ("level", "to_stairs", "stairs", "to_level")


def stair_ground(label: str) -> str:
    """The ground type a classifier label stands for: stairs, or flat."""
    return "stairs" if label in ("to_stairs", "stairs") else "flat"


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
