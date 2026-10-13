"""Tests for the phone middleware: sensor states, the reports it builds, the position and link
rules, and the decisions that come out. No network and no ROS."""

import math

import pytest
from mana_core.decision import Command, Ground, Mode, Source

from demo.middleware import (
    SENSORS,
    LinkSelector,
    Middleware,
    PositionSource,
    State,
    camera_trust_factor,
    tilt,
)

G = 9.81


def flat(m, now):
    m.on_motion(0.0, 0.0, G, now)


def shake(m, start, seconds=1.0):
    t = start
    while t < start + seconds:
        m.on_motion(0.0, 0.0, G + (6.0 if int(t * 10) % 2 else -6.0), t)
        t = round(t + 0.1, 3)
    return t


def feed_flat(m, start, seconds):
    t = start
    while t < start + seconds:
        flat(m, t)
        m.tick(t)
        t = round(t + 0.1, 3)
    return t


def by_id(snapshot):
    return {s["id"]: s for s in snapshot["sensors"]}


# ---- the sensor panel


def test_there_are_eight_sensors_for_a_two_by_four_grid():
    assert len(SENSORS) == 8
    assert [s.id for s in SENSORS] == [
        "camera", "motion", "gps", "microphone", "link", "depth", "lidar", "temperature",
    ]  # fmt: skip


def test_depth_lidar_and_temperature_are_never_available_to_a_browser():
    sensors = by_id(Middleware().tick(1.0))
    for sid in ("depth", "lidar", "temperature"):
        assert sensors[sid]["state"] == State.UNAVAILABLE
        assert sensors[sid]["reason"]


def test_a_sensor_is_idle_until_data_arrives_then_active_then_idle_again():
    m = Middleware()
    assert by_id(m.tick(0.0))["motion"]["state"] == State.IDLE
    flat(m, 1.0)
    assert by_id(m.tick(1.1))["motion"]["state"] == State.ACTIVE
    assert by_id(m.tick(4.0))["motion"]["state"] == State.IDLE


def test_a_sensor_the_browser_lacks_is_unavailable_with_the_reason_given():
    m = Middleware()
    m.hello({"gps": {"available": False, "why": "Location is blocked"}})
    gps = by_id(m.tick(1.0))["gps"]
    assert (gps["state"], gps["reason"]) == (State.UNAVAILABLE, "Location is blocked")


def test_the_phone_cannot_make_an_unavailable_sensor_available():
    m = Middleware()
    m.hello({"lidar": {"available": True}})
    assert by_id(m.tick(1.0))["lidar"]["state"] == State.UNAVAILABLE


# ---- motion


def test_a_phone_lying_flat_has_no_tilt():
    assert tilt(0.0, 0.0, G) == (0.0, 0.0)


def test_a_tilted_phone_gives_the_angle():
    theta = math.radians(30)
    pitch, roll = tilt(G * math.sin(theta), 0.0, G * math.cos(theta))
    assert abs(pitch) == pytest.approx(theta)
    assert roll == pytest.approx(0.0)


def test_level_ground_means_wheels_at_full_speed():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=1.0)
    m.on_ground(Source.DETECTOR, Ground.FLAT, 0.9, now=1.0)
    flat(m, 1.0)
    d = m.tick(1.0)["decision"]
    assert (d["mode"], d["stop"], d["speed"], d["fired"]) == ("WHEELS", False, 1.0, "wheels")


def test_shaking_the_phone_changes_to_tracks_and_holding_it_still_goes_back():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=0.0)
    end = shake(m, 0.0)
    d = m.tick(end)["decision"]
    assert (d["mode"], d["reason"]) == ("TRACKS", "Wheel slip, changing to tracks")
    # keep the ground reports fresh while the phone is held still
    t = end
    while t < end + 4.0:
        m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=t)
        flat(m, t)
        d = m.tick(t)["decision"]
        t = round(t + 0.1, 3)
    assert d["mode"] == "WHEELS"


# ---- voice and buttons


def test_saying_stop_stops_at_once_and_go_resumes():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=1.0)
    flat(m, 1.0)
    assert m.on_word("Stop", 0.9, now=1.0) == Command.STOP
    d = m.tick(1.0)["decision"]
    assert (d["stop"], d["speed"], d["reason"], d["fired"]) == (
        True, 0.0, "Stopped by you", "user stop",
    )  # fmt: skip
    m.on_word("go", 0.9, now=1.1)
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=1.1)
    flat(m, 1.1)
    assert m.tick(1.1)["decision"]["stop"] is False


def test_a_word_that_is_not_a_command_changes_nothing():
    m = Middleware()
    assert m.on_word("banana", 0.95, now=1.0) is None
    assert len(m.log) == 0


def test_stop_to_decision_time_is_recorded():
    m = Middleware()
    m.on_command(Command.STOP, 1.0, now=5.0, source="button")
    snap = m.tick(5.05)
    assert snap["latency"]["voice_or_button_to_stop_ms"] == pytest.approx(50.0)


def test_manual_and_automatic_words_switch_control():
    m = Middleware()
    m.on_word("off", 0.9, now=1.0)
    assert m.tick(1.0)["manual"] is True
    m.on_word("on", 0.9, now=1.1)
    assert m.tick(1.1)["manual"] is False


# ---- camera detection and light


def test_a_person_close_ahead_stops_the_chair_and_one_further_away_slows_it():
    m = Middleware()
    m.on_obstacle("person", 0.7, 0.9, now=1.0)
    assert m.tick(1.0)["decision"]["reason"] == "Obstacle ahead, stopping"
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=1.0)
    m.on_obstacle("person", 1.5, 0.9, now=1.0)
    assert m.tick(1.0)["decision"]["reason"] == "Obstacle ahead, slowing down"


def test_in_the_dark_the_camera_detection_is_ignored():
    m = Middleware()
    m.on_light(0.0, now=1.0)
    m.on_obstacle("person", 0.7, 0.95, now=1.0)
    d = m.tick(1.0)["decision"]
    assert d["reason"] != "Obstacle ahead, stopping"


def test_light_trust_factor_runs_from_dark_to_bright():
    assert camera_trust_factor(0.0) == 0.0
    assert camera_trust_factor(0.2) == pytest.approx(0.6)
    assert camera_trust_factor(0.9) == 1.0


def test_stairs_from_the_classifier_and_the_detector_change_to_tracks():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.STAIRS_UP, 0.9, 1.5, now=1.0)
    m.on_ground(Source.DETECTOR, Ground.STAIRS_UP, 0.9, 1.5, now=1.0)
    flat(m, 1.0)
    d = m.tick(1.0)["decision"]
    assert (d["mode"], d["fired"]) == ("TRACKS", "step")


def test_one_vision_model_alone_does_not_change_mode():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.STAIRS_UP, 0.9, 1.5, now=1.0)
    m.on_ground(Source.DETECTOR, Ground.FLAT, 0.9, now=1.0)
    flat(m, 1.0)
    assert m.tick(1.0)["decision"]["mode"] == "WHEELS"


def test_when_every_source_goes_stale_the_chair_slows_down():
    m = Middleware()
    m.on_ground(Source.CLASSIFIER, Ground.FLAT, 0.9, now=1.0)
    flat(m, 1.0)
    d = m.tick(3.0)["decision"]
    assert d["speed"] == pytest.approx(0.3)
    assert d["reason"] == "Ground sensors not available, slowing down"


# ---- position source


def test_the_position_source_leaves_gps_after_three_bad_seconds():
    p = PositionSource()
    assert p.update(20.0, 0.0) == "gps"
    assert p.update(20.0, 2.9) == "gps"
    assert p.update(20.0, 3.0) == "indoor"


def test_the_position_source_returns_to_gps_only_after_five_good_seconds():
    p = PositionSource()
    p.update(20.0, 0.0)
    p.update(20.0, 3.0)
    assert p.source == "indoor"
    assert p.update(5.0, 4.0) == "indoor"
    assert p.update(5.0, 8.9) == "indoor"
    assert p.update(5.0, 9.0) == "gps"


def test_accuracy_between_the_two_levels_changes_nothing():
    p = PositionSource()
    for t in range(0, 20):
        assert p.update(12.0, float(t)) == "gps"


def test_a_flickering_signal_does_not_flip_the_source():
    p = PositionSource()
    for t in range(0, 30):
        p.update(20.0 if t % 2 else 5.0, float(t))
    assert p.source == "gps"


def test_no_gps_fix_counts_as_bad_accuracy():
    p = PositionSource()
    p.update(None, 0.0)
    assert p.update(None, 3.0) == "indoor"


# ---- link


def test_the_link_falls_back_after_two_missed_heartbeats():
    link = LinkSelector()
    link.heartbeat(0.0, 20.0)
    assert link.update(1.5) == "wifi"
    assert link.update(2.1) == "fallback"


def test_the_link_comes_back_after_five_good_seconds():
    link = LinkSelector()
    link.heartbeat(0.0, 20.0)
    link.update(2.5)
    assert link.state == "fallback"
    for t in (3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
        link.heartbeat(t, 20.0)
        link.update(t)
    assert link.state == "wifi"


def test_link_trust_falls_as_the_round_trip_grows():
    link = LinkSelector()
    link.heartbeat(0.0, 20.0)
    fast = link.trust(0.1)
    link.heartbeat(0.2, 400.0)
    assert fast == 1.0 and link.trust(0.3) < fast


def test_missing_heartbeats_give_zero_link_trust():
    link = LinkSelector()
    link.heartbeat(0.0, 20.0)
    assert link.trust(5.0) == 0.0


# ---- the snapshot


def test_snapshot_has_what_the_dashboard_needs():
    m = Middleware()
    m.on_heartbeat(15.0, now=1.0)
    snap = m.tick(1.0)
    assert set(snap) >= {"sensors", "messages", "decision", "rules", "position", "link", "latency"}
    assert len(snap["sensors"]) == 8
    assert snap["rules"][0] == "user stop"
    assert snap["link"]["state"] == "wifi"


def test_mode_enum_matches_the_snapshot_names():
    assert {m.name for m in Mode} == {"WHEELS", "TRACKS"}
