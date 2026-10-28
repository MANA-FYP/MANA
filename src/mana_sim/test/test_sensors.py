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


def test_a_turn_on_the_spot_is_not_slip():
    # the two sides turn opposite ways at 1 rad/s of the chair: each rim moves at 0.35 m/s
    # (wheels 0.35 m from the middle, 0.15 m radius), but the chair does not move forward
    assert not c.wheel_slip([2.33, -2.33, 2.33, -2.33], 0.0)
    assert not c.wheel_slip([-2.33, 2.33, -2.33, 2.33], 0.0)


def test_reversing_at_the_ground_speed_is_not_slip():
    assert not c.wheel_slip([-4.0] * 4, -0.6)
    assert c.wheel_slip([-8.0] * 4, -0.3)


def test_only_the_four_wheel_joints_count_and_they_are_split_by_side():
    names = [
        "wheel_fl_joint",
        "wheel_fr_joint",
        "wheel_lift",
        "wheel_rl_joint",
        "wheel_rr_joint",
        "track_lift_left",
    ]
    left, right = c.wheel_sides(names, [1.0, 2.0, 99.0, 3.0, 4.0, 77.0])
    assert (left, right) == ([1.0, 3.0], [2.0, 4.0])
    assert c.wheel_sides([], []) == ([], [])


def test_a_normal_turn_on_the_spot_is_not_turn_slip():
    # left turn at 1 rad/s: right wheels forward, left back, and a skid steer chair turns 0.67 of it
    assert not c.turn_slip([-2.33] * 2, [2.33] * 2, 0.67)
    assert not c.turn_slip([-2.33] * 2, [2.33] * 2, 0.7)


def test_wheels_that_turn_twice_as_fast_in_a_turn_are_slip():
    assert c.turn_slip([-4.67] * 2, [4.67] * 2, 0.68)
    assert c.turn_slip([4.67] * 2, [-4.67] * 2, -0.68)  # to the right


def test_driving_straight_is_not_turn_slip_and_no_wheels_is_no_answer():
    assert not c.turn_slip([3.3] * 2, [3.3] * 2, 0.0)
    assert not c.turn_slip([], [3.3], 0.0)


def test_standing_still_is_not_slip():
    assert not c.wheel_slip([0.5] * 4, 0.0)
    assert not c.wheel_slip([], 1.0)


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
