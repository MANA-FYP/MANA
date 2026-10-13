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


async def with_clients(test):
    client = TestClient(TestServer(build_app()))
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


def test_depth_lidar_and_temperature_stay_unavailable():
    async def go(client, phone, dash):
        snap = await snapshot_where(dash, lambda s: True)
        for sid in ("depth", "lidar", "temperature"):
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


def test_the_phone_receives_the_sensor_states_and_the_decision():
    async def go(client, phone, dash):
        while True:
            msg = await asyncio.wait_for(phone.receive_json(), 3)
            if msg["type"] == "state":
                break
        assert len(msg["sensors"]) == 9
        assert "mode" in msg["decision"] and "reason" in msg["decision"]

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
        assert sensor(snap, "wifi")["value"] == "off"

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


def test_the_phone_page_lists_nine_sensors_in_two_columns_and_needs_no_scrolling():
    from pathlib import Path

    from demo.middleware import SENSORS

    css = (Path(__file__).parents[1] / "demo" / "web" / "style.css").read_text()
    assert len(SENSORS) == 9
    assert "grid-template-columns: repeat(2, 1fr)" in css  # the phone grid
    assert "height: 100dvh" in css  # the phone page is one screen
    assert "height: 100vh" in css  # and so is the dashboard
