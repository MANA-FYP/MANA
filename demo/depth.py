"""Depth from the camera image alone (#131): ground reports and obstacle distances in metres.

    camera frame --> depth model --> depth image, scaled so the floor just ahead is where it must be
                                        |                                |
                     mana_perception.depth.read_ground       median depth inside each box
                     (flat, ramp, curb, stairs, drop)         (obstacle distance)

The model is Depth Anything V2, metric, small (Apache-2.0), on the laptop's CPU. Its metres can be
off by 10 to 20 percent on a phone's picture, and the ground reader needs better than that: it
compares the ground with the camera's height to find an edge. So the depth is scaled first. The
phone's motion sensors give the camera's tilt, the person holds it at a known height, and the floor
at the bottom of the image must then lie that far below the camera: one number fixes the scale.
With that, the synthetic tests read the same ground with the model's scale 20 percent off.

It is the same camera as the detector, so for the decision core it is the camera (Source.CAMERA),
not a second sensor: a step it sees slows or stops, but changing mode still needs another sensor.
"""

from __future__ import annotations

import io
import math
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src" / "mana_perception") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception import depth as dp  # noqa: E402

MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"
MIN_PITCH = math.radians(15)  # less than this, the bottom of the image may not be the floor
FLOOR_SPREAD = 0.15  # the floor rows must agree on the scale to within this share


@dataclass(frozen=True)
class Mount:
    """How the camera is held: its height above the floor and its field of view across the long
    side of the picture (a phone's main camera is about 65 to 70 degrees)."""

    height_m: float = 1.2
    long_fov_deg: float = 66.0

    def camera(self, cols: int, rows: int, pitch_rad: float) -> dp.Camera:
        f = (max(cols, rows) / 2.0) / math.tan(math.radians(self.long_fov_deg) / 2.0)
        return dp.Camera(f, f, (cols - 1) / 2.0, (rows - 1) / 2.0, self.height_m, pitch_rad)


def camera_pitch(ax: float, ay: float, az: float) -> float | None:
    """How far the back camera looks below level, in radians, from the phone's gravity reading
    (accelerationIncludingGravity: +9.8 on the axis pointing up). Upright gives 0, lying flat
    with the screen up gives pi/2 (looking at the floor)."""
    g = math.sqrt(ax * ax + ay * ay + az * az)
    return None if g < 5.0 else math.asin(max(-1.0, min(1.0, az / g)))


def floor_scale(depth: np.ndarray, cam: dp.Camera, rows_share=0.15, strip=0.3) -> float | None:
    """The factor that puts the floor at the bottom of the middle strip at cam.height_m below the
    camera. None when the camera looks too level to see the floor, or the rows do not agree (not
    a floor: a person, a table)."""
    if cam.pitch_rad < MIN_PITCH:
        return None
    rows, cols = depth.shape
    v = np.arange(rows - max(3, int(rows * rows_share)), rows)
    u = slice(int(cols * (0.5 - strip / 2)), int(cols * (0.5 + strip / 2)))
    down = (v - cam.cy) / cam.fy * math.cos(cam.pitch_rad) + math.sin(cam.pitch_rad)
    z = depth[v, u]
    ok = np.isfinite(z) & (z > 0) & (down[:, None] > 0.05)
    if ok.sum() < 20:
        return None
    each = (cam.height_m / (z * down[:, None]))[ok]
    mid = float(np.median(each))
    q1, q3 = np.percentile(each, [25, 75])
    return mid if (q3 - q1) / mid <= FLOOR_SPREAD else None


@dataclass(frozen=True)
class Seen:
    """One depth image, as the server keeps it."""

    depth: np.ndarray  # metres, scaled to the floor when it could be
    scale: float | None  # the factor applied, None when the model's own metres were kept
    reading: dp.Reading | None  # the ground, None when the floor was not in view
    ahead_m: float | None = None  # the nearest thing standing in the path
    why: str = ""  # why there is no reading
    cam: dp.Camera | None = None  # as it was held for this image


def read(depth: np.ndarray, mount: Mount, pitch_rad: float | None) -> Seen:
    """Scale one depth image to the floor and read the ground and what stands in the path."""
    if pitch_rad is None:
        return Seen(depth, None, None, why="no tilt from the motion sensor")
    cam = mount.camera(depth.shape[1], depth.shape[0], pitch_rad)
    scale = floor_scale(depth, cam)
    if scale is None:
        why = "tilt the camera down to the floor" if pitch_rad < MIN_PITCH else "no floor in view"
        return Seen(depth, None, None, why=why)
    scaled = depth * scale
    reading = dp.read_ground(scaled, cam)
    return Seen(scaled, scale, reading, nearest_ahead(scaled, cam), cam=cam)


def nearest_ahead(
    depth: np.ndarray, cam: dp.Camera, low_m=0.3, high_m=2.0, strip=0.3, share=0.01
) -> float | None:
    """How far ahead the nearest thing in the path is that stands between low_m and high_m above
    the floor (a wall, a door, a person, a pole), whatever it is: the detector names things, this
    measures them. The depth must be scaled to the floor. None when nothing stands in the path."""
    rows, cols = depth.shape
    u = slice(int(cols * (0.5 - strip / 2)), int(cols * (0.5 + strip / 2)))
    z = depth[:, u]
    yn = ((np.arange(rows) - cam.cy) / cam.fy)[:, None]
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    ahead = z * (c - yn * s)
    height = cam.height_m - z * (yn * c + s)
    hit = np.isfinite(z) & (z > 0) & (height >= low_m) & (height <= high_m) & (ahead > 0.2)
    if hit.sum() < max(10, share * z.size):
        return None
    return float(np.percentile(ahead[hit], 5))


def box_distance(seen: Seen, box, frame_w: int, frame_h: int) -> float | None:
    """How far ahead an object is, along the floor: the median over the middle half of its box (the
    edges of a box hold background). box has x1, y1, x2, y2 in the frame's pixels. None unless the
    depth was scaled to the floor: the model's own metres can be 10 to 20 percent off, or more on
    a scene unlike its training (the indoor model outdoors)."""
    if seen.scale is None or seen.cam is None:
        return None
    depth, cam = seen.depth, seen.cam
    rows, cols = depth.shape
    sx, sy = cols / frame_w, rows / frame_h
    w, h = box.x2 - box.x1, box.y2 - box.y1
    x1, x2 = int((box.x1 + w / 4) * sx), int(math.ceil((box.x2 - w / 4) * sx))
    y1, y2 = int((box.y1 + h / 4) * sy), int(math.ceil((box.y2 - h / 4) * sy))
    y1, y2 = max(0, y1), min(rows, max(y2, y1 + 1))
    z = depth[y1:y2, max(0, x1) : min(cols, max(x2, x1 + 1))]
    yn = ((np.arange(y1, y2) - cam.cy) / cam.fy)[:, None]
    ahead = z * (math.cos(cam.pitch_rad) - yn * math.sin(cam.pitch_rad))
    ahead = ahead[np.isfinite(ahead) & (z > 0)]
    return float(np.median(ahead)) if ahead.size else None


def colour(depth: np.ndarray, far_m: float | None = None) -> bytes:
    """A JPEG of the depth for the dashboard: near is warm, far is dark blue. The far end is the
    image's own 95th percentile (at least 3 m) unless far_m is given, so a room and a street both
    use the whole range."""
    from PIL import Image

    if far_m is None:
        valid = depth[np.isfinite(depth) & (depth > 0)]
        far_m = max(3.0, float(np.percentile(valid, 95))) if valid.size else 6.0
    t = np.clip(np.nan_to_num(depth, nan=far_m, posinf=far_m) / far_m, 0.0, 1.0)
    r = np.clip(1.5 - 2.0 * t, 0, 1)
    g = np.clip(1.2 - np.abs(2.2 * t - 0.9), 0, 1)
    b = np.clip(0.3 + 1.2 * t, 0, 1) * (1.0 - 0.6 * t)
    rgb = (np.stack([r, g, b], axis=-1) * 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgb).save(buf, "JPEG", quality=70)
    return buf.getvalue()


class DepthModel:
    """Depth Anything V2 through transformers. load() returns None when torch or transformers is
    missing or the model cannot be read; the server then runs without depth."""

    def __init__(self, processor, model, size: int):
        self.processor, self.model, self.size = processor, model, size

    @classmethod
    def load(cls, name: str = MODEL, size: int = 266) -> DepthModel | None:
        try:
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation

            processor = AutoImageProcessor.from_pretrained(name)
            model = AutoModelForDepthEstimation.from_pretrained(name).eval()
        except Exception as e:  # not installed, not downloaded, no network
            print(f"depth model not loaded ({name}): {e}", file=sys.stderr)
            return None
        return cls(processor, model, size)

    def estimate(self, jpeg: bytes) -> np.ndarray:
        """Depth in metres along the optical axis, at the model's output size (the frame's shape,
        smaller)."""
        import torch
        from PIL import Image

        with Image.open(io.BytesIO(jpeg)) as im:
            image = im.convert("RGB")
        size = {"height": self.size, "width": self.size}
        inputs = self.processor(images=image, return_tensors="pt", size=size)
        with torch.inference_mode():
            out = self.model(**inputs).predicted_depth
        return out[0].float().numpy()
