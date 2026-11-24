"""Tests for the laptop server: a fake phone and a fake dashboard talk to the real app over
WebSockets. They need aiohttp, so they are skipped where it is not installed."""

import asyncio
import io
import json

import pytest

pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from demo.server import build_app  # noqa: E402


def run(coro):
    return asyncio.run(coro)


async def snapshot_where(ws, predicate, tries=60):
    for _ in range(tries):
        msg = await asyncio.wait_for(ws.receive_json(), 3)
        if msg.get("type") == "snapshot" and predicate(msg):
            return msg
    raise AssertionError("the dashboard never saw the expected snapshot")


def sensor(snap, sid):
    return next(s for s in snap["sensors"] if s["id"] == sid)


async def with_clients(test, **app_kwargs):
    client = TestClient(TestServer(build_app(**app_kwargs)))
    await client.start_server()
    try:
        phone = await client.ws_connect("/ws/phone")
        dash = await client.ws_connect("/ws/dashboard")
        await test(client, phone, dash)
    finally:
        await client.close()


def jpeg(color=(200, 200, 200)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


def test_motion_from_the_phone_makes_the_motion_sensor_active_on_the_dashboard():
    async def go(client, phone, dash):
        await phone.send_json({"type": "motion", "ax": 0.0, "ay": 0.0, "az": 9.81})
        snap = await snapshot_where(dash, lambda s: sensor(s, "motion")["state"] == "active")
        assert snap["decision"]["fired"] != "user stop"

    run(with_clients(go))


def test_depth_and_lidar_stay_unavailable():
    async def go(client, phone, dash):
        snap = await snapshot_where(dash, lambda s: True)
        for sid in ("depth", "lidar"):
            assert sensor(snap, sid)["state"] == "unavailable"

    run(with_clients(go))


def test_stop_button_from_the_phone_stops_the_chair():
    async def go(client, phone, dash):
        await phone.send_json({"type": "button", "name": "stop"})
        snap = await snapshot_where(dash, lambda s: s["decision"]["stop"])
        assert snap["decision"]["reason"] == "Stopped by you"
        assert snap["decision"]["fired"] == "user stop"

    run(with_clients(go))


def test_the_spoken_word_stop_stops_the_chair_and_go_resumes():
    async def go(client, phone, dash):
        await phone.send_json({"type": "word", "word": "stop", "confidence": 0.9})
        await snapshot_where(dash, lambda s: s["decision"]["stop"])
        await phone.send_json({"type": "word", "word": "go", "confidence": 0.9})
        await snapshot_where(dash, lambda s: not s["decision"]["stop"])

    run(with_clients(go))


def test_in_a_spoken_sentence_only_stop_acts():
    """The browser sends the phrase, then each of its words: "where do I go" must not start the
    device, "stop, there is a wall" must stop it, and a lone "go" still works."""

    async def go(client, phone, dash):
        async def say(text):
            await phone.send_json({"type": "heard", "text": text, "final": True})
            for w in text.split():
                await phone.send_json({"type": "word", "word": w, "confidence": 0.9})

        await say("Stop, there is a wall.")
        await snapshot_where(dash, lambda s: s["decision"]["stop"])
        await say("where do I go")
        await say("I am on the second floor")
        for _ in range(5):  # still stopped a few ticks later
            snap = await snapshot_where(dash, lambda s: True)
            assert snap["decision"]["stop"]
        snap = await snapshot_where(dash, lambda s: s["floors"]["label"] == "2")
        await say("go")
        await snapshot_where(dash, lambda s: not s["decision"]["stop"])

    run(with_clients(go))


def test_with_the_laptop_recogniser_go_in_a_sentence_of_unknown_words_does_not_start():
    """Waqas (#196): Vosk gives "don't go" as "[unk] go"; that "go" must not start the device."""
    from demo.speech import CommandRecognizer

    class Vosk:  # what Vosk returns for each audio message, in turn
        def __init__(self):
            self.said = ["stop", "[unk] go", "[unk] [unk] go", "go"]

        def AcceptWaveform(self, data):  # noqa: N802
            return True

        def Result(self):  # noqa: N802
            text = self.said.pop(0)
            words = [{"word": w, "conf": 0.9} for w in text.split() if w != "[unk]"]
            return json.dumps({"text": text, "result": words})

    audio = bytes([2]) + bytes(4 * 480)  # one audio message of silence

    async def go(client, phone, dash):
        await phone.send_bytes(audio)  # "stop"
        await snapshot_where(dash, lambda s: s["decision"]["stop"])
        await phone.send_bytes(audio)  # "don't go"
        await phone.send_bytes(audio)  # "can we go"
        for _ in range(5):
            snap = await snapshot_where(dash, lambda s: True)
            assert snap["decision"]["stop"]
        await phone.send_bytes(audio)  # "go" on its own
        await snapshot_where(dash, lambda s: not s["decision"]["stop"])

    run(with_clients(go, recognizer=CommandRecognizer(Vosk())))


def test_the_dashboard_can_stop_the_chair_too():
    async def go(client, phone, dash):
        await dash.send_json({"type": "button", "name": "stop"})
        await snapshot_where(dash, lambda s: s["decision"]["stop"])

    run(with_clients(go))


def test_a_ping_is_answered_with_a_pong_and_the_round_trip_is_recorded():
    async def go(client, phone, dash):
        await phone.send_json({"type": "ping", "t": 42})
        while True:
            msg = await asyncio.wait_for(phone.receive_json(), 3)
            if msg["type"] == "pong":
                assert msg["t"] == 42
                break
        await phone.send_json({"type": "rtt", "ms": 12.0})
        snap = await snapshot_where(dash, lambda s: s["link"]["rtt_ms"] == 12.0)
        assert snap["link"]["missed"] == 0

    run(with_clients(go))


def test_a_round_trip_below_zero_or_not_a_number_is_not_taken_as_it_comes():
    async def go(client, phone, dash):
        await phone.send_json({"type": "rtt", "ms": -5000})
        await snapshot_where(dash, lambda s: s["link"]["rtt_ms"] == 0.0)
        await phone.send_json({"type": "rtt", "ms": "fast"})
        await phone.send_json({"type": "rtt", "ms": 30})
        await snapshot_where(dash, lambda s: s["link"]["rtt_ms"] == 30.0)

    run(with_clients(go))


def test_the_round_trip_is_cleared_when_the_phone_goes():
    async def go(client, phone, dash):
        await phone.send_json({"type": "rtt", "ms": 12.0})
        await snapshot_where(dash, lambda s: s["link"]["rtt_ms"] == 12.0)
        await phone.close()
        await snapshot_where(dash, lambda s: s["link"]["rtt_ms"] is None)

    run(with_clients(go))


def test_the_phone_receives_the_sensor_states_and_the_decision():
    async def go(client, phone, dash):
        while True:
            msg = await asyncio.wait_for(phone.receive_json(), 3)
            if msg["type"] == "state":
                break
        assert len(msg["sensors"]) == 8
        assert "mode" in msg["decision"] and "reason" in msg["decision"]
        assert isinstance(msg["heard"], list)  # the same messages the laptop says

    run(with_clients(go))


def test_radio_reports_make_the_wifi_and_bluetooth_tiles_active_and_pick_the_link():
    async def go(client, phone, dash):
        await phone.send_json({"type": "radio", "wifi": True, "bluetooth": True})
        snap = await snapshot_where(dash, lambda s: sensor(s, "wifi")["state"] == "active")
        assert sensor(snap, "bluetooth")["state"] == "active"
        assert snap["link"]["state"] == "wifi"
        stop = asyncio.Event()

        async def keep_reporting():  # the phone page reports its radios every second
            while not stop.is_set():
                await phone.send_json({"type": "radio", "wifi": False, "bluetooth": True})
                await asyncio.sleep(0.4)

        task = asyncio.create_task(keep_reporting())
        try:
            snap = await snapshot_where(
                dash, lambda s: s["link"]["state"] == "bluetooth", tries=150
            )
        finally:
            stop.set()
            await task
        assert sensor(snap, "wifi")["state"] == "unavailable"
        assert sensor(snap, "wifi")["reason"] == "Not available"

    run(with_clients(go))


def test_a_camera_frame_makes_the_camera_active_and_is_served_back():
    async def go(client, phone, dash):
        assert (await client.get("/frame.jpg")).status == 204
        await phone.send_bytes(b"\x01" + jpeg((220, 220, 220)))
        snap = await snapshot_where(dash, lambda s: sensor(s, "camera")["state"] == "active")
        assert snap["models"]["detector"] is False  # nothing is invented without a model
        assert snap["frame"]["boxes"] == []
        resp = await client.get("/frame.jpg")
        assert resp.status == 200 and resp.content_type == "image/jpeg"

    run(with_clients(go))


class SlowSecondDetector:
    """Finds a person in the first frame; every later detection takes a second."""

    def __init__(self):
        self.calls = 0

    def detect(self, jpeg):
        import time

        from demo.vision import Detection

        self.calls += 1
        if self.calls > 1:
            time.sleep(0.3)
        return [Detection("person", 0.9, 10.0, 5.0, 30.0, 45.0)], 64, 48


def test_boxes_stay_on_new_frames_until_the_next_detection():
    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01" + jpeg())
        await snapshot_where(dash, lambda s: s["frame"]["boxes"])
        await phone.send_bytes(b"\x01" + jpeg())  # arrives while the detector is still busy
        snap = await snapshot_where(dash, lambda s: s["frame"]["seq"] == 2)
        assert [b["label"] for b in snap["frame"]["boxes"]] == ["person"]

    run(with_clients(go, detector=SlowSecondDetector()))


class PersonDetector:
    def detect(self, jpeg):
        from demo.vision import Detection

        return [Detection("person", 0.9, 10.0, 5.0, 30.0, 45.0)], 64, 48


class PersonAndKiteDetector:
    def detect(self, jpeg):
        from demo.vision import Detection

        return (
            [
                Detection("kite", 0.8, 0.0, 0.0, 9.0, 9.0),
                Detection("person", 0.9, 10.0, 5.0, 30.0, 45.0),
            ],
            64,
            48,
        )


def test_each_box_carries_its_distance_when_it_is_an_obstacle():
    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01" + jpeg())
        snap = await snapshot_where(dash, lambda s: len(s["frame"]["boxes"]) == 2)
        kite, person = snap["frame"]["boxes"]
        assert kite["distance_m"] is None  # no typical height: not an obstacle
        assert person["distance_m"] is not None and person["distance_m"] > 0

    run(with_clients(go, detector=PersonAndKiteDetector()))


def test_what_the_camera_sees_reaches_the_decision_log():
    async def go(client, phone, dash):
        for _ in range(3):
            await phone.send_bytes(b"\x01" + jpeg())
            await asyncio.sleep(0.15)
        await snapshot_where(
            dash, lambda s: any(e["text"].startswith("Camera sees: person") for e in s["decisions"])
        )

    run(with_clients(go, detector=PersonDetector()))


def test_a_broken_frame_does_not_crash_the_server():
    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01not a jpeg")
        await phone.send_str("not json")
        await phone.send_json({"type": "motion", "ax": 0, "ay": 0, "az": 9.81})
        await snapshot_where(dash, lambda s: sensor(s, "motion")["state"] == "active")

    run(with_clients(go))


def test_the_evidence_endpoint_runs_the_scripted_scenarios_and_labels_them_as_tests():
    async def go(client, phone, dash):
        data = await (await client.get("/api/evidence")).json()
        assert "not live" in data["label"]
        names = {s["name"] for s in data["scenarios"]}
        assert {"level", "ramp", "stairs", "curb", "stop"} <= names
        assert all(s["passed"] for s in data["scenarios"])
        assert json.dumps(data)  # plain data

    run(with_clients(go))


def test_the_phone_and_dashboard_pages_and_their_files_are_served():
    async def go(client, phone, dash):
        phone_page = await client.get("/phone")
        assert phone_page.status == 200
        html = await phone_page.text()
        assert 'id="grid"' in html and "MANA" in html
        dash_page = await client.get("/dashboard")
        assert dash_page.status == 200 and 'id="inputs"' in await dash_page.text()
        for path in ("style.css", "common.js", "phone.js", "dashboard.js"):
            assert (await client.get("/static/" + path)).status == 200

    run(with_clients(go))


def test_the_phone_page_lists_eight_sensors_in_two_columns_and_needs_no_scrolling():
    from pathlib import Path

    from demo.middleware import SENSORS

    css = (Path(__file__).parents[1] / "demo" / "web" / "style.css").read_text()
    assert len(SENSORS) == 8
    assert "grid-template-columns: repeat(2, 1fr)" in css  # the phone grid
    assert "height: 100dvh" in css  # the phone page is one screen
    assert "height: 100vh" in css  # and so is the dashboard


def test_the_imgsz_option_reaches_the_detector(monkeypatch):
    from demo import server, vision

    loaded = []
    monkeypatch.setattr(
        vision.Detector, "load", lambda weights, imgsz: loaded.append((weights, imgsz))
    )
    monkeypatch.setattr(server.web, "run_app", lambda *a, **k: None)
    server.main(["--weights", "yoloe-26n-mana.pt", "--imgsz", "480"])
    server.main([])
    assert loaded == [("yoloe-26n-mana.pt", 480), ("yolo26n.pt", 416)]


def test_a_walk_from_the_phone_shows_steps_and_the_gps_position_on_the_dashboard():
    import math

    async def go(client, phone, dash):
        await phone.send_json({"type": "orientation", "alpha": 0, "beta": 70, "gamma": 0})
        await phone.send_json(
            {"type": "gps", "accuracy": 5.0, "lat": 31.5204, "lon": 74.3587, "speed": None}
        )
        for i in range(150):  # 5 s at 1.8 steps a second, the phone's clock in t
            t = i / 30
            m = 9.81 + 1.5 * math.sin(2 * math.pi * 1.8 * t)
            await phone.send_json({"type": "motion", "ax": 0, "ay": 0, "az": m, "t": t})
        await phone.send_json({"type": "orientation", "alpha": None, "beta": "x", "gamma": 0})
        snap = await snapshot_where(dash, lambda s: s["position"]["track"]["steps"] >= 7)
        track = snap["position"]["track"]
        assert track["heading"] == 0 and track["y"] > 3.0  # walked north
        assert track["lat"] > 31.5204 and track["lon"] == pytest.approx(74.3587, abs=1e-6)

    run(with_clients(go))


def test_the_dashboard_says_how_the_phone_is_carried():
    async def go(client, phone, dash):
        await dash.send_json({"type": "carried", "on_foot": True})
        await snapshot_where(dash, lambda s: s["on_foot"] is True)
        await dash.send_json({"type": "carried", "on_foot": "yes"})  # not a switch: ignored
        await dash.send_json({"type": "carried", "on_foot": False})
        await snapshot_where(dash, lambda s: s["on_foot"] is False)

    run(with_clients(go))


class SlowDetector:
    """A detector that takes 1.5 s a frame, as on a busy laptop."""

    def detect(self, jpeg):
        import time

        time.sleep(1.5)
        return [], 64, 48


def test_a_stop_from_the_phone_does_not_wait_for_the_detector():
    async def go(client, phone, dash):
        mw = client.app["mw"]
        await phone.send_bytes(b"\x01" + jpeg())  # the detector is busy from here for 1.5 s
        await asyncio.sleep(0.1)
        assert client.app["live"]["busy"]
        sent = asyncio.get_running_loop().time()
        await phone.send_json({"type": "button", "name": "stop"})
        for _ in range(100):
            if mw.core.tick(mw.clock()).stop:
                break
            await asyncio.sleep(0.01)
        waited = asyncio.get_running_loop().time() - sent
        assert mw.core.tick(mw.clock()).stop and waited < 0.5  # not 1.4 s behind the detector
        assert client.app["live"]["busy"]  # the detector was still running

    run(with_clients(go, detector=SlowDetector()))


def test_the_dashboard_marks_a_place_and_the_walk_can_be_saved():
    async def go(client, phone, dash):
        await dash.send_json({"type": "place", "do": "mark"})
        await snapshot_where(dash, lambda s: s["position"]["track"]["marks"].get("mark"))
        await dash.send_json({"type": "place", "do": "way"})
        await snapshot_where(
            dash, lambda s: any("at the mark" in e["text"] for e in s["decisions"])
        )
        r = await client.get("/api/walk.gpx")
        assert r.status == 409  # no GPS fix yet
        r = await client.get("/api/walk.geojson")
        geo = await r.json(content_type=None)
        assert r.status == 200 and geo["features"][1]["properties"]["name"] == "mark"
        await phone.send_json({"type": "gps", "accuracy": 5.0, "lat": 31.52, "lon": 74.35})
        await snapshot_where(dash, lambda s: s["position"]["track"]["lat"] is not None)
        r = await client.get("/api/walk.gpx")
        assert r.status == 200 and "<wpt" in await r.text()

    run(with_clients(go))


def test_the_live_page_and_its_scripts_are_served_and_the_root_opens_it():
    async def go():
        client = TestClient(TestServer(build_app()))
        await client.start_server()
        try:
            r = await client.get("/", allow_redirects=False)
            assert r.status == 302 and r.headers["Location"] == "/live"
            page = await (await client.get("/live")).text()
            assert "/static/live.js" in page and "/static/icons.js" in page
            for f in ("live.js", "live.css", "icons.js"):
                assert (await client.get("/static/" + f)).status == 200
        finally:
            await client.close()

    run(go())


def test_a_destination_picked_on_the_map_is_checked_kept_and_cleared():
    from demo.server import metres

    assert metres(3, -4.5) == (3.0, -4.5)
    for bad in [("3", 4), (True, 0), (float("nan"), 0), (float("inf"), 0), (None, 1), (30_000, 0),
                (10**400, 0)]:  # fmt: skip
        assert metres(*bad) is None

    async def go(client, phone, dash):
        await dash.send_json({"type": "place", "do": "destination", "x": "north", "y": 1})
        await dash.send_json({"type": "place", "do": "destination", "x": 12.5, "y": -3})
        s = await snapshot_where(dash, lambda s: "destination" in s["position"]["track"]["marks"])
        assert s["position"]["track"]["marks"]["destination"] == [12.5, -3.0]
        assert any(e["text"].startswith("Destination set.") for e in s["decisions"])
        await dash.send_json({"type": "place", "do": "clear"})
        await snapshot_where(dash, lambda s: not s["position"]["track"]["marks"])

    run(with_clients(go))


def test_the_live_page_starts_the_walk_again():
    async def go(client, phone, dash):
        await dash.send_json({"type": "place", "do": "mark"})
        await dash.send_json({"type": "place", "do": "destination", "x": 5, "y": 5})
        await snapshot_where(dash, lambda s: len(s["position"]["track"]["marks"]) == 2)
        await dash.send_json({"type": "place", "do": "restart"})
        s = await snapshot_where(dash, lambda s: not s["position"]["track"]["marks"])
        assert any(e["text"] == "Starting again from here." for e in s["decisions"])
        assert not any(e["text"].startswith("Destination set") for e in s["decisions"])

    run(with_clients(go))


def test_the_phone_is_told_first_how_often_to_send_a_camera_frame():
    """200 ms by default (a laptop CPU); the lab PC's GPU server says 80 (--frame-ms)."""

    async def go(client, phone, dash):
        first = await asyncio.wait_for(phone.receive_json(), 3)
        assert first == {"type": "config", "frame_ms": 200}

    run(with_clients(go))

    async def faster(client, phone, dash):
        first = await asyncio.wait_for(phone.receive_json(), 3)
        assert first["frame_ms"] == 80

    run(with_clients(faster, frame_ms=80))


def test_stairs_the_segmenter_finds_reach_the_core_and_the_dashboard():
    """The segmenter in the classifier's place: its stairs go to the core from the classifier,
    and the dashboard draws where they are."""
    from demo.vision import Detection

    class Segmenter:
        def classify(self, jpeg):
            return "stairs", 0.9

        def region(self, w, h):
            return Detection("stairs", 0.9, 0.0, h / 2, float(w), float(h))

    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01" + jpeg())
        snap = await snapshot_where(
            dash, lambda s: any(b["label"] == "stairs (pixels)" for b in s["frame"]["boxes"])
        )
        assert snap["frame"]["boxes"][-1]["confidence"] == 0.9

    run(with_clients(go, classifier=Segmenter()))


class FloorThingsDetector:
    def detect(self, jpeg):
        from demo.vision import Detection

        return (
            [
                Detection("mop", 0.8, 20.0, 30.0, 40.0, 46.0),  # lying on the floor ahead
                Detection("cup", 0.9, 44.0, 30.0, 50.0, 46.0),  # not a floor thing
            ],
            64,
            48,
        )


def test_a_mop_on_the_floor_is_an_obstacle_where_its_box_meets_the_floor():
    """No typical height for a mop, so no box-height distance: with the phone's tilt, where its
    box's bottom edge meets the floor says how far it is. A cup, which may be on a table, is not
    measured that way."""

    async def go(client, phone, dash):
        for _ in range(3):  # the phone tilted 30 degrees down
            await phone.send_json({"type": "motion", "ax": 0.0, "ay": 8.5, "az": 4.9})
        await phone.send_bytes(b"\x01" + jpeg())
        snap = await snapshot_where(
            dash, lambda s: any(b["label"] == "mop" for b in s["frame"]["boxes"])
        )
        mop, cup = snap["frame"]["boxes"]
        assert mop["distance_m"] is not None and 0.5 < mop["distance_m"] < 5.0
        assert cup["distance_m"] is None

    run(with_clients(go, detector=FloorThingsDetector()))


def test_without_the_tilt_a_mop_has_no_distance_and_is_not_guessed():
    async def go(client, phone, dash):
        await phone.send_bytes(b"\x01" + jpeg())
        snap = await snapshot_where(
            dash, lambda s: any(b["label"] == "mop" for b in s["frame"]["boxes"])
        )
        assert snap["frame"]["boxes"][0]["distance_m"] is None

    run(with_clients(go, detector=FloorThingsDetector()))
