"""Depth from the camera image alone (#131): ground reports and obstacle distances in metres.

    camera frame --> depth model --> depth image, scaled so the floor just ahead is where it must be
                                        |                                |
                     mana_perception.depth.read_ground       median depth inside each box
                     (flat, ramp, curb, stairs, drop)         (obstacle distance)

The model is Depth Anything V2, metric, small (Apache-2.0), on the laptop's CPU. Its metres can be
off by 10 to 20 percent on a phone's picture, and the ground reader needs better than that: it
compares the ground with the camera's height to find an edge. So the depth is scaled first. The
phone's motion sensors give the camera's tilt, the person holds it at a known height, and level
floor at the bottom of the image must then lie that far below the camera: one number fixes the
scale. It is learned from frames of level floor and then held (Reader), so an edge close to the
person still shows. With that, the synthetic tests read the same ground with the model's scale
20 percent off.

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
MAX_FLOOR_M = 4.0  # the bottom of the picture must show the floor closer than this
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


def floor_at(cam: dp.Camera, row: float) -> float | None:
    """How far ahead, along the floor, the floor seen at this image row is: from the camera's
    height and tilt alone, no depth needed. None at or above the horizon."""
    yn = (row - cam.cy) / cam.fy
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    down = yn * c + s  # metres down per metre along the ray
    if down <= 0.02:
        return None
    return cam.height_m * (c - yn * s) / down


def floor_in_view(cam: dp.Camera, rows: int) -> bool:
    """The bottom of the picture shows the floor within MAX_FLOOR_M."""
    ahead = floor_at(cam, rows - 1)
    return ahead is not None and ahead <= MAX_FLOOR_M


def stairs_ground(box, frame_w: int, frame_h: int, mount: Mount, pitch_rad, seen=None):
    """A detector's stairs box as ground: (code, distance). The distance is where the box's
    bottom edge meets the floor, from the camera's height and tilt (NaN without a tilt). Up or
    down comes from the depth when a scaled one is there: the middle of the box above the floor
    is steps up, below it steps down; without depth, steps up. A box says what is there; the
    floor and the depth say where and which way."""
    distance = math.nan
    if pitch_rad is not None:
        at = floor_at(mount.camera(frame_w, frame_h, pitch_rad), box.y2)
        distance = math.nan if at is None else at
    ground = dp.STAIRS_UP
    if seen is not None and seen.scale is not None and seen.cam is not None:
        height = box_height(seen, box, frame_w, frame_h)
        if height is not None and height < -0.1:
            ground = dp.STAIRS_DOWN
    return ground, distance


def box_height(seen, box, frame_w: int, frame_h: int) -> float | None:
    """The median height above the person's floor of what is in the middle of a box."""
    depth, cam = seen.depth, seen.cam
    rows, cols = depth.shape
    sx, sy = cols / frame_w, rows / frame_h
    w, h = box.x2 - box.x1, box.y2 - box.y1
    x1, x2 = int((box.x1 + w / 4) * sx), int(math.ceil((box.x2 - w / 4) * sx))
    y1, y2 = int((box.y1 + h / 4) * sy), int(math.ceil((box.y2 - h / 4) * sy))
    y1, y2 = max(0, y1), min(rows, max(y2, y1 + 1))
    z = depth[y1:y2, max(0, x1) : min(cols, max(x2, x1 + 1))]
    yn = ((np.arange(y1, y2) - cam.cy) / cam.fy)[:, None]
    height = cam.height_m - z * (yn * math.cos(cam.pitch_rad) + math.sin(cam.pitch_rad))
    height = height[np.isfinite(height) & (z > 0)]
    return float(np.median(height)) if height.size else None


def floor_scale(depth: np.ndarray, cam: dp.Camera, rows_share=0.15, strip=0.3) -> float | None:
    """The factor that puts the floor at the bottom of the middle strip at cam.height_m below the
    camera. None when the camera looks too level to see the floor, or the rows do not agree (not
    a floor: a person, a table)."""
    rows, cols = depth.shape
    if not floor_in_view(cam, rows):
        return None
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


class Reader:
    """Reads the ground in each depth image with a **held** scale, learned from frames of level
    floor, not with each frame's own.

    Scaling each frame so its bottom rows lie at the floor would hide an edge in the camera's blind
    zone: standing at a kerb or the top of stairs, the lower floor at the bottom of the picture
    would be scaled up to the person's level and read as flat (Waqas, #138). The model's scale is
    a property of the model on the scene and changes slowly, so it is held:

    - it is learned from `learn` frames in a row whose bottom rows are level floor (floor_plane),
      and then follows such frames slowly; until then there is no reading;
    - each frame is read with the held scale, so the ground reader's own check of the blind zone
      works: nearest ground off the person's level is an edge at 0 m;
    - and when a frame's own floor scale differs from the held one by more than `differ`, the
      bottom of the picture is not where level floor would be: farther is a drop at 0 m, nearer
      (a step up, or feet) is unknown ground, which slows.

    The model's scale can change between scenes (a corridor, then outdoors). If the frames keep
    differing after `relearn_steps` steps, the person has walked past where an edge would be, so
    the held scale is learned again.
    """

    def __init__(self, mount: Mount, learn=3, follow=0.1, differ=0.15, relearn_steps=4):
        self.mount, self.learn, self.follow, self.differ = mount, learn, follow, differ
        self.relearn_steps = relearn_steps
        self.held: float | None = None
        self._learning: list[float] = []
        self._differs_since: int | None = None  # the step count when the frames began to differ

    def read(self, depth: np.ndarray, pitch_rad: float | None, steps: int = 0) -> Seen:
        if pitch_rad is None:
            return Seen(depth, None, None, why="no tilt from the motion sensor")
        cam = self.mount.camera(depth.shape[1], depth.shape[0], pitch_rad)
        own = floor_scale(depth, cam)
        if self.held is None:
            return self._learn(depth, cam, own)
        scaled = depth * self.held
        reading, ahead = dp.read_ground(scaled, cam), nearest_ahead(scaled, cam)
        differs = own is not None and abs(own / self.held - 1.0) > self.differ
        if not differs:
            self._differs_since = None
            # follow only level floor the reader also calls level: the floor past a kerb is a
            # plane at the camera's tilt as well, and following it would walk the held scale down
            # onto it until the kerb read flat (Waqas, #142)
            if own is not None and reading.ground == dp.FLAT and floor_plane(depth, cam):
                self.held += (own - self.held) * self.follow
        else:
            if self._differs_since is None:
                self._differs_since = steps
            elif steps - self._differs_since >= self.relearn_steps:
                self.held, self._learning, self._differs_since = None, [], None
                return self._learn(depth, cam, own)
            if reading.ground == dp.FLAT:  # the reader saw nothing, but the floor is off
                reading = (
                    dp.Reading(dp.DROP, 0.0)  # the bottom of the picture is farther: lower
                    if own < self.held
                    else dp.Reading(dp.UNKNOWN)  # nearer: a step up, or feet in the picture
                )
        return Seen(scaled, self.held, reading, ahead, cam=cam)

    def _learn(self, depth, cam, own) -> Seen:
        if own is None:
            low = not floor_in_view(cam, depth.shape[0])
            why = "tilt the camera down to the floor" if low else "no floor in view"
            return Seen(depth, None, None, why=why)
        if floor_plane(depth, cam):
            self._learning.append(own)
        else:
            self._learning = []  # in a row: one frame of an edge starts over
        if len(self._learning) >= self.learn:
            self.held = float(np.median(self._learning))
        return Seen(depth, None, None, cam=cam, why="learning the scale: point at level floor")


def floor_plane(depth: np.ndarray, cam: dp.Camera, rows_share=0.15, strip=0.3,
                tilt_deg=7.0, residual=0.03) -> bool:  # fmt: skip
    """The bottom of the picture is level floor: a frame the scale can be learned from.

    On a plane, 1/depth is a straight line in the image row: 1/z = A*yn + B, with the plane's tilt
    atan2(B, A), whatever the model's scale. So the bottom rows are level floor when they fit a line
    to within `residual` and the line's tilt is the camera's own (from the phone) to within
    `tilt_deg`. Close steps or a kerb at the bottom fail it: on real phone frames their fitted tilt
    was 28 to 62 degrees for a camera 9 to 21 degrees down (docs/results.md). It does not ask the
    reader whether the whole frame is level, which on real frames it rarely said."""
    rows, cols = depth.shape
    v = np.arange(rows - max(3, int(rows * rows_share)), rows)
    u = slice(int(cols * (0.5 - strip / 2)), int(cols * (0.5 + strip / 2)))
    z = np.median(depth[v, u], axis=1)
    ok = np.isfinite(z) & (z > 0)
    if ok.sum() < 3:
        return False
    yn, inv = (v[ok] - cam.cy) / cam.fy, 1.0 / z[ok]
    a, b = np.polyfit(yn, inv, 1)
    fit = np.sqrt(np.mean((inv - (a * yn + b)) ** 2)) / np.mean(inv)
    tilt = math.atan2(b, a)
    return bool(fit <= residual and abs(math.degrees(tilt - cam.pitch_rad)) <= tilt_deg)


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
