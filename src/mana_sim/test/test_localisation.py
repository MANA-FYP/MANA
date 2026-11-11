"""Measuring a position source against ground truth, and steering round the loop (#43). No ROS."""

import math

import pytest

from mana_sim import localisation as loc


def row(t, g, m=(math.nan,) * 3, o=(math.nan,) * 3):
    return dict(zip(loc.COLUMNS, (t, *g, *m, *o), strict=True))


def test_errors_are_against_the_ground_truth_from_the_start_pose():
    # the chair starts at (1, 1.375) facing north; map and odom begin there, x ahead, y to the left
    start = (1.0, 1.375, math.pi / 2)
    rows = [
        row(0, start, m=(0, 0, 0), o=(0, 0, 0)),
        row(1, (1.0, 3.375, math.pi / 2), m=(2.0, 0.0, 0), o=(2.0, 0.3, 0)),  # 2 m ahead
        row(2, (0.0, 3.375, math.pi / 2), m=(2.0, 1.0, 0), o=(1.6, 1.3, 0)),  # then 1 m left
    ]
    m = loc.errors(rows, "mx", "my")
    assert (m.poses, m.max_m, m.last_m) == (3, pytest.approx(0.0), pytest.approx(0.0))
    o = loc.errors(rows, "ox", "oy")
    assert o.last_m == pytest.approx(0.5) and o.max_m == pytest.approx(0.5)
    assert o.mean_m == pytest.approx((0 + 0.3 + 0.5) / 3)
    assert loc.driven_m(rows) == pytest.approx(3.0)


def test_a_chair_that_moved_before_the_first_row_is_measured_from_where_it_was_spawned():
    # map and odom begin at the spawn pose; the first row came 0.2 m later
    rows = [
        row(1, (1.2, 1.375, 0.0), m=(0.2, 0.0, 0.0)),
        row(2, (3.0, 1.475, 0.0), m=(2.0, 0.1, 0.0)),
    ]
    e = loc.errors(rows, "mx", "my", loc.INDOOR_START)
    assert (e.mean_m, e.max_m) == (pytest.approx(0.0), pytest.approx(0.0))
    # from the first row instead, a perfect localiser reads off by the move before it
    e = loc.errors(rows, "mx", "my")
    assert (e.mean_m, e.max_m, e.last_m) == (pytest.approx(0.2),) * 3


def test_rows_without_the_frame_are_left_out_and_none_means_none():
    rows = [row(0, (1, 1, 0)), row(1, (2, 1, 0), m=(1.1, 0, 0))]
    e = loc.errors(rows, "mx", "my")
    assert e.poses == 1 and e.last_m == pytest.approx(0.1)
    assert loc.errors([row(0, (1, 1, 0))], "mx", "my") is None
    assert loc.errors([], "mx", "my") is None


def test_steer_turns_on_the_spot_then_drives_and_slows_near_the_target():
    fwd, turn = loc.steer((0, 0, 0), (0, 5))  # target to the left: turn first
    assert fwd == 0.0 and turn == pytest.approx(0.6)
    fwd, turn = loc.steer((0, 0, 0), (5, 0.1))  # nearly ahead: drive at the top speed
    assert fwd == pytest.approx(0.4) and abs(turn) < 0.1
    fwd, _ = loc.steer((0, 0, 0), (0.4, 0))  # 0.4 m away: slower
    assert fwd == pytest.approx(0.2)


def test_the_loop_stays_in_the_corridors_of_the_world():
    # the central block covers x 3..14, y 2.75..7.25: every leg must keep clear of it
    block = (3.0, 14.0, 2.75, 7.25)
    pts = [(1.0, 1.375), *loc.INDOOR_LOOP]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:], strict=False):
        for i in range(101):
            x, y = x1 + (x2 - x1) * i / 100, y1 + (y2 - y1) * i / 100
            assert not (block[0] < x < block[1] and block[2] < y < block[3]), (x, y)
