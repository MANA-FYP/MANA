"""Decision core: every branch of the tree, with edge cases (limits) and failure cases (lost or
untrusted sensors). Pure Python; the ROS node has its own test in src/mana_core/test."""

import math
import re
from pathlib import Path

import pytest
from mana_core.decision import (
    ASK_DOWN,
    STEP_STOP,
    Command,
    DecisionCore,
    Ground,
    GroundReport,
    Limits,
    Link,
    LinkReport,
    Mode,
    MotionReport,
    Mount,
    ObstacleReport,
    PositionReport,
    PositionSource,
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


# ---- a stop holds through one reading (#97)


def ticks(c, reports, start_s, seconds, obstacles=()):
    """Feed the same readings at 10 Hz from start_s; return the decisions."""
    out = []
    for i in range(round(seconds * 10)):
        t = NOW + start_s + i / 10
        for o in obstacles:
            c.on_obstacle(ObstacleReport(t, 0.9, LIDAR, o))
        out.append(fed(c, reports, t))
    return out


def test_a_drop_stop_is_not_released_by_readings_just_past_the_edge():
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.4))
    assert c.decision.stop
    just_past = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, LIM.edge_m + 0.05))
    assert all(d.stop for d in ticks(c, just_past, 0.1, 2.0))  # within release_m: still held
    gone = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, LIM.edge_m + LIM.release_m + 0.05))
    after = ticks(c, gone, 2.1, 1.0)
    assert after[0].stop  # held for hold_s
    assert after[-1].reason == "Drop ahead, slowing down"


def test_an_obstacle_stop_is_not_released_by_readings_just_past_the_stop_distance():
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    c = core(*flat, obstacle(0.7))
    assert c.decision.stop
    assert all(d.stop for d in ticks(c, flat, 0.1, 2.0, obstacles=[0.85]))
    after = ticks(c, flat, 2.1, 1.0, obstacles=[0.95])
    assert after[0].stop
    assert (after[-1].stop, after[-1].reason) == (False, "Obstacle ahead, slowing down")


def test_a_drop_further_than_the_edge_does_not_stop_before_any_stop():
    # the band past the limit counts only while a stop holds, not for the first stop
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, LIM.edge_m + 0.05)).decision
    assert (d.stop, d.reason) == (False, "Drop ahead, slowing down")


def test_a_hold_counts_from_its_own_hazard_whichever_branch_stopped():
    # the drop stops first; the obstacle seen at the same time still holds once the drop is gone
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.4), obstacle(0.7))
    assert c.decision.reason == "Drop ahead, stopping"
    c.on_obstacle(ObstacleReport(NOW + 0.6, 0.9, LIDAR, 0.85))
    d = fed(c, (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)), NOW + 0.6)
    assert not d.stop  # both were last seen 0.6 s ago
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.4), obstacle(0.7))
    c.on_obstacle(ObstacleReport(NOW + 0.3, 0.9, LIDAR, 0.7))
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    assert fed(c, flat, NOW + 0.3).reason == "Drop ahead, stopping"  # the drop's hold, from 0 s
    c.on_obstacle(ObstacleReport(NOW + 0.6, 0.9, LIDAR, 0.85))
    d = fed(c, (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)), NOW + 0.6)
    assert d.reason == "Obstacle ahead, stopping"  # seen at 0.3 s, behind the drop's stop


def test_after_the_clock_goes_back_a_stop_from_before_is_not_held():
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.4))
    assert c.decision.stop
    for source in (CAM, DEPTH):  # a world reset: the clock starts again near 0
        c.on_ground(GroundReport(1.0, 0.9, source, Ground.FLAT, 3.0))
    c.on_motion(MotionReport(1.0, 0.9))
    assert not c.tick(1.0).stop


def test_a_user_go_is_not_held():
    c = core()
    c.on_command(Command.STOP, 0.9)
    assert c.tick(NOW + 0.1).stop
    c.on_command(Command.GO, 0.9)
    assert not c.tick(NOW + 0.2).stop


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
    assert (d.stop, d.speed_limit) == (False, LIM.slow)  # slip slows down, it does not stop


def test_each_limit_is_reached_at_its_own_value():
    """A report exactly max_age_s old or exactly at min_trust is used, and a hazard exactly at
    its distance is acted on, as the limits are written: "this close", "older than this"."""
    assert core(*stairs(distance=LIM.step_m)).decision.mode == Mode.TRACKS
    one = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_UP, LIM.step_m)).decision
    assert one.reason == "Possible step ahead, slowing down"
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT), obstacle(LIM.obstacle_slow_m))
    assert d.decision.reason == "Obstacle ahead, slowing down"
    at_limits = (
        ground(CAM, Ground.STAIRS_UP, trust=LIM.min_trust, age=LIM.max_age_s),
        ground(DEPTH, Ground.STAIRS_UP, 1.5, trust=LIM.min_trust, age=LIM.max_age_s),
    )
    assert core(*at_limits).decision.mode == Mode.TRACKS
    c = core()
    c.on_command(Command.STOP, 0.9)
    c.on_command(Command.GO, LIM.min_trust)
    assert not c.tick(NOW + 0.1).stop


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
    assert (d.stop, d.reason) == (False, "On tracks until the ground is level")


def test_dark_corridor_untrusted_camera_is_ignored_but_depth_still_stops():
    d = core(*stairs(cam_trust=0.2)).decision  # camera labels not trusted in the dark
    assert d.mode == Mode.WHEELS
    d = core(ground(CAM, Ground.FLAT, trust=0.2), ground(DEPTH, Ground.DROP, 0.4)).decision
    assert d.stop


def test_stale_reports_are_ignored_and_the_core_slows_down():
    d = core(ground(CAM, Ground.FLAT, age=2.0), ground(DEPTH, Ground.FLAT, age=2.0)).decision
    assert (d.speed_limit, d.reason) == (LIM.slow, "Ground sensors not available, slowing down")
    assert not d.stop


def test_untrusted_obstacle_is_ignored():
    d = core(
        ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT), obstacle(0.3, trust=0.1)
    ).decision
    assert not d.stop


def test_ramp_slows_down_on_wheels():
    d = core(ground(CAM, Ground.RAMP), ground(DEPTH, Ground.RAMP, 1.0)).decision
    assert (d.mode, d.reason) == (Mode.WHEELS, "Ramp ahead, slowing down")


# ---- the first tick


def test_the_first_tick_decides_before_any_imu_report():
    c = DecisionCore()
    for r in (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)):
        c.on_ground(r)
    d = c.tick(NOW)  # no motion report yet: the tilt is unknown, which is no hazard on wheels
    assert (d.mode, d.stop) == (Mode.WHEELS, False)


def test_a_stop_on_the_first_tick_keeps_the_wheels():
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.3)).decision
    assert (d.mode, d.stop) == (Mode.WHEELS, True)


def test_manual_mode_before_any_mode_is_chosen_stays_on_wheels():
    c = DecisionCore()
    c.on_command(Command.MANUAL, 0.9)
    d = fed(c, (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)), NOW)
    assert (d.mode, d.reason) == (Mode.WHEELS, "Manual mode")


# ---- reports that arrive late (#109)


def test_an_older_report_arriving_late_does_not_replace_a_newer_one():
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.3))
    assert c.decision.stop
    c.on_ground(GroundReport(NOW - 0.4, 0.9, DEPTH, Ground.FLAT, 3.0))  # older, arrives later
    assert c.tick(NOW).stop
    c.on_obstacle(obstacle(0.5))
    c.on_obstacle(ObstacleReport(NOW - 0.4, 0.9, LIDAR, 3.0))
    assert c.obstacles[LIDAR].distance_m == 0.5
    c.on_motion(MotionReport(NOW - 0.4, 0.9, pitch_rad=0.3))
    assert c.motion.pitch_rad == 0.0
    c.on_position(gps())
    c.on_position(PositionReport(NOW - 0.4, 0.2, PositionSource.GPS, 50.0))
    assert c.positions[PositionSource.GPS].trust == 0.9
    c.on_link(wifi())
    c.on_link(LinkReport(NOW - 0.4, 0.9, Link.WIFI, False, 5))
    assert c.links[Link.WIFI].connected


def test_after_the_clock_goes_back_new_reports_are_used_again():
    # a Gazebo world reset or a bag played again: the stamps start again near 0
    c = DecisionCore()
    c.on_motion(MotionReport(500.0, 0.9))
    for source in (CAM, DEPTH):
        c.on_ground(GroundReport(500.0, 0.9, source, Ground.FLAT, 3.0))
    c.tick(500.0)
    c.on_motion(MotionReport(5.0, 0.9))
    c.on_ground(GroundReport(5.0, 0.9, DEPTH, Ground.DROP, 0.3))
    assert c.tick(5.0).stop
    assert c.motion.stamp == 5.0


def test_after_the_clock_goes_back_a_little_a_drop_still_stops_the_chair():
    # a reset from 3.0 s to 0.1 s: less than clock_back_s, as in a bag looped every few seconds
    c = DecisionCore()
    for i in range(31):
        t = i / 10
        for source in (CAM, DEPTH):
            c.on_ground(GroundReport(t, 0.9, source, Ground.FLAT, 3.0))
        c.on_motion(MotionReport(t, 0.9))
        c.tick(t)
    after = []
    for i in range(1, 41):  # a drop 0.3 m ahead, reported every 0.5 s
        t = round(i / 10, 2)
        if i % 5 == 1:
            c.on_ground(GroundReport(t, 0.9, DEPTH, Ground.DROP, 0.3))
            c.on_ground(GroundReport(t, 0.9, CAM, Ground.FLAT, 3.0))
            c.on_motion(MotionReport(t, 0.9))
        after.append((t, c.tick(t)))
    assert all(d.speed_limit < 1.0 for _, d in after)  # never on the reports from before
    assert all(d.stop for t, d in after if t >= 0.6)


def test_a_report_stamped_well_ahead_of_the_clock_is_not_used():
    c = DecisionCore()
    for source in (CAM, DEPTH):
        c.on_ground(GroundReport(NOW + 10.0, 0.9, source, Ground.FLAT, 3.0))
    c.on_motion(level())
    d = c.tick(NOW)  # the reports held come from a clock that has since gone back
    assert d.reason == "Ground sensors not available, slowing down"


def test_a_report_with_the_same_stamp_replaces_the_one_held_even_untrusted():
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.3))
    c.on_ground(GroundReport(NOW, 0.2, DEPTH, Ground.FLAT, 3.0))  # the newest frame counts
    assert c.ground[DEPTH].trust == 0.2


# ---- distances from the device's front, not from each sensor (#96)

CHAIR = Mount(
    front_wheels_m=0.45, front_tracks_m=1.1, camera_m=0.35, depth_camera_m=0.35, lidar_m=0.4
)


def mounted(*reports, mode=Mode.WHEELS, at=NOW):
    c = DecisionCore(mount=CHAIR)
    c.mode = mode
    for r in reports:
        (c.on_obstacle if isinstance(r, ObstacleReport) else c.on_ground)(r)
    c.on_motion(level())
    c.tick(at)
    return c


def test_a_drop_past_the_edge_from_the_camera_but_within_it_from_the_front_stops():
    # on wheels the front is 0.10 m ahead of the 3D camera
    reports = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.55))
    assert not core(*reports).decision.stop  # measured from the sensor: 0.55 m
    c = mounted(*reports)
    assert c.decision.stop and c.ground[DEPTH].distance_m == pytest.approx(0.45)


def test_on_tracks_the_front_is_further_ahead():
    c = mounted(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 1.2), mode=Mode.TRACKS)
    assert c.ground[DEPTH].distance_m == pytest.approx(0.45) and c.decision.stop


def test_while_the_slides_move_the_longer_front_counts():
    c = mounted(*stairs(1.5))  # changes to tracks on this tick
    assert c.decision.mode == Mode.TRACKS
    c.mode = Mode.WHEELS  # as if it had just changed back
    c.mode_changed_at = NOW
    c.on_ground(GroundReport(NOW + 0.1, 0.9, DEPTH, Ground.DROP, 1.2))
    assert c.ground[DEPTH].distance_m == pytest.approx(0.45)  # the tracks' front, 0.75 m ahead


def test_an_obstacle_from_the_lidar_is_measured_from_the_front_too():
    c = mounted(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT), obstacle(0.85))
    assert c.obstacles[LIDAR].distance_m == pytest.approx(0.80) and c.decision.stop


def test_an_unknown_distance_stays_unknown_and_a_hazard_under_the_front_is_at_0_m():
    c = mounted(ground(CAM, Ground.STAIRS_UP), ground(DEPTH, Ground.DROP, 0.05))
    assert math.isnan(c.ground[CAM].distance_m)
    assert c.ground[DEPTH].distance_m == 0.0


def test_with_no_mount_the_distances_are_as_reported():
    c = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 0.55))
    assert c.ground[DEPTH].distance_m == 0.55


# ---- the stop distances hold where the device stands still, not where the stop is decided

BRAKES = Mount(top_mps=1.2, reaction_s=0.4, decel_mps2=3.0)


def test_the_travel_is_the_reaction_then_the_braking():
    assert BRAKES.travel_m(1.0) == pytest.approx(1.2 * 0.4 + 1.2**2 / 6)  # 0.72 m
    assert BRAKES.travel_m(0.3) == pytest.approx(0.36 * 0.4 + 0.36**2 / 6)  # 0.17 m
    assert Mount(top_mps=1.2, reaction_s=0.4).travel_m(1.0) == pytest.approx(0.48)  # no braking
    assert Mount().travel_m(1.0) == 0.0


def braked(*steps):
    """A core with BRAKES, ticked once per (drop distance, time) step."""
    c = DecisionCore(mount=BRAKES)
    for distance, at in steps:
        c.on_ground(GroundReport(at, 0.9, CAM, Ground.FLAT))
        c.on_ground(GroundReport(at, 0.9, DEPTH, Ground.DROP, distance))
        c.on_motion(MotionReport(at, 0.9))
        c.tick(at)
    return c


def test_at_full_speed_a_drop_the_device_cannot_stop_short_of_stops_it_now():
    assert not core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.DROP, 1.0)).decision.stop
    assert braked((1.0, NOW)).decision.stop  # 0.5 m + 0.72 m


def test_slowed_down_the_same_drop_does_not_stop_it_yet():
    c = braked((1.9, NOW), (1.0, NOW + 0.1))
    assert not c.decision.stop and c.decision.speed_limit == pytest.approx(0.3)
    assert braked((1.9, NOW), (1.0, NOW + 0.1), (0.6, NOW + 0.2)).decision.stop  # 0.5 + 0.17 m


def test_once_stopped_the_travel_of_the_last_speed_still_counts():
    # standing still, the speed limit is 0: the stop must not release because the travel at 0 is 0
    c = braked((1.0, NOW), (1.1, NOW + 0.1), (1.2, NOW + 0.2))
    assert c.decision.stop and c.moving_speed == 1.0


# ---- position and alert link


def gps(age=0.0, trust=0.9):
    return PositionReport(NOW - age, trust, PositionSource.GPS, 3.0)


def wifi(connected=True, missed=0, age=0.0, trust=0.9):
    return LinkReport(NOW - age, trust, Link.WIFI, connected, missed)


STALE = LIM.link_max_age_s + 0.5  # older than a link or position report may be


def with_reports(*reports, ground_reports=(), commands=()):
    c = DecisionCore()
    for command in commands:
        c.on_command(command, 1.0)
    for r in ground_reports or (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)):
        c.on_ground(r)
    for r in reports:
        (c.on_position if isinstance(r, PositionReport) else c.on_link)(r)
    c.on_motion(level())
    c.tick(NOW)
    return c.decision


def test_a_host_without_position_or_link_modules_is_not_slowed_down():
    assert with_reports().reason == "Level ground"


def test_a_fresh_position_and_a_working_link_keep_full_speed():
    d = with_reports(gps(), wifi())
    assert (d.speed_limit, d.reason) == (1.0, "Level ground")


@pytest.mark.parametrize("position", [gps(age=STALE), gps(trust=0.2)], ids=["stale", "untrusted"])
def test_a_lost_position_slows_down(position):
    d = with_reports(position, wifi())
    assert (d.mode, d.stop, d.speed_limit) == (Mode.WHEELS, False, LIM.slow)
    assert (d.reason, d.priority) == ("Position unknown, slowing down", Priority.WARNING)


def test_any_one_fresh_position_source_is_enough():
    lidar_map = PositionReport(NOW, 0.9, PositionSource.LIDAR_MAP, 0.5)
    assert with_reports(gps(age=STALE), lidar_map, wifi()).reason == "Level ground"


def test_reports_once_a_second_keep_full_speed_between_them():
    # GPS and the heartbeats come about once a second, so a report 0.8 s old is still current
    assert with_reports(gps(age=0.8), wifi(age=0.8)).reason == "Level ground"


def test_odometry_alone_counts_as_a_lost_position():
    odometry = PositionReport(NOW, 0.9, PositionSource.ODOMETRY, 0.2)
    assert with_reports(gps(age=STALE), odometry, wifi()).reason == "Position unknown, slowing down"


def test_one_heartbeat_short_of_the_limit_still_carries_alerts():
    assert with_reports(gps(), wifi(missed=LIM.missed_heartbeats - 1)).reason == "Level ground"


@pytest.mark.parametrize(
    "link",
    [wifi(connected=False), wifi(missed=LIM.missed_heartbeats), wifi(age=STALE), wifi(trust=0.2)],
    ids=["disconnected", "missed heartbeats", "stale", "untrusted"],
)
def test_no_alert_link_slows_down(link):
    d = with_reports(gps(), link)
    assert (d.speed_limit, d.reason) == (LIM.slow, "No alert link, slowing down")


def test_bluetooth_carries_the_alerts_when_wifi_is_gone():
    bluetooth = LinkReport(NOW, 0.9, Link.BLUETOOTH, True, 0)
    assert with_reports(gps(), wifi(connected=False), bluetooth).reason == "Level ground"


def test_a_hazard_is_named_before_a_lost_link():
    ramp = (ground(CAM, Ground.RAMP), ground(DEPTH, Ground.RAMP, 1.0))
    d = with_reports(gps(age=STALE), wifi(connected=False), ground_reports=ramp)
    assert d.reason == "Ramp ahead, slowing down"


def test_a_lost_link_and_position_never_stop_the_device():
    d = with_reports(gps(age=STALE), wifi(connected=False))
    assert not d.stop and d.reason == "No alert link, slowing down"


def test_the_users_stop_still_wins_with_link_and_position_lost():
    d = with_reports(gps(age=STALE), wifi(connected=False), commands=[Command.STOP])
    assert (d.stop, d.reason) == (True, "Stopped by you")


def test_manual_mode_follows_the_user_with_the_link_lost():
    # in manual mode the user drives, so the warning waits until MANA is in control again
    d = with_reports(gps(), wifi(connected=False), commands=[Command.MANUAL])
    assert (d.reason, d.speed_limit) == ("Manual mode", 1.0)


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
        ("PositionReport", "source", PositionSource),
        ("LinkReport", "link", Link),
        ("UserCommand", "command", Command),
        ("DriveRequest", "mode", Mode),
        ("UserMessage", "priority", Priority),
    ],
)
def test_codes_match_the_message_types(msg, field, enum):
    assert constants(msg, field) == {e.name: e.value for e in enum}


# ---- stairs down: stop at the top, go down only after a yes


def stairs_down(distance=1.0):
    return ground(CAM, Ground.STAIRS_DOWN), ground(DEPTH, Ground.STAIRS_DOWN, distance)


def fed(c, reports, at, motion=None):
    for r in reports:
        c.on_ground(GroundReport(at, r.trust, r.source, r.ground, r.distance_m))
    c.on_motion(MotionReport(at, 0.9, pitch_rad=motion or 0.0))
    return c.tick(at)


def test_stairs_down_seen_by_two_sensors_stop_and_ask():
    d = core(*stairs_down()).decision
    assert (d.stop, d.reason, d.priority) == (True, ASK_DOWN, Priority.URGENT)


def test_stairs_down_seen_by_one_sensor_only_slow_down():
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_DOWN, 1.0)).decision
    assert (d.stop, d.reason) == (False, "Possible step ahead, slowing down")


def test_yes_goes_down_on_tracks_and_no_stays_at_the_top():
    c = core(*stairs_down())
    c.on_command(Command.NO, 0.9)
    assert fed(c, stairs_down(), NOW + 0.1).reason == ASK_DOWN
    c.on_command(Command.YES, 0.9)
    d = fed(c, stairs_down(), NOW + 0.2)
    assert (d.mode, d.stop, d.speed_limit) == (Mode.TRACKS, False, LIM.slow)
    assert d.reason == "Going down the stairs on tracks"


def test_an_early_or_unsure_yes_does_not_approve_the_stairs():
    c = core()
    c.on_command(Command.YES, 0.9)  # nothing was asked yet
    assert fed(c, stairs_down(), NOW + 0.1).reason == ASK_DOWN
    c.on_command(Command.YES, 0.2)  # heard, but not trusted
    assert fed(c, stairs_down(), NOW + 0.2).stop


def test_the_next_stairs_down_are_asked_again_once_level_at_the_bottom():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    assert fed(c, stairs_down(), NOW + 0.1, motion=-0.4).mode == Mode.TRACKS  # going down
    fed(c, flat, NOW + 1.0)  # at the bottom, level
    assert fed(c, flat, NOW + 1.0 + LIM.level_hold_s).reason == "Level ground"
    assert fed(c, stairs_down(), NOW + 4.0).reason == ASK_DOWN


def test_stairs_up_still_change_to_tracks_without_asking():
    assert core(*stairs()).decision.reason == "Stairs ahead, changing to tracks"


def test_mixed_step_labels_with_stairs_down_still_ask():
    # the camera names the ground but measures no distance
    d = core(ground(CAM, Ground.STAIRS_DOWN), ground(DEPTH, Ground.CURB, 1.0)).decision
    assert (d.stop, d.reason) == (True, ASK_DOWN)


def test_a_no_after_the_yes_takes_it_back():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    assert fed(c, stairs_down(), NOW + 0.1).mode == Mode.TRACKS
    c.on_command(Command.NO, 0.9)
    assert fed(c, stairs_down(), NOW + 0.2).reason == ASK_DOWN


def test_a_yes_lapses_once_the_stairs_are_out_of_sight_even_without_level_ground():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    for i in range(1, 20):  # no motion reports, so level ground is never confirmed
        t = NOW + i / 10
        for r in flat:
            c.on_ground(GroundReport(t, r.trust, r.source, r.ground, r.distance_m))
        c.tick(t)
    assert fed(c, stairs_down(), NOW + 2.0).reason == ASK_DOWN


def test_a_yes_is_kept_when_the_stairs_drop_out_of_sight_for_a_moment():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    assert fed(c, stairs_down(), NOW + 0.1).mode == Mode.TRACKS
    fed(c, (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT)), NOW + 0.2)  # one lost frame
    assert fed(c, stairs_down(), NOW + 0.3).reason == "Going down the stairs on tracks"


def test_a_yes_heard_before_the_first_tick_does_not_approve_the_stairs():
    c = DecisionCore()
    c.on_command(Command.YES, 0.9)  # nothing has been asked: the core has not decided yet
    assert fed(c, stairs_down(), NOW).reason == ASK_DOWN


def test_the_question_holds_through_one_lost_report_and_a_yes_then_still_counts():
    c = core(*stairs_down())
    one_sensor = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_DOWN, 1.0))
    assert fed(c, one_sensor, NOW + 0.1).reason == ASK_DOWN  # was "Possible step ahead"
    c.on_command(Command.YES, 0.9)
    assert fed(c, stairs_down(), NOW + 0.2).reason == "Going down the stairs on tracks"


def test_the_question_holds_while_the_stairs_read_just_past_the_step_distance():
    c = core(*stairs_down(LIM.step_m - 0.1))
    assert c.decision.reason == ASK_DOWN
    just_past = stairs_down(LIM.step_m + 0.05)  # within release_m of step_m
    assert all(d.reason == ASK_DOWN for d in ticks(c, just_past, 0.1, 2.0))
    c.on_command(Command.YES, 0.9)
    assert fed(c, just_past, NOW + 2.1).reason == "Going down the stairs on tracks"


def test_a_yes_just_after_the_question_still_answers_it():
    # one reading can end the question a moment before the user's yes is heard
    c = core(*stairs_down())
    one_sensor = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_DOWN, 1.0))
    for t in (0.1, 0.6, 1.1):  # longer than hold_s, so the question has ended
        fed(c, one_sensor, NOW + t)
    assert c.decision.reason != ASK_DOWN
    c.on_command(Command.YES, 0.9)
    assert fed(c, stairs_down(), NOW + 1.2).reason == "Going down the stairs on tracks"


def test_a_yes_long_after_the_question_does_not_answer_it():
    c = core(*stairs_down())
    flat = (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.FLAT))
    for t in (0.5, 1.0, 1.5, 2.0, 2.5):
        fed(c, flat, NOW + t)
    c.on_command(Command.YES, 0.9)  # more than answer_s after it was last asked
    assert fed(c, stairs_down(), NOW + 2.6).reason == ASK_DOWN


def test_stop_then_go_asks_again():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    c.on_command(Command.STOP, 0.9)
    c.on_command(Command.GO, 0.9)
    assert fed(c, stairs_down(), NOW + 0.1).reason == ASK_DOWN


@pytest.mark.parametrize(
    "command", [Command.SPEED_UP, Command.SLOW_DOWN, Command.WHEELS, Command.MANUAL]
)
def test_a_driving_command_at_the_question_does_not_drive_on(command):
    c = core(*stairs_down())
    c.on_command(command, 0.9)
    d = fed(c, stairs_down(), NOW + 0.1)
    assert (d.stop, d.reason) == (True, ASK_DOWN)


@pytest.mark.parametrize(
    ("before", "after"),
    [([Command.MANUAL], []), ([], [Command.SPEED_UP]), ([], [Command.WHEELS])],
    ids=["manual then yes", "yes then speed up", "yes then wheels"],
)
def test_after_a_yes_the_stairs_are_gone_down_on_tracks_whatever_the_mode(before, after):
    c = core()
    for command in before:
        c.on_command(command, 0.9)
    assert fed(c, stairs_down(), NOW + 0.1).reason == ASK_DOWN
    c.on_command(Command.YES, 0.9)
    for command in after:
        c.on_command(command, 0.9)
    d = fed(c, stairs_down(), NOW + 0.2)
    assert (d.mode, d.stop, d.speed_limit) == (Mode.TRACKS, False, LIM.slow)
    assert d.reason == "Going down the stairs on tracks"


def test_an_unsure_no_still_takes_the_yes_back():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    c.on_command(Command.NO, 0.2)
    assert fed(c, stairs_down(), NOW + 0.1).reason == ASK_DOWN


def test_stairs_down_seen_by_one_sensor_stop_at_the_edge_like_a_drop():
    # StairNet has no stairs down, so often only the 3D camera sees them: a fall needs no second
    # opinion, so they stop at the edge instead of rolling on slowly
    d = core(ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_DOWN, 0.4)).decision
    assert (d.stop, d.reason) == (True, "Drop ahead, stopping")


@pytest.mark.parametrize("distance", [0.4, 0.0])
def test_after_a_yes_confirmed_stairs_down_are_gone_down_also_at_the_edge(distance):
    # the edge stop for stairs down is for one sensor; a confirmed yes must still go down
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    d = fed(c, stairs_down(distance), NOW + 0.1)
    assert (d.mode, d.stop, d.speed_limit) == (Mode.TRACKS, False, LIM.slow)
    assert d.reason == "Going down the stairs on tracks"


def test_confirmed_stairs_down_first_seen_at_the_edge_stop_and_ask():
    # not "Drop ahead": that would leave no question to say yes to
    d = core(*stairs_down(0.4)).decision
    assert (d.stop, d.reason, d.priority) == (True, ASK_DOWN, Priority.URGENT)


def test_a_drop_at_the_edge_stops_even_after_a_yes_to_stairs_down():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    d = fed(c, (*stairs_down(1.0), ground(DEPTH, Ground.DROP, 0.4)), NOW + 0.1)
    assert (d.stop, d.reason) == (True, "Drop ahead, stopping")


def test_after_a_yes_stairs_down_seen_by_one_sensor_at_the_edge_still_stop():
    c = core(*stairs_down())
    c.on_command(Command.YES, 0.9)
    d = fed(c, (ground(CAM, Ground.FLAT), ground(DEPTH, Ground.STAIRS_DOWN, 0.4)), NOW + 0.1)
    assert (d.stop, d.reason) == (True, "Drop ahead, stopping")


# ---- a step that only one sensor sees (#103): stop at the edge, as in the dark


@pytest.mark.parametrize("kind", [Ground.STAIRS_UP, Ground.CURB])
def test_a_step_one_sensor_measures_at_the_edge_stops_the_chair(kind):
    # the camera cannot be trusted in the dark, so only the 3D camera sees the step
    d = core(ground(CAM, Ground.FLAT, trust=0.2), ground(DEPTH, kind, 0.4)).decision
    assert (d.stop, d.reason, d.priority) == (True, STEP_STOP, Priority.URGENT)
    assert d.mode == Mode.WHEELS  # one sensor is still not enough to change mode


def test_a_lone_step_further_than_the_edge_only_slows_down():
    d = core(ground(CAM, Ground.FLAT, trust=0.2), ground(DEPTH, Ground.STAIRS_UP, 1.0)).decision
    assert (d.stop, d.reason) == (False, "Possible step ahead, slowing down")


def test_a_lone_step_without_a_distance_only_slows_down():
    # the picture of stairs: the camera alone, and it measures no distance
    d = core(ground(CAM, Ground.STAIRS_UP), ground(DEPTH, Ground.FLAT)).decision
    assert (d.stop, d.reason) == (False, "Possible step ahead, slowing down")


def test_a_confirmed_step_at_the_edge_still_changes_to_tracks():
    d = core(*stairs(0.4)).decision
    assert (d.mode, d.stop) == (Mode.TRACKS, False)


def test_already_on_tracks_a_lone_step_does_not_stop():
    c = core(*stairs(1.5))
    assert c.decision.mode == Mode.TRACKS
    c.on_ground(GroundReport(NOW + 0.1, 0.2, CAM, Ground.STAIRS_UP))  # the camera goes dark
    c.on_ground(GroundReport(NOW + 0.1, 0.9, DEPTH, Ground.STAIRS_UP, 0.4))
    c.on_motion(MotionReport(NOW + 0.1, 0.9))
    assert not c.tick(NOW + 0.1).stop


def test_the_user_can_cross_a_lone_step_on_tracks_in_manual_mode():
    c = core(ground(CAM, Ground.FLAT, trust=0.2), ground(DEPTH, Ground.STAIRS_UP, 0.4))
    assert c.decision.stop
    c.on_command(Command.TRACKS, 0.9)  # touch or button: manual mode, on tracks
    c.on_ground(GroundReport(NOW + 0.1, 0.9, DEPTH, Ground.STAIRS_UP, 0.4))
    c.on_motion(MotionReport(NOW + 0.1, 0.9))
    d = c.tick(NOW + 0.1)
    assert (d.mode, d.stop, d.reason) == (Mode.TRACKS, False, "Manual mode")


def test_manual_mode_on_wheels_still_stops_at_a_lone_step():
    c = DecisionCore()
    c.on_command(Command.SPEED_UP, 0.9)  # manual mode, on wheels
    c.on_ground(ground(DEPTH, Ground.CURB, 0.3))
    c.on_motion(level())
    assert c.tick(NOW).reason == STEP_STOP
