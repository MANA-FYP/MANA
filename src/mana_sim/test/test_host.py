"""The host: what the chair does with the core's drive request. No ROS, no Gazebo."""

import pytest
from mana_sim.host import (
    REVERSE_MAX,
    SWITCH_S,
    TOP_SPEED,
    TRACKS,
    TRACKS_DOWN,
    TRACKS_UP,
    TURN_MAX,
    WATCHDOG_S,
    WHEELS,
    WHEELS_DOWN,
    WHEELS_UP,
    Host,
)


def test_it_starts_on_wheels_with_the_tracks_lifted():
    c = Host().halt()
    assert (c.wheel_lift, c.track_lift) == (WHEELS_DOWN, TRACKS_UP)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)


def test_on_wheels_the_speed_limit_is_a_share_of_top_speed():
    c = Host().update(WHEELS, False, 0.5, 0.0)
    assert c.wheels_mps == pytest.approx(0.5 * TOP_SPEED)
    assert c.tracks_mps == 0.0


def test_a_stop_gives_zero_speed_and_keeps_the_mode():
    h = Host()
    c = h.update(WHEELS, True, 1.0, 0.0)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)
    assert (c.wheel_lift, c.track_lift) == (WHEELS_DOWN, TRACKS_UP)


def test_the_speed_limit_is_kept_between_0_and_1():
    h = Host()
    assert h.update(WHEELS, False, 3.0, 0.0).wheels_mps == pytest.approx(TOP_SPEED)
    assert h.update(WHEELS, False, -1.0, 0.1).wheels_mps == 0.0


def test_changing_to_tracks_stops_first_and_moves_the_slides():
    h = Host()
    c = h.update(TRACKS, False, 0.3, 10.0)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)  # not driving while the slides move
    assert (c.wheel_lift, c.track_lift) == (WHEELS_UP, TRACKS_DOWN)
    c = h.update(TRACKS, False, 0.3, 10.0 + SWITCH_S - 0.1)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)


def test_the_tracks_drive_when_the_slides_are_done():
    h = Host()
    h.update(TRACKS, False, 0.3, 10.0)
    c = h.update(TRACKS, False, 0.3, 10.0 + SWITCH_S)
    assert c.wheels_mps == 0.0
    assert c.tracks_mps == pytest.approx(0.3 * TOP_SPEED)


def test_changing_back_to_wheels_does_the_same_in_reverse():
    h = Host(TRACKS)
    c = h.update(WHEELS, False, 1.0, 5.0)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)
    assert (c.wheel_lift, c.track_lift) == (WHEELS_DOWN, TRACKS_UP)
    c = h.update(WHEELS, False, 1.0, 5.0 + SWITCH_S)
    assert c.wheels_mps == pytest.approx(TOP_SPEED)


def test_a_request_that_flips_back_is_followed_at_once():
    h = Host()
    h.update(TRACKS, False, 0.3, 1.0)
    c = h.update(WHEELS, False, 1.0, 1.2)  # before the first change has finished
    assert (c.wheel_lift, c.track_lift) == (WHEELS_DOWN, TRACKS_UP)
    assert c.wheels_mps == 0.0  # the slides still need their time
    assert h.update(WHEELS, False, 1.0, 1.2 + SWITCH_S).wheels_mps == pytest.approx(TOP_SPEED)


def test_halt_keeps_the_undercarriage_that_is_down():
    h = Host()
    h.update(TRACKS, False, 0.3, 0.0)
    c = h.halt()
    assert (c.wheel_lift, c.track_lift) == (WHEELS_UP, TRACKS_DOWN)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)


def test_an_old_request_is_not_replayed():
    h = Host()
    assert h.follow(None, 0.0, 1.0) == h.halt()
    c = h.follow((WHEELS, False, 1.0), 0.0, 0.2)
    assert c.wheels_mps == pytest.approx(TOP_SPEED)
    assert h.follow((WHEELS, False, 1.0), 0.0, WATCHDOG_S + 0.1).wheels_mps == 0.0


def test_a_clock_gone_back_does_not_keep_an_old_request_alive():
    # a world reset: the last request was received at 100 s and it is now 10 s (#85)
    h, request = Host(), (WHEELS, False, 1.0)
    assert h.follow(request, 100.0, 100.0).wheels_mps == pytest.approx(TOP_SPEED)
    assert h.follow(request, 100.0, 10.0).wheels_mps == 0.0
    c = h.follow(request, 10.0, 10.0, driver=(0.5, 0.2), driver_at=100.0, follow_driver=True)
    assert (c.wheels_mps, c.wheels_turn) == (0.0, 0.0)  # nor an old driver command


def test_a_stop_during_a_mode_change_keeps_the_chair_still():
    h = Host()
    h.follow((TRACKS, False, 1.0), 0.0, 0.0)
    c = h.follow((TRACKS, True, 1.0), 1.0, 1.1)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)


def test_a_speed_that_is_not_a_number_is_no_speed():
    for bad in (float("nan"), float("inf"), float("-inf")):
        c = Host().update(WHEELS, False, bad, 0.0)
        assert c.wheels_mps == 0.0 or c.wheels_mps == pytest.approx(TOP_SPEED)
    assert Host().update(WHEELS, False, float("nan"), 0.0).wheels_mps == 0.0


def test_a_mode_the_host_does_not_have_stops_the_chair_and_changes_nothing():
    h = Host()
    c = h.update(7, False, 1.0, 0.0)
    assert (c.wheels_mps, c.tracks_mps) == (0.0, 0.0)
    assert (c.wheel_lift, c.track_lift) == (WHEELS_DOWN, TRACKS_UP)
    assert h.update(WHEELS, False, 1.0, 0.1).wheels_mps == pytest.approx(TOP_SPEED)


# ---- a driver steers (#76): the supervisor's keyboard or the autopilot, capped by MANA


def test_a_driver_steers_and_sets_the_speed_on_wheels():
    c = Host().update(WHEELS, False, 1.0, 0.0, driver=(0.5, 0.4))
    assert (c.wheels_mps, c.wheels_turn) == (0.5, 0.4)
    assert (c.tracks_mps, c.tracks_turn) == (0.0, 0.0)


def test_a_driver_steers_the_tracks_when_they_are_down():
    h = Host(TRACKS)
    c = h.update(TRACKS, False, 1.0, 0.0, driver=(0.3, -0.5))
    assert (c.tracks_mps, c.tracks_turn) == (0.3, -0.5)
    assert (c.wheels_mps, c.wheels_turn) == (0.0, 0.0)


def test_the_driver_is_never_faster_forward_than_the_speed_limit():
    c = Host().update(WHEELS, False, 0.3, 0.0, driver=(5.0, 0.0))
    assert c.wheels_mps == pytest.approx(0.3 * TOP_SPEED)


def test_after_a_stop_the_driver_can_only_turn_or_back_off():
    h = Host()
    assert h.update(WHEELS, True, 1.0, 0.0, driver=(1.0, 0.0)).wheels_mps == 0.0  # no forward
    c = h.update(WHEELS, True, 1.0, 0.1, driver=(0.0, 0.8))
    assert (c.wheels_mps, c.wheels_turn) == (0.0, 0.8)  # turning on the spot
    assert h.update(WHEELS, True, 1.0, 0.2, driver=(-1.0, 0.0)).wheels_mps == -REVERSE_MAX


def test_turning_and_reversing_are_capped():
    c = Host().update(WHEELS, False, 1.0, 0.0, driver=(-5.0, 9.0))
    assert (c.wheels_mps, c.wheels_turn) == (-REVERSE_MAX, TURN_MAX)
    assert Host().update(WHEELS, False, 1.0, 0.0, driver=(0.0, -9.0)).wheels_turn == -TURN_MAX


def test_a_driver_value_that_is_not_a_number_is_no_motion():
    c = Host().update(WHEELS, False, 1.0, 0.0, driver=(float("nan"), float("inf")))
    assert (c.wheels_mps, c.wheels_turn) == (0.0, 0.0)


def test_the_mode_is_always_mana_s_and_nothing_moves_while_the_slides_do():
    h = Host()
    c = h.update(TRACKS, False, 1.0, 0.0, driver=(0.5, 0.5))
    assert (c.wheel_lift, c.track_lift) == (WHEELS_UP, TRACKS_DOWN)
    assert (c.wheels_mps, c.tracks_mps, c.wheels_turn, c.tracks_turn) == (0.0, 0.0, 0.0, 0.0)
    c = h.update(TRACKS, False, 1.0, SWITCH_S, driver=(0.5, 0.5))
    assert (c.tracks_mps, c.tracks_turn) == (0.5, 0.5)


def test_when_following_a_driver_the_chair_stands_still_once_the_driver_goes_quiet():
    h, request = Host(), (WHEELS, False, 1.0)
    c = h.follow(request, 1.0, 1.0, driver=(0.5, 0.2), driver_at=1.0, follow_driver=True)
    assert (c.wheels_mps, c.wheels_turn) == (0.5, 0.2)
    late = 1.0 + WATCHDOG_S + 0.1
    c = h.follow(request, late, late, driver=(0.5, 0.2), driver_at=1.0, follow_driver=True)
    assert (c.wheels_mps, c.wheels_turn) == (0.0, 0.0)  # keys let go: still, not "drive on"
    c = h.follow(request, 2.0, 2.0, follow_driver=True)  # no driver yet: still
    assert c.wheels_mps == 0.0


def test_without_follow_driver_the_chair_drives_straight_as_before():
    h = Host()
    c = h.follow((WHEELS, False, 0.5), 0.0, 0.0, driver=(1.0, 1.0), driver_at=0.0)
    assert (c.wheels_mps, c.wheels_turn) == (pytest.approx(0.5 * TOP_SPEED), 0.0)
