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
