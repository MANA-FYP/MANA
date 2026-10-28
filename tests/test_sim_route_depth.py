"""The 3D camera reader on the simulation's own route and camera, before the Gazebo run.

The ground profile is read from worlds/mana_route.sdf (the top of every box along the route) and the
camera from models/mana_chair/model.sdf (its field of view, size and mount), and the depth frames
are rendered from them with mana_perception.synthetic. So this checks the reader with the camera
and the hazards the Gazebo world has: the ramp, the stairs up and down, the curb and the drop.
"""

import math
import re
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
for package in ("mana_perception", "mana_sim"):
    sys.path.insert(0, str(ROOT / "src" / package))

from mana_sim.frames import SENSORS, SETTLED_CHASSIS_M, depth_mount  # noqa: E402

from mana_perception import depth as dp  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

SIM = ROOT / "src" / "mana_sim"
WORLD = (SIM / "worlds" / "mana_route.sdf").read_text()
MODEL = (SIM / "models" / "mana_chair" / "model.sdf").read_text()


def boxes():
    """(centre x, centre z, pitch, length, height) of every collision box of the route."""
    found = []
    for pose, size in re.findall(
        r"<collision name=\"[^\"]+\"><pose>([^<]+)</pose>\s*<geometry><box><size>([^<]+)</size>",
        WORLD,
    ):
        x, _y, z, _roll, pitch, _yaw = (float(v) for v in pose.split())
        length, _width, height = (float(v) for v in size.split())
        found.append((x, z, pitch, length, height))
    return found


BOXES = boxes()


def route_height(x: np.ndarray) -> np.ndarray:
    """The height of the route's surface at x: the highest box top there, -inf off the route."""
    out = np.full(np.shape(x), -np.inf)
    for cx, cz, pitch, length, height in BOXES:
        # the top face of a box turned by pitch about y (Gazebo: x' = x cos + z sin)
        ends = [
            (cx + dx * math.cos(pitch) + height / 2 * math.sin(pitch),
             cz - dx * math.sin(pitch) + height / 2 * math.cos(pitch))
            for dx in (-length / 2, length / 2)
        ]  # fmt: skip
        (x0, z0), (x1, z1) = sorted(ends)
        inside = (x >= x0) & (x <= x1)
        top = z0 + (z1 - z0) * (x - x0) / (x1 - x0)
        out = np.where(inside, np.maximum(out, top), out)
    return out


def sim_camera() -> dp.Camera:
    block = re.search(r'<sensor name="depth_camera".*?</sensor>', MODEL, re.S).group(0)
    hfov = float(re.search(r"<horizontal_fov>([\d.]+)", block).group(1))
    cols = int(re.search(r"<width>(\d+)", block).group(1))
    rows = int(re.search(r"<height>(\d+)", block).group(1))
    height, pitch = depth_mount()
    return dp.Camera.from_fov(cols, rows, hfov, height, pitch)


CAM = sim_camera()


# Where the camera really is: the chassis settles lower than the rigid geometry (frames.py), and
# differently on wheels and on tracks. Frames are rendered from there and read with the height
# depth_node is told, so a told height drifting too far from the real one fails these tests.
REAL_HEIGHTS = {s: h + SENSORS["depth_link"][2] for s, h in SETTLED_CHASSIS_M.items()}


@pytest.fixture(params=sorted(REAL_HEIGHTS), ids=lambda s: f"on-{s}")
def real(request):
    return REAL_HEIGHTS[request.param]


def read_at(chair_x: float, real_height: float) -> dp.Reading:
    """What the reader says with the chair's 3D camera at chair_x on the route (camera 0.35 m ahead
    of the chassis), the chair standing on the ground under it, the camera really real_height up
    and depth_node told depth_mount()."""
    camera_x = chair_x + 0.35
    floor = route_height(np.array([chair_x]))[0]
    seen_from = replace(CAM, height_m=real_height)
    image = syn.render(lambda a: route_height(camera_x + a) - floor, seen_from, CAM_ROWS, CAM_COLS)
    return dp.read_ground(image, CAM)


CAM_COLS, CAM_ROWS = round(2 * CAM.cx + 1), round(2 * CAM.cy + 1)


def test_the_route_profile_has_the_hazards_the_readme_lists():
    at = route_height(np.array([5.0, 10.0, 13.0, 22.1, 22.5, 22.8, 26.1, 26.5, 27.0, 32.0, 40.0]))
    assert at == pytest.approx(
        [0, 0.25, 0.5, 0.17, 0.34, 0.51, 0.34, 0.17, 0, 0.12, -1.0], abs=0.01
    )


def test_the_sim_camera_sees_flat_ground_on_the_flat_start(real):
    assert read_at(3.0, real).ground == dp.FLAT


def test_the_ramp_is_a_ramp(real):
    assert read_at(8.0 - 0.35 - 1.2, real).ground == dp.RAMP


# the chair still level, both axles off the slope, with the slope already under the camera
@pytest.mark.parametrize("chair_x", [7.5, 7.6, 7.7], ids=lambda x: f"foot-{x}")
def test_at_the_foot_of_the_ramp_the_ramp_is_not_a_curb(chair_x, real):
    assert read_at(chair_x, real).ground == dp.RAMP


@pytest.mark.parametrize("chair_x", [13.5, 13.6, 13.7], ids=lambda x: f"crest-{x}")
def test_at_the_crest_of_the_ramp_the_way_down_is_not_a_curb(chair_x, real):
    assert read_at(chair_x, real).ground == dp.RAMP


@pytest.mark.parametrize("ahead", [1.5, 1.0, 0.6])
def test_the_stairs_up_are_stairs_up_at_the_first_riser(ahead, real):
    r = read_at(22.0 - 0.35 - ahead, real)
    assert r.ground == dp.STAIRS_UP
    assert r.distance_m == pytest.approx(ahead, abs=0.1)
    assert r.step_height_m == pytest.approx(0.17, abs=0.04)


def test_from_the_top_the_two_steps_down_are_stairs_down(real):
    r = read_at(26.0 - 0.35 - 0.9, real)  # on the top step, 0.9 m from its edge
    assert r.ground == dp.STAIRS_DOWN
    assert r.distance_m == pytest.approx(0.9, abs=0.1)


@pytest.mark.parametrize("edge, ahead", [(31.0, 1.0), (34.0, 1.0)])
def test_the_curb_up_and_down_is_a_curb(edge, ahead, real):
    r = read_at(edge - 0.35 - ahead, real)
    assert r.ground == dp.CURB
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


@pytest.mark.parametrize("ahead", [1.5, 0.8, 0.5])
def test_the_drop_is_a_drop_close_enough_for_the_core_to_stop(ahead, real):
    r = read_at(38.0 - 0.35 - ahead, real)
    assert r.ground == dp.DROP
    assert r.distance_m <= ahead + 0.1


def test_the_drop_in_the_blind_zone_is_reported_at_0_m(real):
    blind = dp.nearest_ground(CAM, CAM_ROWS)
    assert blind == pytest.approx(0.53, abs=0.03)
    r = read_at(38.0 - 0.35 - 0.3, real)  # the edge 0.3 m ahead, inside the blind zone
    assert r.ground == dp.DROP and r.distance_m == 0.0
