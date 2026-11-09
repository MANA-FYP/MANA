"""A phone session recorded by the server and played back into a fresh one (#89). Needs aiohttp,
so it is skipped where it is not installed."""

import asyncio
import importlib.util
import io
import json
import math

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("aiohttp") is None, reason="needs aiohttp")


def jpeg(color=(200, 200, 200)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, "JPEG")
    return buf.getvalue()


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_the_recorder_keeps_messages_and_frames_but_not_audio(tmp_path):
    from demo.recording import Recorder, read

    clock = Clock()
    rec = Recorder(tmp_path / "walk", clock)
    rec.text(json.dumps({"type": "motion", "ax": 0, "ay": 0, "az": 9.8, "t": 0}))
    clock.t += 0.5
    rec.binary(b"\x01" + jpeg())
    rec.binary(b"\x02" + b"\x00" * 100)  # audio
    rec.text("not json")
    rec.close()
    lines = read(tmp_path / "walk")
    assert lines == [
        {"t": 0.0, "msg": {"type": "motion", "ax": 0, "ay": 0, "az": 9.8, "t": 0}},
        {"t": 0.5, "frame": "frames/000001.jpg"},
    ]
    assert (tmp_path / "walk/frames/000001.jpg").read_bytes() == jpeg()


def test_a_walk_recorded_by_the_server_plays_back_the_same_into_a_fresh_one(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from demo import recording
    from demo.server import build_app

    folder = tmp_path / "walk"

    async def track_of(dash, steps):
        for _ in range(80):
            msg = await asyncio.wait_for(dash.receive_json(), 3)
            if msg.get("type") == "snapshot" and msg["position"]["track"]["steps"] >= steps:
                return msg
        raise AssertionError("the dashboard never saw the walk")

    async def record():
        client = TestClient(TestServer(build_app(recorder=recording.Recorder(folder))))
        await client.start_server()
        try:
            phone = await client.ws_connect("/ws/phone")
            dash = await client.ws_connect("/ws/dashboard")
            await phone.send_json({"type": "orientation", "alpha": 0, "beta": 70, "gamma": 0})
            await phone.send_bytes(b"\x01" + jpeg())
            for i in range(150):  # 5 s at 1.8 steps a second
                t = i / 30
                m = 9.81 + 1.5 * math.sin(2 * math.pi * 1.8 * t)
                await phone.send_json({"type": "motion", "ax": 0, "ay": 0, "az": m, "t": t})
            return (await track_of(dash, 7))["position"]["track"]
        finally:
            await client.close()

    async def replay():
        client = TestClient(TestServer(build_app()))
        await client.start_server()
        try:
            dash = await client.ws_connect("/ws/dashboard")
            url = str(client.make_url("")).rstrip("/")
            sent = await recording.play(url, folder, speed=50)
            snap = await track_of(dash, 7)
            assert (await client.get("/frame.jpg")).status == 200  # the frame came through
            return sent, snap["position"]["track"]
        finally:
            await client.close()

    live = asyncio.run(record())
    lines = recording.read(folder)
    assert len(lines) == 152 and lines[1] == {"t": lines[1]["t"], "frame": "frames/000001.jpg"}
    sent, played = asyncio.run(replay())
    assert sent == 152
    assert played["steps"] == live["steps"] and played["heading"] == live["heading"] == 0
    assert played["y"] == pytest.approx(live["y"], abs=0.01)


def test_replay_says_when_there_is_no_recording(tmp_path, capsys):
    from demo.replay import main

    assert main([str(tmp_path)]) == 2
    assert "no recording" in capsys.readouterr().err
