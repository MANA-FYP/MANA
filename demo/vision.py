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
from dataclasses import dataclass

# Typical real heights in metres, used to turn a box height into a distance.
OBSTACLE_HEIGHT_M = {
    "person": 1.7,
    "bicycle": 1.1,
    "motorcycle": 1.2,
    "car": 1.5,
    "bus": 3.0,
    "truck": 2.8,
    "dog": 0.5,
    "chair": 0.9,
    "bench": 0.85,
    "dining table": 0.75,
    "couch": 0.85,
    "bed": 0.6,
    "potted plant": 0.6,
    "suitcase": 0.65,
    "refrigerator": 1.7,
    "toilet": 0.75,
    "fire hydrant": 0.75,
    "table": 0.75,  # names from the open-vocabulary list in build_detector.py; "door" is left
    # out on purpose: a door that fills the frame always looks about 2 m away, at every doorway
    "desk": 0.75,
    "wheelchair": 1.0,
    "trash can": 0.7,
    "traffic cone": 0.7,
}

# Defaults for a typical phone camera held upright. They are assumptions: calibrate once with
# calibrate_focal() by standing a person at a known distance.
DEFAULT_VFOV_RAD = math.radians(55.0)
DEFAULT_HFOV_RAD = math.radians(42.0)


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def height_px(self) -> float:
        return max(0.0, self.y2 - self.y1)

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2.0


@dataclass(frozen=True)
class Obstacle:
    label: str
    confidence: float
    distance_m: float
    bearing_rad: float  # positive to the left


def focal_from_fov(image_height_px: float, vfov_rad: float = DEFAULT_VFOV_RAD) -> float:
    """Focal length in pixels from the vertical field of view."""
    return (image_height_px / 2.0) / math.tan(vfov_rad / 2.0)


def calibrate_focal(box_height_px: float, real_height_m: float, distance_m: float) -> float:
    """Focal length in pixels from one object of known height at a known distance."""
    return box_height_px * distance_m / real_height_m


def distance_m(box_height_px: float, real_height_m: float, focal_px: float) -> float:
    """Distance to an object of known height. inf when the box has no height."""
    if box_height_px <= 0:
        return math.inf
    return focal_px * real_height_m / box_height_px


def bearing_rad(
    center_x_px: float, image_width_px: float, hfov_rad: float = DEFAULT_HFOV_RAD
) -> float:
    """Angle from straight ahead, positive to the left (REP 103 as in the message files)."""
    offset = (center_x_px - image_width_px / 2.0) / (image_width_px / 2.0)  # -1 left .. +1 right
    return -offset * (hfov_rad / 2.0)


def to_obstacles(
    detections: list[Detection],
    image_width_px: float,
    image_height_px: float,
    focal_px: float | None = None,
    min_confidence: float = 0.4,
) -> list[Obstacle]:
    """Keep the classes that count as obstacles and estimate where they are, nearest first."""
    focal = focal_px or focal_from_fov(image_height_px)
    found = []
    for d in detections:
        height = OBSTACLE_HEIGHT_M.get(d.label)
        if height is None or d.confidence < min_confidence:
            continue
        dist = distance_m(d.height_px, height, focal)
        if math.isinf(dist):
            continue
        found.append(Obstacle(d.label, d.confidence, dist, bearing_rad(d.center_x, image_width_px)))
    return sorted(found, key=lambda o: o.distance_m)


# ---- the models, loaded only when installed


class Detector:
    """An Ultralytics object detector: YOLO26 on the COCO classes, or the open-vocabulary YOLOE
    built by build_detector.py. imgsz is the size each frame is scaled to: 416 by default, as at the
    mid-evaluation; 640 finds small objects best and is slower on a laptop CPU."""

    def __init__(self, model, imgsz: int = 416):
        self.model = model
        self.imgsz = imgsz

    @classmethod
    def load(cls, weights: str = "yolo26n.pt", imgsz: int = 416) -> Detector | None:
        try:
            ultralytics = importlib.import_module("ultralytics")
        except ImportError:
            return None
        try:
            return cls(ultralytics.YOLO(weights), imgsz)
        except Exception:  # the weights cannot be found or downloaded
            return None

    def detect(self, jpeg: bytes, conf: float = 0.3) -> tuple[list[Detection], int, int]:
        """Detections in one JPEG frame, with the frame's width and height."""
        from PIL import Image

        image = Image.open(io.BytesIO(jpeg)).convert("RGB")
        result = self.model(image, imgsz=self.imgsz, conf=conf, verbose=False)[0]
        names = result.names
        found = []
        for box in result.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            found.append(Detection(names[int(box.cls[0])], float(box.conf[0]), x1, y1, x2, y2))
        return found, image.width, image.height


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
