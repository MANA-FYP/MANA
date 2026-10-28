"""Repeated noisy runs (#54) and the full-system parts of the scripted world: the position and
alert-link modules, the noise, the moved faults and commands, and the pass-rate interval."""

import math
import random

import pytest
from mana_core.decision import DecisionCore, Link, LinkReport, PositionReport, PositionSource
from mana_core.evaluate import run
from mana_core.repeat import NO_NOISE, Noise, NoisyWorld, main, moved, one, table, wilson
from mana_core.scenarios import SCENARIOS, check
from mana_core.world import Fault, Route, World


def modules(route, x=0.0):
    w = World(route)
    w.x = x
    return [r for r in w.sense(0.0) if isinstance(r, PositionReport | LinkReport)]


def test_only_a_full_system_route_has_the_position_and_link_modules():
    assert modules(Route(10.0)) == []
    position, wifi, bluetooth = modules(Route(10.0, full_system=True))
    assert position.source == PositionSource.GPS
    assert (wifi.link, wifi.connected) == (Link.WIFI, True)
    assert (bluetooth.link, bluetooth.connected) == (Link.BLUETOOTH, True)


def test_without_gps_the_position_comes_from_the_lidar_map_then_odometry():
    no_gps = Route(10.0, faults=(Fault("gps", from_m=5.0),), full_system=True)
    assert modules(no_gps, 4.0)[0].source == PositionSource.GPS
    assert modules(no_gps, 6.0)[0].source == PositionSource.LIDAR_MAP
    neither = Route(10.0, faults=(Fault("gps"), Fault("lidar_map")), full_system=True)
    assert modules(neither)[0].source == PositionSource.ODOMETRY


def test_a_radio_with_a_fault_reports_it_is_not_connected():
    route = Route(10.0, faults=(Fault("wifi", from_m=2.0, to_m=4.0),), full_system=True)
    assert modules(route, 3.0)[1].connected is False
    assert modules(route, 4.5)[1].connected is True


def test_without_noise_a_noisy_run_is_the_scripted_run():
    for name in ("stairs", "wifi_drop", "stop"):
        sc = SCENARIOS[name]
        plain = run(sc)
        same = run(sc, world=NoisyWorld(sc.route, random.Random(0), NO_NOISE))
        assert plain.samples == same.samples


def test_the_same_seed_gives_the_same_run_and_another_seed_another():
    sc = SCENARIOS["drop"]
    a, b, c = (one(sc, DecisionCore, seed, Noise()) for seed in (7, 7, 8))
    assert a == b
    traces = [
        run(sc, world=NoisyWorld(sc.route, random.Random(s), Noise())).samples for s in (7, 8)
    ]
    assert traces[0] != traces[1] and c.marks == a.marks


def test_moved_keeps_the_route_ends_and_shifts_faults_and_commands_within_bounds():
    noise = Noise()
    sc = SCENARIOS["wifi_drop"]
    for seed in range(50):
        m = moved(sc, random.Random(seed), noise)
        for old, new in zip(sc.route.faults, m.route.faults, strict=True):
            assert abs(new.from_m - old.from_m) <= noise.fault_shift_m
            assert new.to_m == old.to_m if math.isinf(old.to_m) else abs(new.to_m - old.to_m) <= 0.5
    stop = SCENARIOS["stop"]
    for seed in range(50):
        m = moved(stop, random.Random(seed), noise)
        for old, new in zip(stop.says, m.says, strict=True):
            assert abs(new.at_s - old.at_s) <= noise.say_shift_s
    assert (
        moved(SCENARIOS["dark"], random.Random(1), noise).route.faults
        == SCENARIOS["dark"].route.faults
    )  # a fault over the whole route stays over the whole route


def test_the_full_system_marks_stay_clear_of_the_moved_faults():
    """Without other noise, moving the faults never fails a mark: the marks keep clear of them."""
    only_moved = Noise(0, 0, 0, 0, 0, 0)
    for name in ("entry", "wifi_drop", "imu_lost"):
        for seed in range(30):
            r = one(SCENARIOS[name], DecisionCore, seed, only_moved)
            assert r.marks_met == r.marks, (name, seed)
    # two runs of the table where Bluetooth is lost late (8.44 and 8.45 m) and the chair, with the
    # other noise on, is still at full speed at 8.52 m: the stretch with no link starts after that
    for i in (86, 114):
        r = one(SCENARIOS["wifi_drop"], DecisionCore, 100_003 + i, Noise(false_p=0.0))
        assert r.marks_met == r.marks, i


def test_noise_is_drawn_only_where_it_applies():
    sc = SCENARIOS["level"]
    w = NoisyWorld(sc.route, random.Random(3), Noise(lost_p=0, false_p=0))
    for report in w.sense(1.0):
        assert 0.0 <= report.trust <= 1.0
        if hasattr(report, "distance_m") and math.isnan(report.distance_m):
            assert math.isnan(report.distance_m)  # the camera still gives no distance


def test_the_wilson_interval():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(10, 10)
    assert hi == 1.0 and lo == pytest.approx(0.722, abs=0.001)
    lo, hi = wilson(50, 100)
    assert (lo, hi) == (pytest.approx(0.404, abs=0.001), pytest.approx(0.596, abs=0.001))


def test_the_table_has_a_row_per_scenario():
    text, stats = table(["level", "drop"], runs=3, seed=1)
    assert text.count("\n") == 3 and set(stats) == {"level", "drop"}
    assert stats["drop"]["MANA"]["runs"] == 3


def test_command_line(capsys):
    assert main(["--runs", "2", "level"]) == 0
    assert "| Level ground |" in capsys.readouterr().out
    assert main(["--runs", "2", "--no-false-reports", "level"]) == 0
    assert "noise with no false reports" in capsys.readouterr().out
    assert main(["--runs"]) == 2
    assert main(["--runs", "x"]) == 2
    assert main(["--fast"]) == 2
    assert main(["nowhere"]) == 2


def test_the_noise_finds_a_weakness_the_exact_world_cannot():
    """With noise MANA still never has a mishap at the drop, but a single 3D camera reading over
    edge_m can release its stop for a tick (#97, docs/results.md): the stop mark fails then."""
    sc = SCENARIOS["drop"]
    results = [one(sc, DecisionCore, 100_003 + i, Noise()) for i in range(20)]
    assert not any(r.mishap for r in results)
    assert any(r.marks_met < r.marks for r in results)
    assert all(ok for _, ok, _ in check(sc, run(sc)))  # and without noise every mark is met
