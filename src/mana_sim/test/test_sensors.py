"""The sensor maths and the frames. No ROS, no Gazebo."""

import math
import re
from pathlib import Path

import pytest
from mana_sim.frames import SENSORS, quaternion

from mana_sim import converters as c

MODEL = Path(__file__).parents[1] / "models" / "mana_chair" / "model.sdf"


def test_a_level_chair_has_no_tilt():
    assert c.tilt(0.0, 0.0, 0.0, 1.0) == pytest.approx((0.0, 0.0))


def test_nose_down_is_a_positive_pitch_and_a_tilt_to_the_left_a_positive_roll():
    pitch, roll = c.tilt(*quaternion(0.0, 0.12, 0.0))
    assert (pitch, roll) == pytest.approx((0.12, 0.0))
    pitch, roll = c.tilt(*quaternion(0.2, 0.0, 0.5))  # the heading does not matter
    assert (pitch, roll) == pytest.approx((0.0, 0.2))


def test_wheels_that_match_the_ground_speed_do_not_slip():
    assert not c.wheel_slip([4.0] * 4, 0.6)  # 4 rad/s * 0.15 m = 0.6 m/s


def test_wheels_much_faster_than_the_chair_slip():
    assert c.wheel_slip([8.0] * 4, 0.3)


def test_standing_still_is_not_slip():
    assert not c.wheel_slip([0.5] * 4, 0.0)
    assert not c.wheel_slip([], 1.0)


def test_a_fix_east_of_the_origin_is_east():
    east, north = c.local_xy(33.6844, 73.0489, 33.6844, 73.0479)
    assert east > 80 and north == pytest.approx(0.0)


def test_the_gps_error_comes_from_the_covariance_or_the_default():
    assert c.gps_error_m(4.0, 5.0, 1.5) == pytest.approx(3.0)
    assert c.gps_error_m(0.0, 0.0, 1.5) == 1.5


def test_gps_trust_falls_with_the_error():
    assert c.gps_trust(0.0) == 1.0
    assert c.gps_trust(5.0) == pytest.approx(0.5)
    assert c.gps_trust(50.0) == 0.0


def test_the_nearest_echo_in_front_wins_and_the_ones_to_the_side_are_ignored():
    ranges = [1.0, 3.0, 2.5, 3.5, 0.5]  # at -0.4, -0.2, 0, 0.2, 0.4 rad
    assert c.nearest_ahead(ranges, -0.4, 0.2, 0.25, 4.0) == pytest.approx((2.5, 0.0))
    assert c.nearest_ahead(ranges, -0.4, 0.2, 0.5, 4.0) == pytest.approx((0.5, 0.4))


def test_echoes_that_are_far_close_or_not_finite_are_ignored():
    assert c.nearest_ahead([math.inf, 9.0, math.nan], -0.2, 0.2, 0.5, 4.0) is None
    assert c.nearest_ahead([0.1, 2.0], 0.0, 0.1, 0.5, 4.0, min_m=0.3) == pytest.approx((2.0, 0.1))


def test_the_frames_in_the_code_are_the_ones_in_the_model():
    sdf = MODEL.read_text()
    for frame, pose in SENSORS.items():
        sensor = re.search(rf"<pose>([^<]*)</pose>\s*<gz_frame_id>{frame}</gz_frame_id>", sdf)
        assert sensor, f"{frame} is not in the model"
        assert [float(v) for v in sensor.group(1).split()] == pytest.approx(pose)
