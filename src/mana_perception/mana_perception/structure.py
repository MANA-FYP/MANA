"""What blocks the path, from the 3D camera: walls, objects, overhangs and narrow openings.

depth.py reads the ground along the middle of the path. A rise taller than a stair riser it leaves
to the obstacle sensors, and the 3D camera did not report those, so a wall or a low box seen only by
the depth camera never stopped the device. This module reads that from the same depth image. Pure
numpy.

How it works:
1. Every valid pixel becomes a point in a level frame: how far ahead, how far to the left, and how
   high above the ground under the device.
2. Only the corridor the device sweeps counts: its width plus a margin each side, from the floor up
   to the clearance height (2.03 m by default: the ADA headroom over a walkway).
3. The points are put in bins by distance ahead and in cells across the corridor. The floor in
   each bin is a low percentile of its heights, so it follows ramps and steps. It may rise at most
   a riser above the floor nearer in, so something hanging above the floor (a table top, a shelf)
   never becomes the floor.
4. A cell is occupied when enough of its points stand above that floor by more than the depth
   noise there. Stereo depth error grows with the square of the distance, so that height does too:
   about 3 cm out to 1.7 m, 4 cm at 2 m and 9 cm at 3 m.
5. A bin raised by no more than a riser across the path, from one side of the corridor to the
   other (most of its cells, and the outermost ones on both sides), is a step: depth.py reads it as
   a curb or stairs, and it is not an obstacle. Any other occupied cell blocks the path, so a low
   box in the middle of the path is an obstacle however wide it is.

The nearest blocked bin is the obstacle, labelled:
- "wall": every cell across the corridor is blocked
- "narrow opening": blocked on both sides with a gap between narrower than the corridor (a door or
  a gap the device does not fit through)
- "overhang": nothing blocked below 0.685 m, the lowest edge a long cane may not find (ADA); a
  table top or a shelf, under which the floor looks clear
- "obstacle": anything else

What it cannot see: glass, which depth mostly sees through (see docs/coverage.md); a cable thinner
than the depth noise; and anything above the camera's field of view. The planned mount (1.0 m up,
tilted 0.6 rad down) sees nothing higher than itself, so a head-height overhang needs a camera
tilted less or a sensor looking up.
"""

from __future__ import annotations

import math
import warnings
from dataclasses import dataclass

import numpy as np

from mana_perception.depth import Camera, tilted

NAN = math.nan


@dataclass(frozen=True)
class Settings:
    device_width_m: float = 0.7  # a manual wheelchair is about 0.65 m; ADA doors give 0.815 m
    margin_m: float = 0.05  # clearance each side of the device
    clearance_m: float = 2.03  # headroom: nothing higher than this above the floor counts (ADA)
    min_range_m: float = 0.3
    max_range_m: float = 3.0
    body_m: float = 0.0  # the device's own front, ahead of the camera: nearer is the device itself
    # (footrests, the tracks, the user's feet); on the simulated chair the tracks reach 0.75 m
    max_climb: float = 1.0  # steepest ground the floor may follow, rise per metre (45 degrees)
    bin_m: float = 0.1  # along the path
    cell_m: float = 0.1  # across the corridor
    min_points: int = 4  # a cell with fewer points is not seen
    occupied_share: float = 0.15  # of a cell's points above the floor, to block it
    min_height_m: float = 0.03  # the lowest object reported, near
    noise_per_m2: float = 0.01  # and the noise floor growing with distance squared
    floor_share: float = 0.2  # the floor is this percentile of a bin's heights
    riser_max_m: float = 0.25  # as depth.py: a rise up to this across the path is a step
    step_share: float = 0.75  # of the cells seen, blocked by no more than a riser: a step (noise
    # far off leaves a few cells of a riser unblocked)
    edge_check_m: float = 2.0  # nearer than this, a step must raise both edges of the corridor
    spill_m: float = 0.06  # a riser's depth noise reaches this far into the bin before it
    overhang_min_m: float = 0.685  # an object whose lowest edge is above this is an overhang


DEFAULTS = Settings()


@dataclass(frozen=True)
class Obstacle:
    distance_m: float  # ahead, to the nearest blocked bin
    label: str
    height_m: float  # top of what blocks the path, above the floor
    bearing_rad: float = 0.0  # angle from straight ahead to its middle, positive to the left
    opening_m: float = NAN  # free width of a narrow opening


def median3(depth: np.ndarray) -> np.ndarray:
    """Each pixel's depth as the median of its 3 x 3 neighbours that have a reading (NaN with
    none). Single noisy pixels would otherwise stand above the floor; an edge survives, as more
    than half of a pixel's neighbours are on its own side of it."""
    z = np.where(np.isfinite(depth) & (depth > 0), depth, np.nan).astype(np.float64)
    pad = np.pad(z, 1, constant_values=np.nan)
    rows, cols = z.shape
    stack = [pad[i : i + rows, j : j + cols] for i in range(3) for j in range(3)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN neighbourhoods
        return np.nanmedian(np.stack(stack), axis=0)


def points(depth: np.ndarray, cam: Camera, mount: Camera | None = None):
    """Ahead, left and height above the ground of every valid pixel (REP 103: left is positive),
    and how far ahead each is along the device itself, with the camera as mounted: when the device
    pitches nose up, its own parts below the camera swing forward in the level frame."""
    z = median3(depth)
    rows, cols = z.shape
    yn = (np.arange(rows)[:, None] - cam.cy) / cam.fy
    xn = (np.arange(cols)[None, :] - cam.cx) / cam.fx
    ok = np.isfinite(z) & (z > 0)
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    ahead = z * (c - yn * s)
    left = -z * xn
    height = cam.height_m - z * (yn * c + s)
    pitch = (mount or cam).pitch_rad
    own = z * (math.cos(pitch) - yn * math.sin(pitch))
    return ahead[ok], np.broadcast_to(left, z.shape)[ok], height[ok], own[ok]


def min_height(distance_m: float, settings: Settings = DEFAULTS) -> float:
    """The lowest object reported at this distance: above the depth noise there."""
    return max(settings.min_height_m, settings.noise_per_m2 * distance_m**2)


def find(
    depth: np.ndarray, cam: Camera, settings: Settings = DEFAULTS, chair_pitch_rad: float = 0.0
) -> Obstacle | None:
    """The nearest thing blocking the device's corridor in one depth image, or None. cam is the
    camera as mounted; chair_pitch_rad the device's own pitch (REP 103: nose down is positive),
    as depth.read_ground takes it."""
    st = settings
    half = st.device_width_m / 2.0 + st.margin_m
    seen_by = tilted(cam, chair_pitch_rad) if chair_pitch_rad else cam
    ahead, left, height, own = points(depth, seen_by, cam)
    keep = (np.abs(left) <= half) & (ahead >= st.min_range_m) & (ahead < st.max_range_m)
    keep &= own >= st.body_m
    ahead, left, height = ahead[keep], left[keep], height[keep]
    cells = max(1, math.ceil(2 * half / st.cell_m - 1e-9))
    bins = np.floor((ahead - st.min_range_m) / st.bin_m).astype(int)
    across = np.clip(np.floor((left + half) / st.cell_m).astype(int), 0, cells - 1)
    order = np.argsort(bins, kind="stable")
    bins, across = bins[order], across[order]
    ahead, left, height = ahead[order], left[order], height[order]
    starts = np.searchsorted(bins, np.arange(bins.max() + 1 if bins.size else 0))
    ends = np.searchsorted(bins, np.arange(len(starts)), side="right")
    low = [
        float(np.percentile(height[a:b], 100 * st.floor_share)) if b - a >= st.min_points else NAN
        for a, b in zip(starts, ends, strict=True)
    ]
    floor, floor_at = 0.0, 0.0  # the ground under the device, as the camera's mount gives it
    for i, (a, b) in enumerate(zip(starts, ends, strict=True)):
        if b - a < st.min_points:
            continue
        h, cell, y = height[a:b], across[a:b], left[a:b]
        # the floor climbs at most a riser, and stairs as steep as any the device climbs over the
        # ground not seen since (the blind zone in front of the device, or while climbing)
        at = st.min_range_m + i * st.bin_m
        floor = min(low[i], floor + st.riser_max_m + st.max_climb * max(0.0, at - floor_at))
        floor_at = at + st.bin_m
        rise = h - floor
        up = rise > min_height(st.min_range_m + (i + 0.5) * st.bin_m, st)
        up &= rise <= st.clearance_m
        count = np.bincount(cell, minlength=cells)
        raised = np.bincount(cell, weights=up, minlength=cells)
        seen = count >= st.min_points
        blocked = seen & (raised >= st.min_points) & (raised >= st.occupied_share * count)
        if not blocked.any():
            continue
        hit = up & blocked[cell]
        top = float(np.percentile(rise[hit], 95))
        if top <= st.riser_max_m:
            # a riser far off has few points in some cells, but it raises them all; and a riser
            # whose face noise spills into the bin before it raises the floor itself just after
            touched = seen & (raised > 0)
            # a step runs across the whole path, so it raises the corridor's outermost seen cells
            # on both sides; a low box in the middle leaves one of them flat. Checked out to
            # edge_check_m: further off, stereo noise can leave a real riser's edge cell flat, and
            # the bin is checked again on every frame as the device comes nearer
            sides = np.flatnonzero(seen)
            edge = st.min_range_m + (i + 1) * st.bin_m  # where this bin ends
            across_path = edge > st.edge_check_m or bool(touched[sides[0]] and touched[sides[-1]])
            after = max((x for x in low[i + 1 : i + 3] if x == x), default=NAN)
            spill = float(np.percentile(ahead[a:b][hit], 5)) >= edge - st.spill_m
            rises = after - floor >= min_height(edge + st.bin_m, st)
            if across_path and (touched.sum() >= st.step_share * seen.sum() or (spill and rises)):
                continue  # a step across the path: depth.py's to read
        distance = float(np.percentile(ahead[a:b][hit], 5))  # its near face
        bearing = math.atan2(float(np.median(y[hit])), distance)
        if float(np.percentile(rise[hit], 5)) > st.overhang_min_m:
            return Obstacle(distance, "overhang", top, bearing)
        if blocked[seen].all():
            return Obstacle(distance, "wall", top, bearing)
        lefts, rights = hit & (y > 0), hit & (y < 0)
        free = np.flatnonzero(seen & ~blocked)
        if lefts.any() and rights.any() and free.size:
            low_side = np.flatnonzero(blocked & (np.arange(cells) < free[0]))
            high_side = np.flatnonzero(blocked & (np.arange(cells) > free[-1]))
            if low_side.size and high_side.size:
                opening = float(np.percentile(y[lefts], 5) - np.percentile(y[rights], 95))
                return Obstacle(distance, "narrow opening", top, bearing, max(0.0, opening))
        return Obstacle(distance, "obstacle", top, bearing)
    return None
