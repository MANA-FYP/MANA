"""Taking slam_toolbox's jumps out of the lidar pose, with the wheels as the check (#43)."""

import math

import pytest
from mana_position.lidar_gate import JumpFilter


def drive(f, start, steps, lidar_offset=0.0, step_m=0.2, dt=0.5, t0=0.0, heading=0.0):
    """Drive straight in steps of step_m every dt s; the lidar sees the true position plus
    lidar_offset along x. Returns the corrected x of each pose."""
    out = []
    for i in range(1, steps + 1):
        d = start + i * step_m
        f.on_odom((d, 0.0, 0.0))  # odom frame: straight along its own x
        x, y = d * math.cos(heading) + lidar_offset, d * math.sin(heading)
        out.append(f.correct(t0 + i * dt, (x, y, heading))[0])
    return out


def started():
    f = JumpFilter(fade_s=1e9)  # no fading, to see the offset itself
    f.on_odom((0.0, 0.0, 0.0))
    f.correct(0.0, (0.0, 0.0, 0.0))
    return f


def test_poses_that_agree_with_the_wheels_pass_unchanged():
    f = started()
    assert drive(f, 0.0, 10) == pytest.approx([0.2 * i for i in range(1, 11)])
    assert f.jumps == 0


def test_a_metre_jump_is_taken_out_and_comes_back_when_the_lidar_does():
    f = started()
    drive(f, 0.0, 5)  # to 1 m
    jumped = drive(f, 1.0, 10, lidar_offset=-1.0, t0=2.5)  # slam jumps 1 m back and stays
    assert jumped == pytest.approx([1.0 + 0.2 * i for i in range(1, 11)], abs=1e-6)
    back = drive(f, 3.0, 3, t0=7.5)  # slam right again
    assert back == pytest.approx([3.2, 3.4, 3.6], abs=1e-6)
    assert f.jumps == 2 and f.offset == pytest.approx([0.0, 0.0], abs=1e-6)


def test_a_small_correction_passes():
    f = started()
    drive(f, 0.0, 5)
    assert drive(f, 1.0, 1, lidar_offset=-0.3, t0=2.5) == pytest.approx([0.9])  # under 0.5 m
    assert f.jumps == 0


def test_the_wheels_move_is_turned_into_the_map_heading():
    # the map is turned 90 degrees from odom: odom's x is the map's y
    f = JumpFilter(fade_s=1e9)
    f.on_odom((0.0, 0.0, 0.0))
    f.correct(0.0, (5.0, 5.0, math.pi / 2))
    f.on_odom((2.0, 0.0, 0.0))
    assert f.correct(1.0, (5.0, 7.0, math.pi / 2))[:2] == pytest.approx((5.0, 7.0))
    f.on_odom((4.0, 0.0, 0.0))
    x, y, _ = f.correct(2.0, (7.0, 7.0, math.pi / 2))  # 2 m east instead of north: a jump
    assert (x, y) == pytest.approx((5.0, 9.0))


def test_a_lasting_offset_fades_so_a_real_correction_is_taken_in_the_end():
    f = JumpFilter(fade_s=60.0)
    f.on_odom((0.0, 0.0, 0.0))
    f.correct(0.0, (0.0, 0.0, 0.0))
    xs = drive(f, 0.0, 600, lidar_offset=-1.0, step_m=0.0)  # standing, 5 minutes, slam 1 m off
    assert xs[0] == pytest.approx(0.0, abs=0.01)  # taken out at first
    assert xs[-1] == pytest.approx(-1.0, abs=0.01)  # after 5 minutes the lidar's is kept
