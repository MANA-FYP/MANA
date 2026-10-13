"""Scripted perception for the stub (M1 Vision). Pure Python, no ROS.

The stub stands in for the real vision models. It reports what the camera, the 3D camera and the
lidar would see in a few fixed situations, so the decision core can be run and tested before the
models exist. The real models will publish the same messages, so nothing downstream changes.
"""

import math
from dataclasses import dataclass

# Codes match the constants in mana_msgs; tests/test_perception_stub.py checks they stay in step.
CAMERA = 0
DEPTH_CAMERA = 1
LIDAR = 2

FLAT = 0
RAMP = 1
STAIRS_UP = 2
STAIRS_DOWN = 3
CURB = 4
DROP = 5
UNKNOWN = 255

NAN = math.nan


@dataclass(frozen=True)
class Ground:
    source: int
    ground: int
    distance_m: float = NAN  # to where the ground changes; NaN when it does not change
    step_height_m: float = NAN  # NaN when not measured


@dataclass(frozen=True)
class Obstacle:
    source: int
    distance_m: float
    label: str = ""
    bearing_rad: float = 0.0  # positive to the left


# What each source reports in each situation.
SCENARIOS = {
    "level": (
        [Ground(CAMERA, FLAT), Ground(DEPTH_CAMERA, FLAT)],
        [],
    ),
    "ramp": (
        [Ground(CAMERA, RAMP, 1.5), Ground(DEPTH_CAMERA, RAMP, 1.5)],
        [],
    ),
    # both sources see the stairs, so the decision core may change to tracks
    "stairs": (
        [Ground(CAMERA, STAIRS_UP, 1.5), Ground(DEPTH_CAMERA, STAIRS_UP, 1.5, 0.17)],
        [],
    ),
    "curb": (
        [Ground(CAMERA, CURB, 1.0), Ground(DEPTH_CAMERA, CURB, 1.0, 0.12)],
        [],
    ),
    # only the 3D camera sees the drop: a failure case for the camera
    "drop": (
        [Ground(CAMERA, FLAT), Ground(DEPTH_CAMERA, DROP, 0.4, 0.30)],
        [],
    ),
    "obstacle": (
        [Ground(CAMERA, FLAT), Ground(DEPTH_CAMERA, FLAT)],
        [Obstacle(LIDAR, 0.7, "person")],
    ),
    # the sources disagree, so the stairs must not be confirmed
    "conflict": (
        [Ground(CAMERA, STAIRS_UP, 1.5), Ground(DEPTH_CAMERA, FLAT)],
        [],
    ),
}

# The order in which the "cycle" scenario plays them, with level ground in between.
CYCLE = [
    "level",
    "ramp",
    "level",
    "stairs",
    "level",
    "curb",
    "level",
    "obstacle",
    "level",
    "drop",
    "level",
    "conflict",
]


def reports(name: str) -> tuple[list[Ground], list[Obstacle]]:
    """The ground and obstacle reports for one scenario."""
    if name not in SCENARIOS:
        raise ValueError(f"unknown scenario {name!r}; choose one of {sorted(SCENARIOS)} or 'cycle'")
    ground, obstacles = SCENARIOS[name]
    return list(ground), list(obstacles)


def scenario_at(seconds: float, hold_s: float = 5.0) -> str:
    """Which scenario the cycle is in after this many seconds; it wraps around at the end."""
    return CYCLE[int(max(seconds, 0.0) // hold_s) % len(CYCLE)]
