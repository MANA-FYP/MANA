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
from mana_core.evaluate import compare, main, met, run
from mana_core.scenarios import SCENARIOS, check

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


def test_it_trusts_old_and_doubtful_reports_that_mana_ignores():
    doubtful = ground(Source.DEPTH_CAMERA, Ground.DROP, 0.3, trust=0.1, age=5.0)
    assert baseline(doubtful).tick(NOW).stop
    mana = DecisionCore()
    mana.on_ground(doubtful)
    mana.on_motion(MotionReport(NOW, 0.9))
    d = mana.tick(NOW)
    assert not d.stop and d.reason == "Ground sensors not available, slowing down"


def test_a_ramp_slows_on_wheels_and_a_stairs_sized_tilt_means_tracks():
    d = baseline(ground(Source.CAMERA, Ground.RAMP), pitch=0.08).tick(NOW)
    assert (d.mode, d.reason) == (Mode.WHEELS, "Ramp ahead, slowing down")
    d = baseline(ground(Source.CAMERA, Ground.FLAT), pitch=0.55).tick(NOW)
    assert (d.mode, d.reason) == (Mode.TRACKS, "On tracks until the ground is level")


def test_a_drop_further_ahead_slows_down():
    d = baseline(ground(Source.DEPTH_CAMERA, Ground.DROP, 1.5)).tick(NOW)
    assert (d.stop, d.reason) == (False, "Drop ahead, slowing down")


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


def test_the_picture_of_stairs_is_where_the_baseline_falls_short():
    # one camera is enough for the baseline to ask for tracks; MANA needs two sensors
    trace = run(SCENARIOS["picture"], controller=BaselineController)
    failed = [m.text for m, ok, _ in check(SCENARIOS["picture"], trace) if not ok]
    assert failed == ["No track request on flat ground or ramps"]
    assert not run(SCENARIOS["picture"]).mishaps and met(SCENARIOS["picture"], DecisionCore)[0] == 4


@pytest.mark.parametrize(
    ("name", "misses"),
    [
        ("dark_stairs", ["Waits before the stairs until the user says TRACKS"]),
        ("dark_curb", []),  # on tracks from the start, it is still slow short of the curb at 12 s
    ],
)
def test_in_the_dark_the_baseline_crosses_on_one_sensor_without_stopping(name, misses):
    # one source is enough for the baseline to change to tracks: here that gets it across,
    # at the picture of stairs the same rule asks for tracks on a wall
    trace = run(SCENARIOS[name], controller=BaselineController)
    failed = [m.text for m, ok, _ in check(SCENARIOS[name], trace) if not ok]
    assert failed == ['Says "Step ahead, stopping"', *misses]
    assert not trace.mishaps


def test_the_baseline_meets_every_other_scenario():
    for name, scenario in SCENARIOS.items():
        if name not in ("picture", "dark_stairs", "dark_curb"):
            base, marks, _ = met(scenario, BaselineController)
            assert base == marks, name


def test_compare_prints_both_and_passes_while_mana_meets_every_mark(capsys):
    marks = sum(len(check(s, run(s))) for s in SCENARIOS.values())
    assert compare(list(SCENARIOS)) == 0
    out = capsys.readouterr().out
    assert "Baseline" in out and out.splitlines()[-1].split()[1] == f"{marks}/{marks}"


def test_main_runs_the_baseline_and_rejects_unknown_options(capsys):
    assert main(["--baseline", "picture"]) == 1  # the baseline misses one mark there
    assert "FAIL" in capsys.readouterr().out
    assert main(["--basline"]) == 2
    assert main(["nowhere"]) == 2
    assert main(["--baseline", "level"]) == 0
