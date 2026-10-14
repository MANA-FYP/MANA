"""Decision core: every branch of the tree, with edge cases (limits) and failure cases (lost or
untrusted sensors). Pure Python; the ROS node has its own test in src/mana_core/test."""

import math
import re
from pathlib import Path

import pytest
from mana_core.decision import (
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    Limits,
    Mode,
    MotionReport,
    ObstacleReport,
    Priority,
    Source,
)

NOW = 100.0
LIM = Limits()
CAM, DEPTH, LIDAR = Source.CAMERA, Source.DEPTH_CAMERA, Source.LIDAR


def ground(source, kind, distance=math.nan, trust=0.9, age=0.0):
    return GroundReport(NOW - age, trust, source, kind, distance)


def level(age=0.0, slip=False, pitch=0.0):
    return MotionReport(NOW - age, 0.9, pitch_rad=pitch, wheel_slip=slip)


def core(*reports, motion=None, at=NOW):
    """A core fed with the reports (flat ground from both cameras by default), ticked once."""
    c = DecisionCore()
    for r in reports or (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)):
        (c.on_obstacle if isinstance(r, ObstacleReport) else c.on_ground)(r)
    c.on_motion(motion or level())
    c.tick(at)
    return c


def obstacle(distance, source=LIDAR, trust=0.9):
    return ObstacleReport(NOW, trust, source, distance)


def stairs(distance=1.5, cam_trust=0.9):
    return ground(CAM, Ground.STAIRS_UP, trust=cam_trust), ground(DEPTH, Ground.STAIRS_UP, distance)


# ---- level ground


def test_level_ground_drives_on_wheels_at_full_speed():
    d = core().decision
    assert (d.mode, d.stop, d.speed_limit, d.reason) == (Mode.WHEELS, False, 1.0, "Level ground")


# ---- 1. the user's stop always wins


def test_spoken_stop_wins_over_everything_until_go():
    c = core(*stairs(), obstacle(1.0))
    c.on_command(Command.STOP, 0.9)
    assert c.tick(NOW).stop and c.decision.reason == "Stopped by you"
    c.on_command(Command.GO, 0.2)  # a low-trust "go" is not enough
    assert c.tick(NOW).stop
    c.on_command(Command.GO, 0.9)
    assert not c.tick(NOW).stop


def test_stop_is_obeyed_even_at_low_trust():
    c = core()
    c.on_command(Command.STOP, 0.1)
    d = c.tick(NOW)
    assert d.stop and d.priority == Priority.URGENT


# ---- 2. safety: drops and obstacles


@pytest.mark.parametrize(
    ("distance", "stop", "reason"),
    [
        (0.3, True, "Drop ahead, stopping"),
        (0.5, True, "Drop ahead, stopping"),
        (1.0, False, "Drop ahead, slowing down"),
    ],
)
def test_drop_stops_at_the_edge_and_slows_before_it(distance, stop, reason):
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, distance)).decision
    assert (d.stop, d.reason) == (stop, reason)


def test_drop_without_a_distance_slows_but_does_not_stop():
    d = core(ground(CAM, Ground.DROP)).decision
    assert (d.stop, d.reason) == (False, "Drop ahead, slowing down")


@pytest.mark.parametrize(
    ("distance", "stop", "speed"),
    [(0.5, True, 0.0), (0.8, True, 0.0), (1.5, False, LIM.slow), (3.0, False, 1.0)],
)
def test_obstacle_stops_close_and_slows_further_out(distance, stop, speed):
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT), obstacle(distance)).decision
    assert (d.stop, d.speed_limit) == (stop, speed)


# ---- 3. manual mode: the user drives, safety still guards


def test_manual_mode_follows_the_user_but_safety_still_stops():
    c = core()
    c.on_command(Command.TRACKS, 0.9)  # choosing a mode hands control to the user
    d = c.tick(NOW)
    assert (d.mode, d.reason, c.automatic) == (Mode.TRACKS, "Manual mode", False)
    c.on_obstacle(obstacle(0.4))
    assert c.tick(NOW).stop
    c.on_command(Command.AUTOMATIC, 0.9)
    c.on_obstacle(obstacle(5.0))
    assert c.tick(NOW).reason != "Manual mode"


def test_speed_commands_stay_within_range():
    c = core()
    for _ in range(10):
        c.on_command(Command.SLOW_DOWN, 0.9)
    assert c.tick(NOW).speed_limit == 0.25
    for _ in range(10):
        c.on_command(Command.SPEED_UP, 0.9)
    assert c.tick(NOW).speed_limit == 1.0


# ---- 4. tracks: two sources must agree


def test_stairs_seen_by_both_cameras_change_to_tracks():
    d = core(*stairs()).decision
    assert (d.mode, d.speed_limit, d.reason) == (
        Mode.TRACKS,
        LIM.slow,
        "Stairs ahead, changing to tracks",
    )
    assert d.priority == Priority.WARNING


def test_curb_seen_by_both_cameras_changes_to_tracks():
    d = core(ground(CAM, Ground.CURB), ground(DEPTH, Ground.CURB, 1.0)).decision
    assert (d.mode, d.reason) == (Mode.TRACKS, "Curb ahead, changing to tracks")


def test_one_camera_is_not_enough_to_change_mode():
    d = core(ground(CAM, Ground.STAIRS_UP), ground(DEPTH, Ground.FLAT)).decision
    assert (d.mode, d.reason) == (Mode.WHEELS, "Possible step ahead, slowing down")


def test_stairs_further_than_the_step_distance_only_slow_down():
    d = core(*stairs(distance=LIM.step_m + 0.5)).decision
    assert d.mode == Mode.WHEELS and d.speed_limit == LIM.slow


def test_wheel_slip_changes_to_tracks():
    d = core(motion=level(slip=True)).decision
    assert (d.mode, d.reason) == (Mode.TRACKS, "Wheel slip, changing to tracks")


# ---- 4b. two sensors must agree: the vision models on one camera image count as the camera

CLS, DET = Source.CLASSIFIER, Source.DETECTOR


def test_the_classifier_and_the_detector_on_one_camera_only_slow_down():
    d = core(ground(CLS, Ground.STAIRS_UP, 1.5), ground(DET, Ground.STAIRS_UP, 1.5)).decision
    assert (d.mode, d.reason) == (Mode.WHEELS, "Possible step ahead, slowing down")


def test_the_classifier_alone_is_not_enough_to_change_mode():
    d = core(ground(CLS, Ground.STAIRS_UP, 1.5), ground(DET, Ground.FLAT)).decision
    assert d.mode == Mode.WHEELS


def test_agreeing_sensors_need_only_one_known_distance():
    d = core(ground(CLS, Ground.STAIRS_UP), ground(DEPTH, Ground.STAIRS_UP, 1.2)).decision
    assert d.mode == Mode.TRACKS


def test_agreeing_sensors_without_any_distance_do_not_change_mode():
    d = core(ground(CLS, Ground.STAIRS_UP), ground(DEPTH, Ground.STAIRS_UP)).decision
    assert (d.mode, d.reason) == (Mode.WHEELS, "Possible step ahead, slowing down")


def test_a_stale_source_cannot_confirm_a_step():
    stale = ground(CLS, Ground.STAIRS_UP, 1.5, age=1.0)
    d = core(stale, ground(DEPTH, Ground.STAIRS_UP, 1.5)).decision
    assert d.mode == Mode.WHEELS


def test_a_phone_source_and_a_depth_camera_can_agree():
    d = core(ground(DET, Ground.CURB, 1.0), ground(DEPTH, Ground.CURB, 1.0)).decision
    assert (d.mode, d.reason) == (Mode.TRACKS, "Curb ahead, changing to tracks")


# ---- back to wheels: level for 2 s


def test_back_to_wheels_only_after_level_for_two_seconds():
    c = core(*stairs())
    assert c.mode == Mode.TRACKS
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    for t in (0.0, 1.0, 1.9):  # level on the landing, but not long enough
        for r in flat:
            c.on_ground(GroundReport(NOW + t, 0.9, r.source, r.ground))
        c.on_motion(MotionReport(NOW + t, 0.9))
        assert c.tick(NOW + t).mode == Mode.TRACKS
    for r in flat:
        c.on_ground(GroundReport(NOW + 2.0, 0.9, r.source, r.ground))
    c.on_motion(MotionReport(NOW + 2.0, 0.9))
    assert c.tick(NOW + 2.0).mode == Mode.WHEELS


def test_tilt_resets_the_level_timer():
    c = core(*stairs())
    c.on_ground(ground(CAM, Ground.FLAT))
    c.on_ground(ground(DEPTH, Ground.FLAT))
    c.on_motion(level(pitch=0.2))  # still on the slope
    assert c.tick(NOW).mode == Mode.TRACKS and c.level_since is None


# ---- failure cases: lost or untrusted sensors


def test_stays_on_tracks_when_the_imu_is_lost():
    c = core(*stairs())
    c.on_ground(ground(CAM, Ground.FLAT))
    c.on_ground(ground(DEPTH, Ground.FLAT))
    c.on_motion(level(age=5.0))  # stale: level cannot be confirmed
    d = c.tick(NOW + 3.0)
    assert d.mode == Mode.TRACKS


def test_dark_corridor_untrusted_camera_is_ignored_but_depth_still_stops():
    d = core(*stairs(cam_trust=0.2)).decision  # camera labels not trusted in the dark
    assert d.mode == Mode.WHEELS
    d = core(ground(CAM, Ground.FLAT, trust=0.2), ground(DEPTH, Ground.DROP, 0.4)).decision
    assert d.stop


def test_stale_reports_are_ignored_and_the_core_slows_down():
    d = core(ground(CAM, Ground.FLAT, age=2.0), ground(DEPTH, Ground.FLAT, age=2.0)).decision
    assert (d.speed_limit, d.reason) == (LIM.slow, "Ground sensors not available, slowing down")


def test_untrusted_obstacle_is_ignored():
    d = core(
        ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT), obstacle(0.3, trust=0.1)
    ).decision
    assert not d.stop


def test_ramp_slows_down_on_wheels():
    d = core(ground(CAM, Ground.RAMP), ground(DEPTH, Ground.RAMP, 1.0)).decision
    assert (d.mode, d.reason) == (Mode.WHEELS, "Ramp ahead, slowing down")


# ---- the codes match mana_msgs


def constants(msg, field):
    """Constants declared just above a field in a .msg file."""
    found, block = {}, {}
    for line in (
        (Path(__file__).parents[1] / "src/mana_msgs/msg" / f"{msg}.msg").read_text().splitlines()
    ):
        line = line.split("#", 1)[0].strip()
        if m := re.fullmatch(r"\w+ (\w+)=(\d+)", line):
            block[m[1]] = int(m[2])
        elif line:
            if line.split()[-1] == field:
                found = block
            block = {}
    return found


@pytest.mark.parametrize(
    ("msg", "field", "enum"),
    [
        ("GroundReport", "ground", Ground),
        ("ObstacleReport", "source", Source),
        ("UserCommand", "command", Command),
        ("DriveRequest", "mode", Mode),
        ("UserMessage", "priority", Priority),
    ],
)
def test_codes_match_the_message_types(msg, field, enum):
    assert constants(msg, field) == {e.name: e.value for e in enum}
