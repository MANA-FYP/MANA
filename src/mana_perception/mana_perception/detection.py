"""The object detector and its maths (M1 Vision), shared by the phone demo and the ROS node.

demo/vision.py and mana_perception.detector_node both use this module, so the phone and ROS 2 give
the same obstacles from the same picture. The maths is pure Python; the detector (Ultralytics YOLO26
or the open-vocabulary YOLOE-26 from demo/build_detector.py) loads lazily and is optional.

- The distance is estimated from the height of the box and a typical height for the class. It is
  an estimate, not a measurement, and it depends on the camera's focal length: from the field of
  view on the phone, from the camera info in ROS.
- Only the classes in OBSTACLE_HEIGHT_M get a distance; other labels are names only.
- If a model is not installed, nothing is invented: the loader returns None.
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

# Typical widths in metres, for a box cut by the top or the bottom of the frame (legs below it, or
# a person too close to fit): the box then shows only part of the object, so its height gives a
# distance that is too far, but its width still grows as the object comes closer. Shoulders, on
# the narrow side, so the distance errs closer.
OBSTACLE_WIDTH_M = {"person": 0.4}
EDGE = 0.01  # a box within this share of the image height of the top or the bottom is cut

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


def cut_by_frame(d: Detection, image_height_px: float) -> bool:
    """True when the box runs off the top or the bottom of the frame."""
    margin = max(2.0, EDGE * image_height_px)
    return d.y1 <= margin or d.y2 >= image_height_px - margin


def to_obstacles(
    detections: list[Detection],
    image_width_px: float,
    image_height_px: float,
    focal_px: float | None = None,
    min_confidence: float = 0.4,
    hfov_rad: float = DEFAULT_HFOV_RAD,
) -> list[Obstacle]:
    """Keep the classes that count as obstacles and estimate where they are, nearest first.

    The distance comes from the box height and a typical height for the class. A box cut by the top
    or the bottom of the frame shows only part of the object, so its height gives a distance too
    far, and a person filling the frame never reads closer than about 1.6 m. For such a box the
    width is used too, where the class has a typical width, and the nearer of the two is kept.
    """
    focal = focal_px or focal_from_fov(image_height_px)
    found = []
    for d in detections:
        height = OBSTACLE_HEIGHT_M.get(d.label)
        if height is None or d.confidence < min_confidence:
            continue
        dist = distance_m(d.height_px, height, focal)
        width = OBSTACLE_WIDTH_M.get(d.label)
        if width is not None and cut_by_frame(d, image_height_px):
            dist = min(dist, distance_m(d.x2 - d.x1, width, focal))  # square pixels
        if math.isinf(dist):
            continue
        bearing = bearing_rad(d.center_x, image_width_px, hfov_rad)
        found.append(Obstacle(d.label, d.confidence, dist, bearing))
    return sorted(found, key=lambda o: o.distance_m)


# ---- light: the camera is trusted less in the dark, on the phone and in ROS alike


def camera_trust_factor(brightness: float) -> float:
    """0 in the dark, 1 in good light; brightness is the mean image luminance, 0 to 1."""
    return max(0.0, min(1.0, (brightness - 0.05) / 0.25))


def brightness(rgb) -> float:
    """Mean luminance of an HxWx3 RGB image, 0 (black) to 1 (white), as PIL's "L" weighs it."""
    import numpy as np

    image = np.asarray(rgb)
    rows, cols = image.shape[:2]
    small = image[:: max(1, rows // 24), :: max(1, cols // 32), :3]  # about 32 x 24 points
    luma = small.astype(np.float64) @ np.array([0.299, 0.587, 0.114])
    return float(luma.mean() / 255.0)


# ---- ROS images

CHANNELS = {"rgb8": (3, False), "bgr8": (3, True), "rgba8": (4, False), "bgra8": (4, True)}


def to_rgb(encoding: str, rows: int, cols: int, step: int, data):
    """A colour image's bytes as an HxWx3 RGB array, or None for an encoding not supported."""
    import numpy as np

    if encoding not in CHANNELS:
        return None
    channels, bgr = CHANNELS[encoding]
    raw = np.frombuffer(bytes(data), dtype=np.uint8, count=rows * step)
    image = raw.reshape(rows, step)[:, : cols * channels].reshape(rows, cols, channels)[..., :3]
    return np.ascontiguousarray(image[..., ::-1] if bgr else image)


def hfov_from(fx: float, width: float) -> float:
    """Horizontal field of view in radians from the focal length in pixels."""
    return 2.0 * math.atan((width / 2.0) / fx)


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
        return self.detect_image(image, conf), image.width, image.height

    def detect_image(self, image, conf: float = 0.3) -> list[Detection]:
        """Detections in a decoded frame: an RGB PIL image or an HxWx3 RGB array."""
        result = self.model(image, imgsz=self.imgsz, conf=conf, verbose=False)[0]
        names = result.names
        found = []
        for box in result.boxes:
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            found.append(Detection(names[int(box.cls[0])], float(box.conf[0]), x1, y1, x2, y2))
        return found
