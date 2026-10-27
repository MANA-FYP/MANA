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
