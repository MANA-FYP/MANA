"""Measuring an indoor position source against the simulation's ground truth (#43). No ROS.

The loop driver steers the chair round a list of waypoints by its ground-truth pose (a stand-in
for a path planner), and logs once a second the ground truth, the map > base_link pose (the
localiser) and the odom > base_link pose (wheel odometry). The map and odom frames both start where
the chair starts, so each is compared with the ground truth taken relative to the start pose.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# the corridor loop round the central block of worlds/mana_indoor.sdf, from the start at (1, 1.375)
INDOOR_LOOP = [(14.5, 1.375), (14.9, 8.6), (1.5, 8.6), (1.5, 1.375), (8.0, 1.375)]

COLUMNS = ["t", "gx", "gy", "gyaw", "mx", "my", "myaw", "ox", "oy", "oyaw"]


def steer(
    pose: tuple[float, float, float],
    target: tuple[float, float],
    top_speed: float = 0.4,
    top_turn: float = 0.6,
) -> tuple[float, float]:
    """Forward speed (m/s) and turn rate (rad/s) towards target: turn on the spot while the target
    is more than about 30 degrees off the heading, then drive, slowing in the last metre."""
    x, y, yaw = pose
    want = math.atan2(target[1] - y, target[0] - x)
    err = math.atan2(math.sin(want - yaw), math.cos(want - yaw))
    turn = max(-top_turn, min(top_turn, 1.5 * err))
    d = math.hypot(target[0] - x, target[1] - y)
    return (0.0 if abs(err) > 0.5 else min(top_speed, 0.5 * d)), turn


def in_start_frame(x: float, y: float, start: tuple[float, float, float]) -> tuple[float, float]:
    """A ground-truth position in the frame that begins at the start pose, as map and odom do."""
    dx, dy = x - start[0], y - start[1]
    c, s = math.cos(-start[2]), math.sin(-start[2])
    return dx * c - dy * s, dx * s + dy * c


@dataclass
class Errors:
    poses: int
    mean_m: float
    p95_m: float
    max_m: float
    last_m: float


def errors(rows: list[dict], x: str, y: str) -> Errors | None:
    """The distance between a logged pose (columns x and y) and the ground truth, over the rows
    that have that pose; None when none has it (the frame never appeared)."""
    if not rows:
        return None
    start = tuple(float(rows[0][k]) for k in ("gx", "gy", "gyaw"))
    e = []
    for r in rows:
        if math.isnan(float(r[x])):
            continue
        gx, gy = in_start_frame(float(r["gx"]), float(r["gy"]), start)
        e.append(math.hypot(float(r[x]) - gx, float(r[y]) - gy))
    if not e:
        return None
    s = sorted(e)
    return Errors(len(e), sum(e) / len(e), s[min(len(s) - 1, int(0.95 * len(s)))], s[-1], e[-1])


def driven_m(rows: list[dict]) -> float:
    """The length of the ground-truth path, from the logged positions."""
    pts = [(float(r["gx"]), float(r["gy"])) for r in rows]
    return sum(math.dist(a, b) for a, b in zip(pts, pts[1:], strict=False))
