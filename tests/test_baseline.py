"""The if-else baseline (FR-26): its rules, and where it falls short of MANA on the scenarios."""

import math

import pytest
from mana_core.baseline import BaselineController
from mana_core.decision import (
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    Limits,
    Mode,
    MotionReport,
    ObstacleReport,
    Source,
)
from mana_core.evaluate import compare, met
from mana_core.scenarios import SCENARIOS

NOW = 100.0
LIM = Limits()


def baseline(*reports, pitch=0.0):
    b = BaselineController()
    for r in reports:
        (b.on_obstacle if isinstance(r, ObstacleReport) else b.on_ground)(r)
    b.on_motion(MotionReport(NOW, 0.9, pitch_rad=pitch))
    return b


def ground(source, kind, distance=math.nan, trust=0.9, age=0.0):
    return GroundReport(NOW - age, trust, source, kind, distance)


def test_level_ground_is_full_speed_on_wheels():
    d = baseline(ground(Source.CAMERA, Ground.FLAT)).tick(NOW)
    assert (d.mode, d.stop, d.speed_limit) == (Mode.WHEELS, False, 1.0)


def test_one_camera_is_enough_for_the_baseline_to_change_mode():
    d = baseline(ground(Source.CAMERA, Ground.STAIRS_UP)).tick(NOW)
    assert d.mode == Mode.TRACKS


def test_it_trusts_old_and_doubtful_reports():
    b = baseline(ground(Source.DEPTH_CAMERA, Ground.DROP, 0.3, trust=0.1, age=5.0))
    assert b.tick(NOW).stop
    assert DecisionCore().tick(NOW).reason == "Ground sensors not available, slowing down"


def test_a_tilt_alone_changes_to_tracks():
    d = baseline(ground(Source.CAMERA, Ground.RAMP), pitch=0.08).tick(NOW)
    assert d.mode == Mode.TRACKS


def test_stop_keeps_the_mode_and_go_drives_on():
    b = baseline(ground(Source.CAMERA, Ground.STAIRS_UP))
    b.tick(NOW)
    b.on_command(Command.STOP, 0.3)
    d = b.tick(NOW)
    assert (d.stop, d.mode, d.reason) == (True, Mode.TRACKS, "Stopped by you")
    b.on_command(Command.GO, 1.0)
    assert not b.tick(NOW).stop


@pytest.mark.parametrize(
    ("distance", "stop", "speed"), [(0.5, True, 0.0), (1.5, False, LIM.slow), (5.0, False, 1.0)]
)
def test_obstacles_stop_close_and_slow_further_out(distance, stop, speed):
    d = baseline(ObstacleReport(NOW, 0.9, Source.LIDAR, distance)).tick(NOW)
    assert (d.stop, d.speed_limit) == (stop, speed)


def test_manual_mode_still_stops_at_a_drop():
    b = baseline(ground(Source.DEPTH_CAMERA, Ground.DROP, 2.0))
    b.on_command(Command.MANUAL, 1.0)
    assert b.tick(NOW).reason == "Manual mode"
    b.on_ground(ground(Source.DEPTH_CAMERA, Ground.DROP, 0.4))
    assert b.tick(NOW).stop
    b.on_command(Command.AUTOMATIC, 1.0)
    b.on_position(None)  # position and link are not part of its rules
    b.on_link(None)
    assert b.tick(NOW).stop


@pytest.mark.parametrize("name", ["ramp", "picture", "imu_lost"])
def test_the_baseline_misses_marks_that_mana_meets(name):
    mana, marks, _ = met(SCENARIOS[name], DecisionCore)
    base, _, _ = met(SCENARIOS[name], BaselineController)
    assert mana == marks and base < marks


def test_compare_prints_both_and_passes_while_mana_meets_every_mark(capsys):
    assert compare(list(SCENARIOS)) == 0
    out = capsys.readouterr().out
    assert "Baseline" in out and out.splitlines()[-1].split()[1] == "39/39"
