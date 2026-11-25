"""Tests for what blocks the path in a 3D camera frame (mana_perception.structure). Pure numpy.

The frames are rendered by mana_perception.synthetic.render_boxes: a flat floor with boxes on or
above it (walls, door jambs, a person, a bag, a table top, a sign), or by render() for ground that
is the same across the path (ramps, steps, stairs). The camera is the one planned for the chair
(1.0 m up, tilted 0.6 rad down). Each scene is read clean and with depth noise as stereo cameras
have it: about 0.5 % of the depth at 1 m, growing with the square of the depth, and a tenth of the
pixels with no reading.
"""

import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception import depth as dp  # noqa: E402
from mana_perception import structure as st  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

W = 3.0  # half the width of a wall across the path
LESS_TILTED = dp.Camera.from_fov(syn.COLS, syn.ROWS, math.radians(87.0), 1.0, 0.3)


def stereo_noise(image, rng, at_1m=0.005, missing=0.1):
    """Depth noise growing with the square of the depth, as a stereo camera's does."""
    seen = np.where(np.isfinite(image), image, 0.0)
    out = image * (1.0 + rng.normal(0.0, 1.0, image.shape) * at_1m * seen)
    out[rng.random(image.shape) < missing] = np.nan
    return out


def frames(boxes, cam=syn.CAMERA, seeds=10):
    clean = syn.render_boxes(boxes, cam)
    rng = np.random.default_rng(7)
    return [clean] + [stereo_noise(clean, rng) for _ in range(seeds)]


BLOCKED = {  # name: (boxes, camera, label, near face ahead)
    "wall": ([(2.5, 2.6, -W, W, 0.0, 2.5)], syn.CAMERA, "wall", 2.5),
    "person": ([(1.8, 2.1, -0.25, 0.25, 0.0, 1.75)], syn.CAMERA, "obstacle", 1.8),
    "box to the right": ([(1.2, 1.5, -0.35, -0.05, 0.0, 0.3)], syn.CAMERA, "obstacle", 1.2),
    "bag 10 cm": ([(1.5, 1.8, 0.0, 0.3, 0.0, 0.1)], syn.CAMERA, "obstacle", 1.5),
    "box 5 cm near": ([(1.0, 1.3, -0.2, 0.2, 0.0, 0.05)], syn.CAMERA, "obstacle", 1.0),
    "door 0.6 m": (
        [(1.5, 1.6, -W, -0.3, 0.0, 2.5), (1.5, 1.6, 0.3, W, 0.0, 2.5)],
        syn.CAMERA,
        "narrow opening",
        1.5,
    ),
    "table top": ([(1.2, 2.0, -0.6, 0.6, 0.72, 0.75)], syn.CAMERA, "overhang", 1.2),
    "sign at 1.2 m": ([(1.5, 1.6, -0.5, 0.5, 1.2, 1.45)], LESS_TILTED, "overhang", 1.5),
    "bag before a step": (
        [(1.3, 1.45, -0.1, 0.2, 0.0, 0.1), (1.5, 9.0, -W, W, 0.0, 0.15)],
        syn.CAMERA,
        "obstacle",
        1.3,
    ),
}


@pytest.mark.parametrize("name", BLOCKED)
def test_what_blocks_the_path_is_found_and_named(name):
    boxes, cam, label, face = BLOCKED[name]
    for image in frames(boxes, cam):
        found = st.find(image, cam)
        assert found is not None, name
        assert found.label == label, (name, found)
        assert face - 0.1 <= found.distance_m <= face + 0.05, (name, found)


def test_a_narrow_opening_gives_its_width():
    boxes = [(1.5, 1.6, -W, -0.3, 0.0, 2.5), (1.5, 1.6, 0.3, W, 0.0, 2.5)]
    for image in frames(boxes):
        assert st.find(image, syn.CAMERA).opening_m == pytest.approx(0.6, abs=0.05)


def test_the_bearing_points_to_the_side_it_is_on():
    found = st.find(syn.render_boxes([(1.2, 1.5, -0.35, -0.05, 0.0, 0.3)]), syn.CAMERA)
    assert found.bearing_rad < 0  # to the right (REP 103)
    found = st.find(syn.render_boxes([(1.2, 1.5, 0.05, 0.35, 0.0, 0.3)]), syn.CAMERA)
    assert found.bearing_rad > 0


CLEAR = {
    "nothing": [],
    "door 0.9 m, wider than the device": [
        (1.5, 1.6, -W, -0.45, 0.0, 2.5),
        (1.5, 1.6, 0.45, W, 0.0, 2.5),
    ],
    "wall beside the path": [(0.5, 3.0, 0.6, 0.8, 0.0, 2.5)],
    "curb 12 cm": [(1.0, 9.0, -W, W, 0.0, 0.12)],
    "stairs up": [(1.2 + 0.28 * k, 9.0, -W, W, 0.0, 0.17 * (k + 1)) for k in range(6)],
}


@pytest.mark.parametrize("name", CLEAR)
def test_steps_and_the_space_beside_the_path_are_not_obstacles(name):
    """A step across the whole path is the ground reader's (depth.py): a curb or stairs."""
    for image in frames(CLEAR[name]):
        assert st.find(image, syn.CAMERA) is None, name


def test_a_ceiling_above_the_headroom_is_not_an_obstacle():
    for image in frames([(0.0, 9.0, -W, W, 2.4, 2.5)], LESS_TILTED):
        assert st.find(image, LESS_TILTED) is None


GROUND = {
    "ramp 1:12": syn.ramp(1.0, 1 / 12),
    "stairs down": syn.stairs(1.2, -0.17),
    "step down": syn.step(1.0, -0.1),
}


@pytest.mark.parametrize("name", GROUND)
def test_ramps_and_steps_down_are_not_obstacles(name):
    clean = syn.render(GROUND[name])
    rng = np.random.default_rng(3)
    for image in [clean] + [stereo_noise(clean, rng) for _ in range(10)]:
        assert st.find(image, syn.CAMERA) is None, name


def test_the_planned_mount_cannot_see_above_its_own_height():
    """A known limit, in docs/coverage.md: tilted 0.6 rad down from 1.0 m, the top of the image
    is level, so a sign at head height is out of view and needs another sensor."""
    sign = syn.render_boxes([(1.5, 1.6, -0.5, 0.5, 1.6, 1.9)])
    assert st.find(sign, syn.CAMERA) is None


def test_the_lowest_object_reported_grows_with_distance():
    assert st.min_height(1.0) == pytest.approx(0.03)
    assert st.min_height(2.0) == pytest.approx(0.04)
    assert st.min_height(3.0) == pytest.approx(0.09)


def test_boxes_render_as_the_floor_where_there_are_none():
    assert np.allclose(syn.render_boxes([]), syn.render(syn.flat), atol=0.01)


def test_frames_with_no_reading_give_nothing():
    assert st.find(np.full((syn.ROWS, syn.COLS), np.nan), syn.CAMERA) is None


def test_gazebo_frames_show_the_route_clear_and_not_the_chair_itself():
    """The frames saved from the Gazebo runs (tests/fixtures/gazebo_depth.npz): curbs, drops,
    stairs, flat ground and climbing the stairs nose up to 39 degrees. None has anything in the
    path, but the chair's own tracks are in view, 0.75 m ahead of the camera along the chair
    (front_tracks_m less depth_camera_m in mana_sim/config/core.yaml); nose up they swing further
    ahead in the level frame."""
    sys.path.insert(0, str(ROOT / "src" / "mana_sim"))
    from mana_sim.frames import depth_mount

    data = np.load(ROOT / "tests" / "fixtures" / "gazebo_depth.npz")
    cam = dp.Camera(*data["k"], *depth_mount())
    settings = st.Settings(body_m=0.75)
    i = 0
    while f"f{i}_name" in data:
        _gap, pitch = data[f"f{i}_meta"]
        mm = data[f"f{i}_depth_mm"]
        image = np.where(mm > 0, mm / 1000.0, np.nan)
        assert st.find(image, cam, settings, float(pitch)) is None, str(data[f"f{i}_name"])
        if abs(pitch) < 0.01:  # level, the tracks are in view: without the mask, an obstacle
            assert st.find(image, cam) is not None
        i += 1


def test_the_simulated_chair_tells_the_depth_node_where_its_tracks_end():
    core = (ROOT / "src" / "mana_sim" / "config" / "core.yaml").read_text()
    launch = (ROOT / "src" / "mana_sim" / "launch" / "world.launch.py").read_text()

    def value(name, text):
        return float(re.search(rf'"?{name}"?:\s*([0-9.]+)', text).group(1))

    tracks = value("front_tracks_m", core) - value("depth_camera_m", core)
    assert value("body_m", launch) == pytest.approx(tracks)


@pytest.mark.parametrize("width", [0.45, 0.5, 0.6])
@pytest.mark.parametrize("height", [0.1, 0.2])
def test_a_low_box_in_the_middle_of_the_path_is_an_obstacle_not_a_step(width, height):
    """A low box narrower than the path raises most of the corridor's cells but leaves its edges
    flat; it was taken for a step across the path, which the core does not stop for on tracks."""
    boxes = [(1.5, 1.8, -width / 2, width / 2, 0.0, height)]
    for body in (0.0, 0.75):
        for image in frames(boxes, seeds=5):
            found = st.find(image, syn.CAMERA, st.Settings(body_m=body))
            assert found is not None and found.label == "obstacle", (width, height, body)


CHAIR = st.Settings(body_m=0.75, device_width_m=0.8)  # as world.launch.py sets the node


@pytest.mark.parametrize("width", [0.82, 0.85])
def test_a_door_too_narrow_for_the_chair_is_a_narrow_opening(width):
    boxes = [(1.5, 1.6, -W, -width / 2, 0.0, 2.5), (1.5, 1.6, width / 2, W, 0.0, 2.5)]
    for image in frames(boxes, seeds=3):
        found = st.find(image, syn.CAMERA, CHAIR)
        assert found is not None and found.label == "narrow opening", (width, found)


def test_a_door_wide_enough_for_the_chair_is_clear():
    boxes = [(1.5, 1.6, -W, -0.5, 0.0, 2.5), (1.5, 1.6, 0.5, W, 0.0, 2.5)]
    for image in frames(boxes, seeds=3):
        assert st.find(image, syn.CAMERA, CHAIR) is None


def test_the_simulated_chair_tells_the_depth_node_its_width():
    launch = (ROOT / "src" / "mana_sim" / "launch" / "world.launch.py").read_text()
    assert re.search(r'"device_width_m":\s*0\.8\b', launch)


STAIRS_UP = [(1.2 + 0.28 * k, 9.0, -W, W, 0.0, 0.17 * (k + 1)) for k in range(6)]


@pytest.mark.parametrize("yaw", [-8.0, -5.0, -3.0, 3.0, 5.0, 8.0])
def test_stairs_met_at_an_angle_are_not_an_obstacle(yaw):
    """Turned a few degrees off the stairs' axis, each riser crosses the path diagonally, so one
    bin sees only part of it: the chair on tracks stopped halfway up the campus flight (Gazebo)."""
    clean = syn.render_boxes(STAIRS_UP, yaw_rad=math.radians(yaw))
    rng = np.random.default_rng(5)
    for image in [clean] + [stereo_noise(clean, rng) for _ in range(5)]:
        for s in (st.DEFAULTS, CHAIR):
            assert st.find(image, syn.CAMERA, s) is None, (yaw, st.find(image, syn.CAMERA, s))


@pytest.mark.parametrize("roll", [-4.0, -2.0, 2.0, 4.0])
def test_level_floor_seen_by_a_leaning_chair_is_level(roll):
    """At the top of the flight the tracks sat 2 degrees unevenly; the level floor then rose 4 cm
    across the path and was an obstacle. With the chair's roll it is level again."""
    r = math.radians(roll)
    clean = syn.render_boxes([], roll_rad=r)
    rng = np.random.default_rng(6)
    for image in [clean] + [stereo_noise(clean, rng) for _ in range(5)]:
        assert st.find(image, syn.CAMERA, CHAIR, chair_roll_rad=r) is None, roll


@pytest.mark.parametrize("yaw, roll", [(5.0, 0.0), (0.0, 3.0), (-5.0, -3.0)])
def test_a_box_is_still_found_by_a_turned_or_leaning_chair(yaw, roll):
    r = math.radians(roll)
    for box in [(1.5, 1.8, -0.2, 0.2, 0.0, 0.1), (1.3, 1.45, -0.1, 0.2, 0.0, 0.1)]:
        boxes = [box] + ([(1.5, 9.0, -W, W, 0.0, 0.15)] if box[0] < 1.5 else [])
        image = syn.render_boxes(boxes, yaw_rad=math.radians(yaw), roll_rad=r)
        found = st.find(image, syn.CAMERA, chair_roll_rad=r)
        assert found is not None and found.label == "obstacle", (yaw, roll, box, found)


def test_the_way_is_read_in_three_corridors_as_wide_as_the_chair():
    """Which way is open (WayReport): a box ahead at 1.2 m, a wall 1.0 m off to the right, the left
    clear. Each corridor gives the distance to what blocks it, inf when nothing does."""
    boxes = [(1.2, 1.5, -0.3, 0.3, 0.0, 0.5), (1.0, 4.0, -2.0, -0.5, 0.0, 2.0)]
    for image in frames(boxes, seeds=3):
        left, ahead, right = st.way(image, syn.CAMERA, CHAIR)
        assert left == math.inf, left
        assert ahead == pytest.approx(1.2, abs=0.1)
        assert right < 1.6, right
    left, ahead, right = st.way(syn.render_boxes([]), syn.CAMERA, CHAIR)
    assert (left, ahead, right) == (math.inf, math.inf, math.inf)


def test_a_corridor_not_seen_is_not_called_open():
    blank = np.full((syn.ROWS, syn.COLS), np.nan)
    assert all(math.isnan(d) for d in st.way(blank, syn.CAMERA, CHAIR))
    narrow = dp.Camera.from_fov(syn.COLS, syn.ROWS, math.radians(30.0), 1.0, 0.6)
    image = syn.render_boxes([], narrow)
    left, ahead, right = st.way(image, narrow, CHAIR)
    assert math.isnan(left) and math.isnan(right) and ahead == math.inf


def side_drop(cam=syn.CAMERA, edge_left=0.45, depth_m=1.0):
    """A level floor whose left part, from edge_left to the left, is depth_m lower: a platform or
    stairwell edge running beside the path."""
    v = np.arange(syn.ROWS, dtype=np.float64)[:, None]
    u = np.arange(syn.COLS, dtype=np.float64)[None, :]
    yn, xn = (v - cam.cy) / cam.fy, (u - cam.cx) / cam.fx
    c, s = math.cos(cam.pitch_rad), math.sin(cam.pitch_rad)
    b, d = np.broadcast_to(-xn, (syn.ROWS, syn.COLS)), np.broadcast_to(yn * c + s, (syn.ROWS, syn.COLS))
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(d > 0, cam.height_m / d, np.inf)
        t = np.where(t * b > edge_left, (cam.height_m + depth_m) / d, t)
    return np.where(t < 8.0, t, np.inf)


def test_a_drop_beside_the_path_is_never_called_open():
    """Telling the user "Open to your left" towards a platform edge would walk them off it."""
    for image in [side_drop()] + [stereo_noise(side_drop(), np.random.default_rng(k)) for k in range(3)]:
        left, ahead, right = st.way(image, syn.CAMERA, CHAIR)
        assert math.isfinite(left) and left < 2.5, left
        assert ahead == math.inf and right == math.inf, (ahead, right)
