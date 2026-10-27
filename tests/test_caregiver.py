"""Tests for the caregiver's read-only status view: the view itself, and the page, socket and
status endpoint on the real server. The server tests need aiohttp and are skipped without it."""

import asyncio

import pytest

from demo import caregiver


def snapshot(stop=False, speed=1.0, mode="WHEELS", decisions=(), position="gps", link="wifi"):
    return {
        "time": 12.5,
        "decision": {
            "mode": mode,
            "stop": stop,
            "speed": speed,
            "reason": "Level ground",
            "sentence": "Moving on wheels at full speed.",
            "priority": "INFO",
        },
        "manual": False,
        "position": {"source": position, "accuracy_m": 4.26},
        "link": {"state": link, "rtt_ms": 15.0},
        "decisions": list(decisions),
    }


def alert(n, text, kind="decision"):
    return {"n": n, "t": float(n), "kind": kind, "text": text}


def test_the_state_says_moving_slowing_down_or_stopped():
    assert caregiver.view(snapshot(), True, 0.1)["state"] == "moving"
    assert caregiver.view(snapshot(speed=0.3), True, 0.1)["state"] == "slow"
    assert caregiver.view(snapshot(stop=True, speed=0.0), True, 0.1)["state"] == "stopped"


def test_mana_is_offline_when_the_phone_is_gone_or_has_gone_quiet():
    assert caregiver.view(snapshot(), False, 0.1)["state"] == "offline"
    assert caregiver.view(snapshot(), True, None)["state"] == "offline"
    v = caregiver.view(snapshot(), True, caregiver.STALE_S + 0.5)
    assert v["state"] == "offline" and not v["online"]


def test_the_newest_alerts_come_first_and_camera_sightings_are_left_out():
    log = [alert(i, f"alert {i}") for i in range(1, 12)] + [
        alert(12, "Camera sees: person", kind="detection")
    ]
    alerts = caregiver.view(snapshot(decisions=log), True, 0.1)["alerts"]
    assert [a["text"] for a in alerts] == [f"alert {i}" for i in range(11, 3, -1)]
    assert len(alerts) == caregiver.ALERTS


def test_mode_position_and_link_are_in_words():
    v = caregiver.view(snapshot(mode="TRACKS", position="indoor", link="bluetooth"), True, 0.1)
    assert v["mode"] == "On tracks"
    assert v["position"] == {"source": "Indoor map", "accuracy_m": 4.3}
    assert v["link"]["text"] == "Bluetooth" and v["link"]["state"] == "bluetooth"
    assert caregiver.view(snapshot(link="none"), True, 0.1)["link"]["text"] == "No link"


def test_sentences_to_the_user_are_reworded_for_the_caregiver():
    snap = snapshot(decisions=[alert(1, "Manual mode. You are in control of the device.")])
    snap["decision"]["sentence"] = "MANA deactivated. You are fully in control."
    v = caregiver.view(snap, True, 0.1)
    assert v["status"] == "Stopped by the user. They are in control."
    assert v["alerts"][0]["text"] == "Manual mode. The user is driving."
    assert caregiver.for_caregiver("Stopped by you") == "Stopped by the user"


def test_the_view_has_no_camera_image_or_raw_sensor_stream():
    snap = snapshot() | {"frame": {"seq": 3}, "sensors": [], "messages": [], "detections": []}
    v = caregiver.view(snap, True, 0.1)
    assert not {"frame", "sensors", "messages", "detections"} & v.keys()


# ---- on the real server

aiohttp = pytest.importorskip("aiohttp")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

from demo.server import build_app  # noqa: E402


def run(coro):
    return asyncio.run(coro)


async def with_client(test):
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        await test(client)
    finally:
        await client.close()


async def receive_where(ws, kind, predicate, tries=60):
    for _ in range(tries):
        msg = await asyncio.wait_for(ws.receive_json(), 3)
        if msg.get("type") == kind and predicate(msg):
            return msg
    raise AssertionError(f"never saw the expected {kind} message")


def test_the_page_loads_and_has_no_controls():
    async def go(client):
        resp = await client.get("/caregiver")
        assert resp.status == 200
        page = await resp.text()
        assert "caregiver view" in page and "/static/caregiver.js" in page
        assert "<button" not in page and "<input" not in page and "<form" not in page
        for asset in ("/static/caregiver.js", "/static/caregiver.css"):
            assert (await client.get(asset)).status == 200

    run(with_client(go))


def test_the_view_updates_live_from_the_dashboard_stream():
    async def go(client):
        care = await client.ws_connect("/ws/caregiver")
        await receive_where(care, "caregiver", lambda v: v["state"] == "offline")
        phone = await client.ws_connect("/ws/phone")
        await phone.send_json({"type": "motion", "ax": 0.0, "ay": 0.0, "az": 9.81})
        await receive_where(care, "caregiver", lambda v: v["online"])
        await phone.send_json({"type": "button", "name": "stop"})
        v = await receive_where(care, "caregiver", lambda v: v["state"] == "stopped")
        assert v["status"] == "Stopped by the user. They are in control."
        assert v["alerts"][0]["text"] == "Stopped by the user. They are in control."

    run(with_client(go))


def test_a_caregiver_cannot_stop_or_drive_the_chair():
    async def go(client):
        phone = await client.ws_connect("/ws/phone")
        await phone.send_json({"type": "motion", "ax": 0.0, "ay": 0.0, "az": 9.81})
        care = await client.ws_connect("/ws/caregiver")
        await receive_where(care, "caregiver", lambda v: v["online"])
        for name in ("stop", "manual", "go"):
            await care.send_json({"type": "button", "name": name})
        await care.send_str("not even json")
        for _ in range(10):  # a second of ticks after the attempts
            v = await receive_where(care, "caregiver", lambda v: True)
            assert v["state"] != "stopped" and not v["manual"]
        # the same button from the dashboard does stop it, so the check above means something
        dash = await client.ws_connect("/ws/dashboard")
        await dash.send_json({"type": "button", "name": "stop"})
        await receive_where(care, "caregiver", lambda v: v["state"] == "stopped")

    run(with_client(go))


def test_the_status_endpoint_gives_the_same_view_as_json():
    async def go(client):
        care = await client.ws_connect("/ws/caregiver")
        await receive_where(care, "caregiver", lambda v: True)  # the loop has ticked
        resp = await client.get("/api/status")
        assert resp.status == 200
        v = await resp.json()
        assert v["type"] == "caregiver" and v["state"] == "offline"
        assert {"mode", "position", "link", "alerts", "status"} <= v.keys()

    run(with_client(go))
