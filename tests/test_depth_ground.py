"""Tests for the 3D camera ground reader (mana_perception.depth). Pure numpy, no ROS.

The depth frames are rendered by mana_perception.synthetic, which casts a ray for every pixel at a
ground shape: flat ground, ramps, a curb, stairs up and down, drops and a wall. The camera is the
3D camera planned for the chair: 1.0 m up, tilted 0.6 rad down, 87 degrees across, with a blind
zone to about 0.37 m. When the Gazebo world (#13, #14) exists, frames saved from it go through the
same checks.
"""

import math
import re
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception.synthetic import flat, ramp, render, stairs, step  # noqa: E402

from mana_perception import depth as dp  # noqa: E402
from mana_perception import synthetic as syn  # noqa: E402

COLS, ROWS, CAM = syn.COLS, syn.ROWS, syn.CAMERA


def read(ground, **kw):
    return dp.read_ground(render(ground, **kw), kw.get("cam", CAM))


def test_codes_match_the_message_file():
    text = (ROOT / "src" / "mana_msgs" / "msg" / "GroundReport.msg").read_text()
    codes = {n: int(v) for n, v in re.findall(r"^uint8 (\w+)=(\d+)", text, re.M)}
    for name in ("DEPTH_CAMERA", "FLAT", "RAMP", "STAIRS_UP", "STAIRS_DOWN", "CURB", "DROP"):
        assert getattr(dp, name) == codes[name]
    assert dp.UNKNOWN == codes["UNKNOWN"]


def test_flat_ground_is_flat_with_no_distance():
    r = read(flat)
    assert r.ground == dp.FLAT
    assert math.isnan(r.distance_m) and math.isnan(r.step_height_m)


def test_a_ramp_up_or_down_is_a_ramp_where_it_starts():
    for slope in (0.08, -0.08):
        r = read(ramp(1.5, slope))
        assert r.ground == dp.RAMP, slope
        assert r.distance_m == pytest.approx(1.5, abs=0.15)


def test_a_steep_slope_is_still_a_ramp_not_flat_ground():
    assert read(ramp(1.2, 0.3)).ground == dp.RAMP


def test_one_low_step_up_is_a_curb_with_its_height():
    r = read(step(1.0, 0.12))
    assert r.ground == dp.CURB
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_stairs_up_are_stairs_up_at_the_first_riser():
    r = read(stairs(1.5, 0.17))
    assert r.ground == dp.STAIRS_UP
    assert r.distance_m == pytest.approx(1.5, abs=0.1)
    assert r.step_height_m == pytest.approx(0.17, abs=0.04)


def test_stairs_down_seen_from_above_are_stairs_down_at_the_edge():
    r = read(stairs(1.0, -0.17))
    assert r.ground == dp.STAIRS_DOWN
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert r.step_height_m == pytest.approx(0.17, abs=0.05)


def test_a_drop_with_the_floor_in_sight_gives_its_edge_and_height():
    r = read(step(0.8, -0.3))
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(0.8, abs=0.1)
    assert r.step_height_m == pytest.approx(0.3, abs=0.05)


def test_a_drop_with_the_floor_out_of_sight_is_a_drop_of_unknown_height():
    r = read(step(1.0, -3.0))  # a ledge: the floor below is beyond the sensor's range
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(1.0, abs=0.1)
    assert math.isnan(r.step_height_m)


def test_a_drop_close_ahead_is_reported_close_enough_for_the_core_to_stop():
    r = read(step(0.6, -0.3))
    assert r.ground == dp.DROP and r.distance_m <= 0.7  # the core stops at 0.5 m


@pytest.mark.parametrize("at", [1.2, 1.5, 2.0])
def test_a_drop_farther_away_hides_floor_behind_its_edge_and_is_still_found(at):
    r = read(step(at, -0.25))
    assert r.ground == dp.DROP
    assert r.distance_m == pytest.approx(at, abs=0.1)


@pytest.mark.parametrize("at", [1.03, 1.5, 2.0])
def test_stairs_and_curbs_are_found_wherever_the_riser_falls_between_bins(at):
    assert read(stairs(at, 0.17)).ground == dp.STAIRS_UP
    r = read(step(at, 0.12))
    assert r.ground == dp.CURB
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_a_low_step_down_is_a_curb_not_a_drop():
    assert read(step(1.0, -0.12)).ground == dp.CURB


def test_a_wall_is_not_read_as_stairs():
    r = read(step(2.0, 2.5))
    assert r.ground == dp.FLAT


def test_noise_and_missing_pixels_do_not_change_the_reading():
    rng = np.random.default_rng(7)
    for ground, expected in ((flat, dp.FLAT), (stairs(1.5, 0.17), dp.STAIRS_UP)):
        image = syn.noisy(render(ground), rng)  # 1 % depth noise, 10 % of pixels missing
        image[rng.random(image.shape) < 0.02] = 0.0
        assert dp.read_ground(image, CAM).ground == expected


def test_a_two_degree_tilt_error_on_flat_ground_is_not_a_ramp():
    image = render(flat)
    tilted = dp.Camera(CAM.fx, CAM.fy, CAM.cx, CAM.cy, CAM.height_m, CAM.pitch_rad + 0.035)
    assert dp.read_ground(image, tilted).ground == dp.FLAT


def test_a_mounting_height_error_of_a_few_centimetres_does_not_matter():
    for height in (0.97, 1.03):
        cam = dp.Camera(CAM.fx, CAM.fy, CAM.cx, CAM.cy, height, CAM.pitch_rad)
        assert dp.read_ground(render(stairs(1.5, 0.17)), cam).ground == dp.STAIRS_UP
        assert dp.read_ground(render(flat), cam).ground == dp.FLAT


def test_the_blind_zone_ends_where_the_bottom_row_meets_the_ground():
    assert dp.nearest_ground(CAM, ROWS) == pytest.approx(0.37, abs=0.01)
    up = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=-0.7)
    assert dp.nearest_ground(up, ROWS) == math.inf


@pytest.mark.parametrize("at", [0.1, 0.2, 0.3, 0.4, 0.45])
def test_an_edge_down_in_the_blind_zone_is_reported_no_farther_than_it_is(at):
    for shape, kinds in (
        (stairs(at, -0.17), (dp.STAIRS_DOWN,)),
        (step(at, -0.3), (dp.DROP,)),
        (step(at, -3.0), (dp.DROP,)),  # a ledge: the floor below is out of range
    ):
        r = dp.read_ground(render(shape), CAM)
        assert r.ground in kinds, (at, r)
        assert r.distance_m <= at, (at, r)  # never farther than the real edge


@pytest.mark.parametrize("at", [0.1, 0.3, 0.45])
def test_a_curb_up_in_the_blind_zone_is_a_curb_right_in_front(at):
    r = read(step(at, 0.12))
    assert r.ground == dp.CURB and r.distance_m <= at
    assert r.step_height_m == pytest.approx(0.12, abs=0.03)


def test_the_drop_in_the_blind_zone_gives_its_measured_height():
    r = read(step(0.3, -0.3))
    assert r.step_height_m == pytest.approx(0.3, abs=0.04)


def test_a_wall_right_in_front_is_left_to_the_obstacle_sensors():
    assert read(step(0.3, 2.5)).ground == dp.UNKNOWN


def test_stairs_up_starting_in_the_blind_zone_are_stairs_not_a_wall():
    shallow = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=0.5)
    r = dp.read_ground(render(stairs(0.1, 0.17), cam=shallow), shallow)
    assert r.ground == dp.STAIRS_UP and r.distance_m == 0.0


def test_a_floor_seen_only_beyond_the_reading_range_is_a_ledge_and_no_depth_is_unknown():
    # the floor 8 m down shows only past max_range_m (3 m): a ledge right in front
    deep = dp.read_ground(render(step(0.2, -8.0), far=20.0), CAM)
    assert deep.ground == dp.DROP and deep.distance_m == 0.0
    # no depth at all, as from a covered lens or a floor beyond the sensor itself: nothing to judge
    assert dp.read_ground(np.full((ROWS, COLS), np.nan), CAM).ground == dp.UNKNOWN


def test_no_readings_at_all_is_unknown():
    assert dp.read_ground(np.full((ROWS, COLS), np.nan), CAM).ground == dp.UNKNOWN


def test_depth_images_in_metres_or_millimetres_become_metres():
    metres = np.array([[1.5, np.nan], [2.0, 0.5]], dtype="<f4")
    out = dp.to_metres("32FC1", 2, 2, 8, metres.tobytes())
    assert out[0, 0] == pytest.approx(1.5) and math.isnan(out[0, 1])
    mm = np.array([[1500, 0, 7], [2000, 500, 7]], dtype=">u2")  # padded rows, big-endian
    out = dp.to_metres("16UC1", 2, 2, 6, mm.tobytes(), big_endian=True)
    assert out.shape == (2, 2)
    assert out[1, 0] == pytest.approx(2.0) and math.isnan(out[0, 1])
    assert dp.to_metres("rgb8", 2, 2, 6, b"\0" * 12) is None


def test_the_profile_bins_heights_along_the_path():
    prof = dp.profile(render(step(1.0, 0.12)), CAM)
    have = ~np.isnan(prof.height_m)
    d, h = prof.distance_m[have], prof.height_m[have]
    assert np.all(np.abs(h[d < 0.9]) < 0.02)
    assert np.all(np.abs(h[d > 1.1] - 0.12) < 0.02)
    assert prof.visible_until_m == pytest.approx(3.0)


def test_how_far_the_camera_sees_the_ground_depends_on_its_tilt():
    level = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=0.0)
    assert dp.visible_until(level, ROWS, 3.0) == 3.0  # the top row looks above the horizon
    steep = dp.Camera.from_fov(COLS, ROWS, math.radians(87.0), height_m=1.0, pitch_rad=1.2)
    assert dp.visible_until(steep, ROWS, 3.0) < 3.0
