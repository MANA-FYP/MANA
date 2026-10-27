"""Ground ahead from the 3D camera (M1 Vision): flat, ramp, curb, stairs or a drop. Pure numpy.

The 3D camera is the second ground source the decision core needs: a step is confirmed only when
two different sensors see it, and the camera image is one. This module turns one depth image into
one ground reading, with the distance to where the ground changes and the height of the step.

How it works:
1. Every valid pixel in a strip down the middle of the image is turned into a point, then rotated
   by the camera's downward tilt into a level frame: how far ahead it is, and how high above the
   ground under the chair.
2. The points are put in bins by distance ahead, and the median height of each bin gives a
   profile of the ground along the path.
3. The profile is read from near to far. A short, fast change in height is a step (a riser seen
   from the front, or a lower level seen past an edge); a rise taller than any riser is a wall or
   an object, which the obstacle sensors handle. A stretch of ground the camera should see but
   does not, with nothing after it, is a drop. A steady slope with no step is a ramp.

Depth follows REP 118: metres along the optical axis, with NaN, inf or 0 where there is no reading.
The camera cannot see the ground right in front of the chair (its blind zone, about 0.5 m with the
default mount). If the nearest ground it does see is lower or higher than the ground the chair
stands on, the edge is somewhere in that zone, and it is reported at 0 m, the worst case. That
comparison uses the mounting height, so it must be right to within about 5 cm, and the tilt to
within about 2 degrees (2 degrees of error also looks like a slope of 3.5 %).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Codes match the constants in mana_msgs/GroundReport (checked in tests/test_depth_ground.py).
DEPTH_CAMERA = 1
FLAT = 0
RAMP = 1
STAIRS_UP = 2
STAIRS_DOWN = 3
CURB = 4
DROP = 5
UNKNOWN = 255

NAN = math.nan


@dataclass(frozen=True)
class Camera:
    """A pinhole depth camera and where it is mounted."""

    fx: float  # focal lengths and principal point, in pixels
    fy: float
    cx: float
    cy: float
    height_m: float  # optical centre above the ground
    pitch_rad: float  # tilt below level, positive looking down

    @classmethod
    def from_fov(cls, width, height, hfov_rad, height_m, pitch_rad) -> Camera:
        """A camera from its horizontal field of view, with square pixels (as Gazebo makes it)."""
        f = (width / 2.0) / math.tan(hfov_rad / 2.0)
        return cls(f, f, (width - 1) / 2.0, (height - 1) / 2.0, height_m, pitch_rad)


@dataclass(frozen=True)
class Settings:
    min_range_m: float = 0.3  # closer than this the sensor gives no reading
    max_range_m: float = 3.0  # farther than this the depth is too noisy to read steps
    bin_m: float = 0.05  # profile resolution along the path
    strip: float = 0.3  # share of the image width, in the middle, that is the path
    min_points: int = 8  # fewer points than this and a bin has no height
    near_m: float = 0.1  # the nearest stretch of ground seen sets height zero; short, so it ends
    # before an edge just past the blind zone
    near_max_m: float = 1.2  # level ground seen only beyond this is missing data: UNKNOWN
    blind_m: float = 0.06  # nearest ground this far off the chair's level: an edge out of view
    step_m: float = 0.04  # a height change this big between bins can be a step
    steep: float = 1.0  # a riser climbs at least half this per metre, a steep ramp a quarter
    riser_bins: int = 4  # a step's height change spans at most this many bins
    curb_max_m: float = 0.2  # a single step up to this height is a curb
    riser_max_m: float = 0.25  # a rise taller than this is a wall or an object, not a step
    hole_m: float = 0.3  # this much missing ground the camera should see is a drop
    ramp_slope: float = 0.05  # a slope above this (about 3 degrees) is a ramp
    ramp_fit_m: float = 0.015  # how closely the ground must follow the ramp's line
    ramp_bins: int = 6  # a ramp must be seen over at least this many bins
    ramp_rise_m: float = 0.02  # and climb or fall at least this much where it is seen
    stairs_m: float = 1.0  # look this far past the first step for more steps


DEFAULTS = Settings()


@dataclass(frozen=True)
class Reading:
    ground: int
    distance_m: float = NAN  # to where the ground changes; NaN when it does not change
    step_height_m: float = NAN  # of the step or drop; NaN when not measured


@dataclass(frozen=True)
class Profile:
    distance_m: np.ndarray  # bin centres
    height_m: np.ndarray  # median height in each bin, NaN when the bin has too few points
    visible_until_m: float  # how far ahead the camera can see the ground at all
    nearest_m: float = 0.0  # the nearest flat ground in view: closer is the camera's blind zone
    far_points: int = 0  # valid points beyond max_range_m: the image is not empty


def ground_points(depth: np.ndarray, cam: Camera, strip: float = 0.3):
    """Distance ahead and height above the ground of every valid pixel in the middle strip."""
    depth = np.asarray(depth, dtype=np.float64)
    rows, cols = depth.shape
    half = max(1, int(round(cols * strip / 2.0)))
    mid = cols // 2
    z = depth[:, max(0, mid - half) : mid + half]
    v = np.arange(rows, dtype=np.float64)[:, None]
    yn = (v - cam.cy) / cam.fy  # image rows go down
    ok = np.isfinite(z) & (z > 0)
    z, yn = z[ok], np.broadcast_to(yn, ok.shape)[ok]
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    ahead = z * (c - yn * s)
    below = z * (yn * c + s)
    return ahead, cam.height_m - below


def visible_until(cam: Camera, rows: int, max_range_m: float) -> float:
    """How far ahead the top row of the image still meets flat ground."""
    top = math.atan((0 - cam.cy) / cam.fy)  # negative: above the optical axis
    down = cam.pitch_rad + top  # angle below level of the top row
    if down <= 0:
        return max_range_m
    return min(max_range_m, cam.height_m / math.tan(down))


def nearest_ground(cam: Camera, rows: int) -> float:
    """How far ahead the bottom row of the image meets flat ground: closer is not seen at all."""
    down = cam.pitch_rad + math.atan((rows - 1 - cam.cy) / cam.fy)
    return cam.height_m / math.tan(down) if down > 0 else math.inf


def profile(depth: np.ndarray, cam: Camera, settings: Settings = DEFAULTS) -> Profile:
    """The median ground height along the path, in bins of settings.bin_m."""
    ahead, height = ground_points(depth, cam, settings.strip)
    edges = np.arange(settings.min_range_m, settings.max_range_m + 1e-9, settings.bin_m)
    centres = (edges[:-1] + edges[1:]) / 2.0
    heights = np.full(centres.shape, np.nan)
    index = np.digitize(ahead, edges) - 1
    keep = (index >= 0) & (index < len(centres))
    index, height = index[keep], height[keep]
    order = np.argsort(index, kind="stable")
    index, height = index[order], height[order]
    starts = np.searchsorted(index, np.arange(len(centres)))
    ends = np.searchsorted(index, np.arange(len(centres)), side="right")
    for i, (a, b) in enumerate(zip(starts, ends, strict=True)):
        if b - a >= settings.min_points:
            heights[i] = np.median(height[a:b])
    rows = depth.shape[0]
    return Profile(
        centres,
        heights,
        visible_until(cam, rows, settings.max_range_m),
        nearest_ground(cam, rows),
        int(np.count_nonzero(ahead >= settings.max_range_m)),
    )


def _steps(d, h, settings):
    """Sharp height changes along the profile: (edge distance, change) pairs, nearest first.

    Two kinds of change are steps:
    - A short run of bins where the height climbs or falls fast in one direction. Depth noise
      blurs a riser over a few bins, so the run is judged by its average slope; a ramp, even a
      steep one, climbs more slowly and for longer.
    - A lower level seen past a stretch the camera could not see: the ground hidden behind the
      edge of a drop or of a stair tread.
    """
    dh, dd = np.diff(h), np.diff(d)
    hidden = dd > 1.5 * settings.bin_m  # ground the camera could not see between two bins
    edge = hidden & (dh <= -settings.step_m)
    fast = ~hidden & (np.abs(dh) >= settings.step_m / 4) & (np.abs(dh) / dd >= settings.steep / 4)
    found = []
    i = 0
    while i < len(dh):
        if edge[i]:
            a = b = i
        elif fast[i]:
            a = b = i  # grow the run while the height keeps changing fast the same way
            while b + 1 < len(dh) and fast[b + 1] and dh[b + 1] * dh[i] > 0:
                b += 1
            span = d[b + 1] - d[a]
            if b - a >= settings.riser_bins or abs(h[b + 1] - h[a]) / span < settings.steep / 2:
                i = b + 1
                continue
        else:
            i += 1
            continue
        # the height between the level ground on either side, not the sum of noisy jumps
        reach = 3 * settings.bin_m
        before = h[(d <= d[a]) & (d >= d[a] - reach)]
        after = h[(d >= d[b + 1]) & (d <= d[b + 1] + reach)]
        total = float(np.median(after) - np.median(before))
        if abs(total) >= settings.step_m:
            found.append((d[a] + settings.bin_m / 2.0, total))
        i = b + 1
    return found


def _ramp(d, h, settings):
    """Where a slope starts and its gradient, from the best flat-then-sloped fit, or None."""
    if len(d) < settings.ramp_bins + 2:
        return None
    best = None
    for k in range(2, len(d) - settings.ramp_bins + 1):
        b = d[k]
        rest = np.clip(d - b, 0.0, None)
        x = np.column_stack([np.ones_like(d), rest])
        coef, *_ = np.linalg.lstsq(x, h, rcond=None)
        err = float(np.sqrt(np.mean((x @ coef - h) ** 2)))
        if best is None or err < best[0]:
            best = (err, b, float(coef[1]))
    err, start, slope = best
    if err > settings.ramp_fit_m or abs(slope) < settings.ramp_slope:
        return None
    if abs(slope) * (d[-1] - start) < settings.ramp_rise_m:
        return None
    return start, slope


def classify(prof: Profile, settings: Settings = DEFAULTS) -> Reading:
    """One ground reading from a profile: the nearest change in the ground ahead."""
    have = ~np.isnan(prof.height_m)
    d, h = prof.distance_m[have], prof.height_m[have]
    if not d.size:
        if prof.far_points >= settings.min_points:
            return Reading(DROP, 0.0)  # the camera sees only far below or beyond: a ledge
        return Reading(UNKNOWN)  # no reading at all: nothing to judge
    # the nearest ground seen, up to where its height starts to change, so that an edge just past
    # the blind zone is not averaged into it (each bin is already a median of many points)
    near = d < d[0] + settings.near_m
    changes = np.flatnonzero(np.abs(np.diff(h)) >= settings.step_m / 2)
    if changes.size:
        near[changes[0] + 1 :] = False
    level = float(np.median(h[near]))  # height of the nearest ground seen, against the chair's
    if level > settings.riser_max_m:
        # taller than a riser: stairs if more risers climb behind it, else a wall or an object
        climbs = [dh for _, dh in _steps(d, h - level, settings) if dh > 0]
        return Reading(STAIRS_UP, 0.0) if climbs else Reading(UNKNOWN)

    # The camera cannot see the ground just in front of the chair. If the nearest ground it does
    # see is clearly lower or higher than the ground the chair stands on, the edge is in that
    # blind zone. Where in it is unknown, so it is reported at 0 m: the worst case.
    blind = []
    if abs(level) > settings.blind_m:
        blind = [(0.0, level)]
    elif d[0] > settings.near_max_m:
        return Reading(UNKNOWN)  # level ground only far away: the near ground is missing data
    h = h - level  # heights from the nearest ground seen
    visible = prof.visible_until_m

    # a rise taller than any stair riser is a wall or an object: the ground ends there for us
    for edge, dh in _steps(d, h, settings):
        if dh > settings.riser_max_m:
            keep = d < edge
            d, h, visible = d[keep], h[keep], edge
            break

    # ground the camera should see but does not
    hole = None
    seen_to = d[-1]
    gaps = np.diff(d)
    for i, gap in enumerate(gaps):
        if gap - settings.bin_m >= settings.hole_m:
            hole = (d[i] + settings.bin_m / 2.0, i)
            break
    if hole is None and visible - seen_to >= settings.hole_m:
        hole = (seen_to + settings.bin_m / 2.0, len(d) - 1)

    steps = _steps(d, h, settings)
    if blind:  # the edge in the blind zone comes first; a hole past it is not the nearest change
        steps, hole = blind + steps, None
    if hole is not None and not any(edge < hole[0] for edge, _ in steps):
        edge, i = hole
        after = h[i + 1 :]
        if after.size == 0:  # nothing seen past the edge: the floor is out of sight
            return Reading(DROP, edge)
        lower = float(np.median(after[: max(1, settings.min_points // 4)]))
        if lower <= -settings.step_m:  # otherwise the ground comes back level: just missing data
            later = [s for s in steps if s[0] > edge + 1e-9]  # the jump across the hole is this one
            steps = [(edge, lower - float(h[i]))] + later
    if steps:
        return _read_steps(steps, settings)
    ramp = _ramp(d, h, settings)
    if ramp is not None:
        return Reading(RAMP, ramp[0])
    return Reading(FLAT)


def _read_steps(steps, settings) -> Reading:
    first_edge, first = steps[0]
    near = [dh for edge, dh in steps if edge - first_edge <= settings.stairs_m]
    same_way = [dh for dh in near if dh * first > 0]
    height = abs(first)
    if first > 0:
        if len(same_way) == 1 and height <= settings.curb_max_m:
            return Reading(CURB, first_edge, height)
        return Reading(STAIRS_UP, first_edge, height)
    if len(same_way) >= 2:
        return Reading(STAIRS_DOWN, first_edge, height)
    if height <= settings.curb_max_m:
        return Reading(CURB, first_edge, height)
    return Reading(DROP, first_edge, height)


def to_metres(encoding: str, rows: int, cols: int, step: int, data, big_endian=False):
    """A depth image's bytes as metres: 32FC1 is already metres, 16UC1 is millimetres.

    None for any other encoding. Readings of 0 in 16UC1 mean no reading and become NaN.
    """
    kinds = {"32FC1": "f4", "16UC1": "u2"}
    if encoding not in kinds:
        return None
    dtype = np.dtype(kinds[encoding]).newbyteorder(">" if big_endian else "<")
    raw = np.frombuffer(bytes(data), dtype=dtype, count=rows * step // dtype.itemsize)
    image = raw.reshape(rows, step // dtype.itemsize)[:, :cols].astype(np.float64)
    if encoding == "16UC1":
        image[image == 0] = np.nan
        image /= 1000.0
    return image


def read_ground(depth: np.ndarray, cam: Camera, settings: Settings = DEFAULTS) -> Reading:
    """The ground ahead in one depth image."""
    return classify(profile(depth, cam, settings), settings)
