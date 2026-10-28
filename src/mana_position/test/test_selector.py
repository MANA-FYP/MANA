"""Position source: the switching rule (pure Python) and its codes against mana_msgs."""

import math
import re
from pathlib import Path

import pytest
from mana_position.selector import PositionSelector, Source

DT = 0.1


def run(selector, readings, lidar_ok=True, start=0.0):
    """Feed (seconds, gps error) pieces at 10 Hz; return the source at every tick."""
    t, seen = start, []
    for seconds, error in readings:
        for _ in range(round(seconds / DT)):
            seen.append((round(t, 2), selector.update(error, lidar_ok, t)))
            t += DT
    return seen


def switches(seen):
    return [(t, b) for (_, a), (t, b) in zip(seen, seen[1:], strict=False) if a != b]


def test_a_good_fix_stays_on_gps():
    assert {s for _, s in run(PositionSelector(), [(10, 3.0)])} == {Source.GPS}


def test_losing_gps_switches_to_the_lidar_map_within_two_seconds():
    seen = run(PositionSelector(), [(5, 3.0), (5, None)])
    ((t, source),) = switches(seen)
    assert source == Source.LIDAR_MAP and t - 5.0 <= 2.0


def test_one_bad_reading_does_not_switch():
    seen = run(PositionSelector(), [(3, 3.0), (0.5, 40.0), (3, 3.0)])
    assert not switches(seen)


def test_without_the_lidar_it_falls_back_to_odometry_and_back_to_the_map():
    s = PositionSelector()
    run(s, [(3, None)], lidar_ok=False)
    assert s.source == Source.ODOMETRY
    assert s.update(None, True, 3.1) == Source.LIDAR_MAP


def test_gps_must_be_good_for_back_s_before_it_is_chosen_again():
    s = PositionSelector()
    seen = run(s, [(3, None), (4.5, 3.0), (1, 3.0)])
    (_, away), (t, back) = switches(seen)
    assert (away, back) == (Source.LIDAR_MAP, Source.GPS) and t >= 3.0 + 5.0 - DT


def test_an_error_between_the_two_levels_never_switches_back():
    # in a doorway the error wanders between back_m and out_m: one switch away, none back
    seen = run(PositionSelector(), [(3, None), *[(1, 9.0), (1, 14.0)] * 5])
    assert [s for _, s in switches(seen)] == [Source.LIDAR_MAP]


def test_one_outage_gives_one_switch_away_and_one_back():
    seen = run(PositionSelector(), [(3, 3.0), (6, None), (1, 30.0), (8, 3.0)])
    assert [s for _, s in switches(seen)] == [Source.LIDAR_MAP, Source.GPS]


def test_nan_counts_as_no_fix():
    s = PositionSelector()
    run(s, [(2, math.nan)])
    assert s.source == Source.LIDAR_MAP


def constants(field):
    msg = Path(__file__).parents[2] / "mana_msgs/msg/PositionReport.msg"
    found, block = {}, {}
    for line in msg.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if m := re.fullmatch(r"\w+ (\w+)=(\d+)", line):
            block[m[1]] = int(m[2])
        elif line:
            found = block if line.split()[-1] == field else found
            block = {}
    return found


@pytest.mark.parametrize("field", ["source"])
def test_codes_match_the_message_type(field):
    assert constants(field) == {e.name: e.value for e in Source}


def test_a_gps_that_falls_silent_switches_within_two_seconds_of_its_last_good_fix():
    # the node only notices silence once the last fix is max_age_s old (1.5 s), so it passes
    # the time of that fix and the selector counts from there
    s, last_fix, max_age = PositionSelector(), 5.0, 1.5
    t, switched = last_fix, None
    while switched is None and t < 10.0:
        error = 3.0 if t - last_fix <= max_age else None
        if s.update(error, True, t, lost_since=last_fix) == Source.LIDAR_MAP:
            switched = t
        t = round(t + DT, 2)
    assert switched is not None and switched - last_fix <= 2.0


def test_lost_since_does_not_hurry_the_way_back_to_gps():
    s = PositionSelector()
    run(s, [(3, None)])
    assert s.update(3.0, True, 3.1, lost_since=0.0) == Source.LIDAR_MAP


def test_one_bad_reading_on_a_1_hz_gps_does_not_switch():
    # called as the node calls it: lost_since only while the last fix is stale (older than 1.5 s)
    s, last_good, last_fix, error, seen = PositionSelector(), None, None, None, []
    for i in range(200):
        t = round(i * DT, 2)
        if abs(t - round(t)) < 1e-9:  # one fix a second; the one at 10 s has no fix
            last_fix, error = t, (None if round(t) == 10 else 3.0)
            last_good = t if error is not None else last_good
        stale = last_fix is None or t - last_fix > 1.5
        reading = None if stale else error
        seen.append((t, s.update(reading, True, t, lost_since=last_good if stale else None)))
    assert not switches(seen)


def test_each_limit_is_reached_at_its_own_value():
    """GPS is left only when its error is over out_m, and taken back only under back_m; the
    times count as reached at their value."""
    s = PositionSelector(out_m=15.0, out_s=1.5, back_m=8.0, back_s=5.0)
    assert {source for _, source in run(s, [(5, 15.0)])} == {Source.GPS}  # 15 m is not over
    seen = run(PositionSelector(), [(1, 3.0), (1.5, 16.0), (0.1, 16.0)])
    ((t, source),) = switches(seen)
    assert (round(t - 1.0, 2), source) == (1.5, Source.LIDAR_MAP)  # after exactly out_s
    s = PositionSelector()
    run(s, [(3, None)])
    assert s.source == Source.LIDAR_MAP
    assert {source for _, source in run(s, [(10, 8.0)], start=3.0)} == {Source.LIDAR_MAP}
