"""Scoring the switch between GPS and the lidar map in GPS outages (#42)."""

import pytest
from mana_sim.switching import outcomes

GPS, LIDAR = 0, 1


def reports(*spans):
    """(source, from s, to s) spans, a report every 0.5 s."""
    return [(t / 2, src) for src, a, b in spans for t in range(int(a * 2), int(b * 2))]


def test_one_switch_away_and_one_back_with_their_delays():
    r = reports((GPS, 0, 26.5), (LIDAR, 26.5, 61), (GPS, 61, 80))
    (o,) = outcomes(r, [(25.0, 55.0)])
    assert o.switch_s == pytest.approx(1.5) and o.back_s == pytest.approx(6.0)
    assert o.changes == 2


def test_switching_back_and_forth_in_one_outage_is_counted():
    r = reports((GPS, 0, 26), (LIDAR, 26, 30), (GPS, 30, 31), (LIDAR, 31, 60), (GPS, 60, 70))
    (o,) = outcomes(r, [(25.0, 55.0)])
    assert o.changes == 4


def test_each_outage_counts_up_to_the_next_and_none_means_it_never_switched():
    r = reports((GPS, 0, 27), (LIDAR, 27, 60), (GPS, 60, 120))
    first, second = outcomes(r, [(25.0, 55.0), (85.0, 115.0)])
    assert (first.switch_s, first.changes) == (pytest.approx(2.0), 2)
    assert (second.switch_s, second.changes, second.back_s) == (None, 0, pytest.approx(0.0))


def test_the_logs_are_read(tmp_path):
    from mana_sim.switching import read_outages, read_reports

    pos = tmp_path / "position.csv"
    pos.write_text("100,500000000,map,0.9,0,1.0,2.0,0.0,1.5\n101,0,map,0.8,1,1.0,2.0,0.0,0.3\n")
    assert read_reports(str(pos)) == [(pytest.approx(100.5), 0), (101.0, 1)]
    log = tmp_path / "faults.log"
    log.write_text(
        "[INFO] [fault_script]: at 25.0 s (clock 125.00): gps dropout on\n"
        "[INFO] [fault_script]: at 55.0 s (clock 155.10): gps dropout off\n"
    )
    assert read_outages(str(log)) == [(125.0, 155.1)]
