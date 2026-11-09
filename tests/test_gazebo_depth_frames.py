"""The 3D camera reader on depth frames saved from Gazebo (#39), not rendered by our own code.

tests/fixtures/gazebo_depth.npz (made by tools/grab_depth_frames.py) holds frames of the simulated
3D camera from the `stairs`, `curb` and `drop` runs, saved when the camera was 2.5, 2.0, 1.5 and
1.0 m (within 5 cm) from the first riser, the curb or the edge, and on flat ground, in millimetres
as a 16UC1 camera sends them, with the true distance and the chair's pitch from Gazebo's ground
truth. They are read as `depth_node` reads
them in the Gazebo runs: the mount from mana_sim.frames, the pitch from the IMU.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for package in ("mana_perception", "mana_sim"):
    sys.path.insert(0, str(ROOT / "src" / package))

from mana_sim.frames import depth_mount  # noqa: E402

from mana_perception import depth as dp  # noqa: E402

DATA = np.load(ROOT / "tests" / "fixtures" / "gazebo_depth.npz")
FX, FY, CX, CY = DATA["k"]
CAM = dp.Camera(FX, FY, CX, CY, *depth_mount())


def frames():
    i = 0
    while f"f{i}_name" in DATA:
        kind, _ = str(DATA[f"f{i}_name"]).split(":")
        gap, pitch = DATA[f"f{i}_meta"]
        mm = DATA[f"f{i}_depth_mm"]
        yield pytest.param(kind, float(gap), float(pitch), mm, id=str(DATA[f"f{i}_name"]))
        i += 1


EXPECTED = {
    "stairs_up": (dp.STAIRS_UP, 0.17),
    "curb": (dp.CURB, 0.12),
    "drop": (dp.DROP, None),
    "flat": (dp.FLAT, None),
}


@pytest.mark.parametrize(("kind", "gap", "pitch", "mm"), list(frames()))
def test_a_gazebo_frame_reads_the_hazard_at_its_true_distance(kind, gap, pitch, mm):
    depth = np.where(mm > 0, mm / 1000.0, np.nan)
    r = dp.read_ground(depth, CAM, chair_pitch_rad=pitch)
    ground, height = EXPECTED[kind]
    assert r.ground == ground
    if ground != dp.FLAT:
        assert abs(r.distance_m - gap) <= 0.1
    if height is not None:
        assert abs(r.step_height_m - height) <= 0.03


def test_the_frames_cover_every_hazard_at_every_distance():
    names = [str(DATA[k]) for k in DATA.files if k.endswith("_name")]
    for kind in ("stairs_up", "curb", "drop"):
        assert sorted(n for n in names if n.startswith(kind)) == [
            f"{kind}:{d}" for d in ("1.0", "1.5", "2.0", "2.5")
        ]
    assert any(n.startswith("flat") for n in names)
