"""The host: what the chair does with the core's drive request. No ROS, no Gazebo."""

import pytest
from mana_sim.host import (
    SWITCH_S,
    TOP_SPEED,
    TRACKS,
    TRACKS_DOWN,
    TRACKS_UP,
    WHEELS,
    WHEELS_DOWN,
    WATCHDOG_S,
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
