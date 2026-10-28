"""The scenarios on the Gazebo route: marks, world files, traces. No ROS, no Gazebo."""

from pathlib import Path

import pytest
from mana_core.decision import Ground, Mode
from mana_core.evaluate import run as play_scripted

from mana_core import scenarios as sc
from mana_sim import gazebo_scenarios as gs

WORLD = (Path(__file__).parents[1] / "worlds" / "mana_route.sdf").read_text()
ORIGINAL_TEN = [
    "level",
    "ramp",
    "stairs",
    "curb",
    "stop",
    "drop",
    "manual_drop",
    "picture",
    "dark",
    "imu_lost",
]


def test_the_ten_original_scenarios_are_all_there():
    assert list(gs.RUNS) == ORIGINAL_TEN
    assert set(gs.RUNS) <= set(sc.SCENARIOS)


@pytest.mark.parametrize("name", ORIGINAL_TEN)
def test_a_run_lies_on_the_route_and_has_marks(name):
    run = gs.RUNS[name]
    assert -2.0 <= run.start_x < run.end_x <= gs.DROP_X
    assert run.marks and run.duration_s > 0
    assert run.scenario().marks == run.marks and run.scenario().name == name


@pytest.mark.parametrize("name", ORIGINAL_TEN)
def test_the_marks_can_be_met_on_this_geometry_by_the_scripted_core(name):
    # the scripted world with the camera route of the run: the marks are not out of reach
    scenario = gs.RUNS[name].scenario()
    trace = play_scripted(scenario)
    failed = [m.text for m, ok, said in sc.check(scenario, trace) if not ok]
    assert failed == [], (name, failed)


def test_a_run_ends_before_the_stairs_down_and_the_drop_it_does_not_cross():
    assert gs.RUNS["stairs"].end_x < 26.0 and gs.RUNS["imu_lost"].end_x < 26.0
    assert gs.RUNS["curb"].start_x > 26.7  # after the stairs down
    assert gs.RUNS["curb"].end_x < 34.0  # on the platform, before the step down
    assert all(gs.RUNS[n].end_x <= gs.DROP_X for n in gs.RUNS)


def test_the_world_of_a_run_has_the_chair_at_the_start_and_the_boxes():
    out = gs.world_for(WORLD, gs.RUNS["dark"])
    assert "<pose>-1 0 0.38 0 0 0</pose>" in out and "<pose>1 0 0.38" not in out
    assert '<model name="person">' in out and "<pose>7 0 0.85 0 0 0</pose>" in out
    assert out.count("</world>") == 1 and out.rstrip().endswith("</sdf>")
    plain = gs.world_for(WORLD, gs.RUNS["level"])
    assert "person" not in plain and "<pose>-1.5 0 0.38" in plain


def test_a_world_without_the_chair_pose_is_refused():
    with pytest.raises(ValueError):
        gs.world_for("<sdf><world></world></sdf>", gs.RUNS["level"])


def test_mishaps_on_a_step_on_wheels_over_the_drop_and_at_an_obstacle():
    start = 18.2
    assert gs.mishaps_at(2.0, 0.4, Mode.WHEELS, start, ()) == []
    assert gs.mishaps_at(4.0, 0.4, Mode.TRACKS, start, ()) == []  # on the stairs on tracks
    assert "on the stairs on wheels" in gs.mishaps_at(3.9, 0.4, Mode.WHEELS, start, ())[0]
    assert gs.mishaps_at(5.0, 0.7, Mode.WHEELS, start, ()) == []  # at the top
    assert "went over the drop" in gs.mishaps_at(0.0, -1.0, Mode.WHEELS, 30.0, ())[0]
    assert "went over the drop" in gs.mishaps_at(8.0, 0.4, Mode.WHEELS, 30.0, ())[0]
    box = gs.RUNS["dark"].boxes
    assert "hit the person" in gs.mishaps_at(8.1, 0.4, Mode.WHEELS, -1.0, box)[0]
    assert gs.mishaps_at(7.0, 0.4, Mode.WHEELS, -1.0, box) == []


def test_the_recording_keeps_each_kind_of_mishap_once():
    rec = gs.Recording()
    rec.add_mishaps(["on the stairs on wheels at 6.20 m"])
    rec.add_mishaps(["on the stairs on wheels at 6.30 m", "went over the drop at 8.0 m"])
    assert rec.trace.mishaps == ["on the stairs on wheels at 6.20 m", "went over the drop at 8.0 m"]


def test_a_trace_survives_the_json_file():
    trace = sc.Trace()
    trace.samples.append(sc.Sample(0.1, 0.5, Mode.TRACKS, True, 0.3, "Stopped by you"))
    trace.messages.append(sc.Message(0.1, "Stopped by you", True))
    trace.said.append((0.0, sc.Command.STOP))
    trace.mishaps.append("hit the person at 8.0 m")
    trace.end_m = 0.5
    back = gs.trace_from_json(gs.trace_to_json(trace))
    assert back.samples == trace.samples and back.messages == trace.messages
    assert back.said == trace.said and back.mishaps == trace.mishaps and back.end_m == 0.5


def test_a_perfect_run_of_level_ground_is_scored_as_met():
    trace = sc.Trace()
    for i in range(1, 100):
        t = i / 10
        trace.samples.append(sc.Sample(t, t * 1.2, Mode.WHEELS, False, 1.0, "Level ground"))
    trace.end_m = 9.5
    results = sc.check(gs.RUNS["level"].scenario(), trace)
    assert all(ok for _, ok, _ in results)


def test_a_run_that_never_reaches_the_end_fails_that_mark():
    trace = sc.Trace()
    trace.samples.append(sc.Sample(1.0, 1.0, Mode.WHEELS, False, 1.0, "Level ground"))
    trace.end_m = 1.0
    failed = [m.text for m, ok, _ in sc.check(gs.RUNS["level"].scenario(), trace) if not ok]
    assert "Reaches the end of the route" in failed


def test_the_camera_standin_names_the_ground_ahead_but_not_a_picture_on_the_floor():
    route = gs.RUNS["stairs"].camera
    assert gs.camera_ground(route, -2.0) == Ground.FLAT  # 5.8 m away: out of its 5 m range
    assert gs.camera_ground(route, 0.0) == Ground.STAIRS_UP
    assert gs.camera_ground(route, 4.6) == Ground.FLAT  # at the top
    picture = gs.RUNS["picture"].camera
    assert gs.camera_ground(picture, 1.0) == Ground.STAIRS_UP  # it only looks like stairs


def test_the_camera_is_trusted_less_as_the_light_falls():
    assert gs.camera_trust(gs.LIT_MEAN) == pytest.approx(gs.CAMERA_TRUST)
    assert gs.camera_trust(200.0) == pytest.approx(gs.CAMERA_TRUST)
    assert gs.camera_trust(6.0) == pytest.approx(0.09)
    assert gs.camera_trust(0.0) == 0.0
