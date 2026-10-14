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
    RadioLink,
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


def test_there_are_nine_sensors_for_a_three_by_three_grid():
    assert len(SENSORS) == 8
    ids = "camera motion gps microphone wifi bluetooth depth lidar".split()
    assert [s.id for s in SENSORS] == ids


def test_depth_and_lidar_are_never_available_to_a_browser():
    sensors = by_id(Middleware().tick(1.0))
    for sid in ("depth", "lidar"):
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


def test_stairs_from_the_classifier_and_the_detector_only_slow_down():
    m = Middleware()  # both models use the phone's one camera, so they count as one sensor
    m.on_ground(Source.CLASSIFIER, Ground.STAIRS_UP, 0.9, 1.5, now=1.0)
    m.on_ground(Source.DETECTOR, Ground.STAIRS_UP, 0.9, 1.5, now=1.0)
    flat(m, 1.0)
    d = m.tick(1.0)["decision"]
    assert d["mode"] == "WHEELS" and d["speed"] == pytest.approx(0.3)


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


# ---- which radio carries the alerts


def test_wifi_present_means_wifi():
    assert RadioLink().update(True, True, 0.0) == "wifi"


def test_the_browser_not_being_able_to_tell_is_unknown_not_a_guess():
    assert RadioLink().update(None, None, 0.0) == "unknown"


def test_losing_wifi_for_two_seconds_moves_alerts_to_bluetooth():
    r = RadioLink()
    r.update(True, True, 0.0)
    assert r.update(False, True, 1.0) == "wifi"
    assert r.update(False, True, 3.0) == "bluetooth"


def test_with_wifi_and_bluetooth_both_off_there_is_no_link():
    r = RadioLink()
    r.update(True, True, 0.0)
    r.update(False, False, 1.0)
    assert r.update(False, False, 3.5) == "none"


def test_bluetooth_coming_on_while_wifi_is_gone_gives_a_link_again():
    r = RadioLink()
    r.update(True, False, 0.0)
    r.update(False, False, 1.0)
    assert r.update(False, False, 4.0) == "none"
    assert r.update(False, True, 5.0) == "bluetooth"


def test_wifi_must_be_back_for_five_seconds_before_alerts_return_to_it():
    r = RadioLink()
    r.update(True, True, 0.0)
    r.update(False, True, 1.0)
    r.update(False, True, 3.5)
    assert r.state == "bluetooth"
    assert r.update(True, True, 4.0) == "bluetooth"
    assert r.update(True, True, 8.9) == "bluetooth"
    assert r.update(True, True, 9.1) == "wifi"


def test_a_flickering_wifi_does_not_flip_the_link():
    r = RadioLink()
    r.update(True, True, 0.0)
    for t in range(1, 30):
        r.update(bool(t % 2), True, float(t))
    assert r.state == "wifi"


def test_a_radio_that_is_on_is_active_and_one_that_is_off_is_not_available():
    m = Middleware()
    m.on_radio(False, True, now=1.0)
    tiles = by_id(m.tick(1.1))
    assert (tiles["wifi"]["state"], tiles["wifi"]["reason"]) == ("unavailable", "Not available")
    assert (tiles["bluetooth"]["state"], tiles["bluetooth"]["value"]) == ("active", "on")


def test_bluetooth_turned_off_says_so_and_comes_back_when_it_is_on_again():
    m = Middleware()
    m.on_radio(True, False, now=1.0)
    tiles = by_id(m.tick(1.1))
    assert (tiles["bluetooth"]["state"], tiles["bluetooth"]["reason"]) == (
        "unavailable",
        "Turned off",
    )
    m.on_radio(True, True, now=2.0)
    assert by_id(m.tick(2.1))["bluetooth"]["state"] == "active"


def test_what_the_user_says_into_the_microphone_is_kept_as_text():
    m = Middleware()
    m.on_heard("stop the chair", now=1.0)
    m.on_word("stop", 0.9, now=1.0, record=False)  # the same words, not written down twice
    m.on_command(Command.MANUAL, now=1.5, source="button")  # a button is not what was said
    assert m.tick(1.6)["inputs"] == [{"t": 0.0, "text": "stop the chair", "command": "Stop"}]


def test_a_recognised_word_without_a_phrase_is_written_down_once():
    m = Middleware()
    m.on_word("banana", 0.9, now=1.0)
    assert m.tick(1.1)["inputs"] == [{"t": 0.0, "text": "banana", "command": ""}]


def run_ticks(m, start, seconds, step=0.1, **kw):
    t = start
    while t < start + seconds:
        m.tick(t)
        t += step
    return t


def texts(m, kind=None):
    return [e["text"] for e in m.tick(m.clock())["decisions"] if kind in (None, e["kind"])]


def test_the_decision_log_says_stop_and_go_in_words():
    m = Middleware(clock=lambda: 10.0)
    m.tick(0.0)
    m.on_command(Command.STOP, now=1.0, source="button")
    m.tick(1.1)
    m.on_command(Command.GO, now=2.0, source="button")
    m.tick(2.1)
    log = texts(m, "decision")
    assert "MANA deactivated. You are fully in control." in log
    assert any(t.startswith("MANA reactivated.") for t in log)


def test_the_decision_log_says_when_alerts_switch_to_bluetooth():
    m = Middleware(clock=lambda: 10.0)
    m.on_radio(True, True, now=0.0)
    m.tick(0.0)
    m.on_radio(False, True, now=1.0)
    t = run_ticks(m, 1.0, 4.0)
    assert "Wi-Fi is not available. Switching alerts to Bluetooth." in texts(m, "link")
    m.on_radio(True, True, now=t)
    run_ticks(m, t, 7.0)
    assert "Wi-Fi is available again. Alerts use Wi-Fi." in texts(m, "link")


def test_the_decision_log_says_when_a_sensor_goes_off():
    m = Middleware(clock=lambda: 10.0)
    m.tick(0.0)  # everything starts idle
    t = 0.1
    while t < 2.0:  # the phone keeps sending motion
        m.on_motion(0.0, 0.0, 9.81, now=t)
        m.tick(t)
        t += 0.1
    assert "Motion is available. MANA can check tilt and rough ground." in texts(m, "switch")
    run_ticks(m, t, 6.0)  # nothing arrives any more
    assert "Motion is not available. MANA cannot check tilt or rough ground." in texts(m, "switch")


def test_the_decision_sentence_is_in_the_snapshot():
    m = Middleware()
    snap = m.tick(0.0)
    assert snap["decision"]["sentence"].startswith("Driving on wheels")


# ---- the snapshot


def test_snapshot_has_what_the_dashboard_needs():
    m = Middleware()
    m.on_heartbeat(15.0, now=1.0)
    snap = m.tick(1.0)
    assert set(snap) >= {"sensors", "messages", "decision", "rules", "position", "link", "latency"}
    assert len(snap["sensors"]) == 8
    assert snap["rules"][0] == "user stop"
    assert snap["link"]["state"] == "unknown"  # no radio report yet
    m.on_radio(True, True, now=1.0)
    assert m.tick(1.0)["link"]["state"] == "wifi"


def test_mode_enum_matches_the_snapshot_names():
    assert {m.name for m in Mode} == {"WHEELS", "TRACKS"}


def test_a_busy_stream_is_thinned_in_the_log_but_commands_are_always_kept():
    m = Middleware()
    for i in range(30):
        m.on_motion(0.0, 0.0, G, now=10.0 + i * 0.033)  # 30 readings in one second
    assert len([e for e in m.log if e["source"] == "motion"]) <= 3
    m.on_command(Command.STOP, 1.0, now=11.0)
    m.on_command(Command.GO, 1.0, now=11.01)
    assert [e["value"] for e in m.log if e["kind"] == "command"] == ["STOP", "GO"]


def test_log_and_snapshot_times_are_seconds_since_the_first_message():
    m = Middleware()
    m.on_gps(6.0, now=5000.0)
    m.on_gps(6.0, now=5002.0)
    assert [e["t"] for e in m.log] == [0.0, 2.0]
    assert m.tick(5003.0)["time"] == 3.0


def test_a_recogniser_that_cannot_listen_says_why_and_listening_clears_it():
    m = Middleware()
    m.on_microphone_state("network", now=1.0)
    tile = by_id(m.tick(1.1))["microphone"]
    assert (tile["state"], tile["reason"]) == ("unavailable", "Needs internet")
    m.on_microphone_state("listening", now=2.0)
    assert by_id(m.tick(2.1))["microphone"]["state"] == "active"


def test_the_action_box_lines_hold_every_decision_in_force():
    m = Middleware(clock=lambda: 10.0)
    m.on_radio(True, True, now=0.0)
    m.tick(0.0)
    m.on_radio(False, True, now=1.0)
    t = run_ticks(m, 1.0, 4.0)
    lines = m.tick(t)["decision"]["lines"]
    assert lines[0].startswith("Driving on wheels")
    assert "Wi-Fi is not available. Switching alerts to Bluetooth." in lines
    assert not any(line == "Wi-Fi is not available." for line in lines)  # not said twice


def test_a_phrase_being_spoken_fills_the_live_line_until_it_is_final():
    m = Middleware()
    m.on_heard("sto", now=1.0, final=False)
    assert m.tick(1.1)["speaking"] == "sto"
    m.on_heard("stop", now=1.5, final=True)
    snap = m.tick(1.6)
    assert snap["speaking"] == ""
    assert snap["inputs"][-1]["text"] == "stop"


def test_switching_a_sensor_off_is_answered_at_once_in_words():
    m = Middleware(clock=lambda: 10.0)
    m.tick(0.0)
    m.on_motion(0.0, 0.0, 9.81, now=0.5)
    m.tick(0.6)
    m.on_sensor_switch("motion", False, now=0.7)
    snap = m.tick(0.8)
    tile = by_id(snap)["motion"]
    assert (tile["state"], tile["value"]) == ("idle", "Switched off")
    assert any("Motion is not available" in line for line in snap["decision"]["lines"])
    assert not any("by you" in line for line in snap["decision"]["lines"])
    assert [e["kind"] for e in snap["decisions"] if e["text"].startswith("Motion")] == ["switch"]


def test_switching_gps_off_moves_the_position_to_the_indoor_map():
    m = Middleware(clock=lambda: 10.0)
    m.on_gps(5.0, now=0.0)
    m.tick(0.0)
    m.on_sensor_switch("gps", False, now=1.0)
    run_ticks(m, 1.0, 4.0)
    assert m.tick(5.1)["position"]["source"] == "indoor"


def test_a_sensor_that_comes_back_is_said_to_be_available():
    m = Middleware(clock=lambda: 10.0)
    m.tick(0.0)
    m.on_sensor_switch("camera", False, now=0.1)
    m.on_sensor_switch("camera", True, now=1.0)
    t = 1.1
    while t < 3.0:
        m.on_light(0.5, now=t)
        m.tick(t)
        t += 0.1
    assert "Camera is available. MANA can see obstacles and the ground." in texts(m, "switch")


def test_losing_wifi_is_said_once_not_three_times():
    m = Middleware(clock=lambda: 10.0)
    m.on_radio(True, True, now=0.0)
    m.tick(0.0)
    t = 0.1
    while t < 8.0:  # the browser keeps saying wifi is gone, and flickers once
        m.on_radio(False if round(t, 1) != 3.0 else None, True, now=t)
        m.tick(t)
        t += 0.1
    said = [x for x in texts(m) if "Wi-Fi" in x and "not available" in x]
    assert said == ["Wi-Fi is not available. Switching alerts to Bluetooth."]


def test_bluetooth_available_comes_with_what_it_is_for():
    m = Middleware(clock=lambda: 10.0)
    m.tick(0.0)
    t = 0.1
    while t < 3.0:
        m.on_radio(True, True, now=t)
        m.tick(t)
        t += 0.1
    assert "Bluetooth is available. It is the backup link for alerts." in texts(m, "switch")
    assert "Wi-Fi is available. Alerts use Wi-Fi." in texts(m, "link")
