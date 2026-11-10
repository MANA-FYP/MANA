"""Sirens and horns on the phone's microphone (#47): the window kept, when it is scored, what counts
as heard, what MANA says, and the server feeding the phone's sound to it. The model is a stand-in:
EfficientAT itself is not in the repository."""

import asyncio
from array import array

import numpy as np
import pytest

from demo import sounds
from demo.middleware import Middleware


def scores(siren=0.0, horn=0.0):
    s = np.zeros(527, dtype=np.float32)
    s[396], s[308] = siren, horn
    return s


def test_sound_is_resampled_to_32_khz():
    assert sounds.resample(np.ones(48000, dtype=np.float32), 48000).size == 32000
    assert sounds.resample(np.ones(320, dtype=np.float32), 32000).size == 320


def test_the_last_ten_seconds_are_kept_and_a_short_start_is_padded_to_ten():
    seen = []
    listener = sounds.Listener(lambda w: seen.append(w) or scores())
    listener.feed(np.ones(48000, dtype=np.float32), 48000)  # 1 s
    listener.hear(0.0)
    assert seen[-1].size == 320000 and seen[-1][:32000].all() and not seen[-1][32000:].any()
    for _ in range(12):
        listener.feed(np.full(48000, 0.5, dtype=np.float32), 48000)
    assert listener.sound.size == 320000 and (listener.sound == 0.5).all()


def test_the_window_is_scored_once_a_second():
    listener = sounds.Listener(lambda w: scores())
    assert not listener.due(0.0)  # nothing heard yet
    listener.feed(np.ones(4800, dtype=np.float32), 48000)
    assert listener.due(0.0)
    listener.hear(0.0)
    assert not listener.due(0.5) and listener.due(1.0)


@pytest.mark.parametrize(
    ("siren", "horn", "heard"),
    [
        (0.0, 0.0, {}),
        (0.21, 0.07, {}),
        (0.23, 0.0, {"siren": 0.23}),
        (0.5, 0.3, {"siren": 0.5, "horn": 0.3}),
    ],
)
def test_a_siren_or_horn_is_heard_from_its_threshold(siren, horn, heard):
    listener = sounds.Listener(lambda w: scores(siren, horn))
    listener.feed(np.ones(100, dtype=np.float32), 32000)
    assert listener.hear(0.0) == heard


def test_mana_says_a_siren_once_while_it_goes_on_and_shows_it():
    m = Middleware()
    for t in (1.0, 2.0, 5.0):
        m.on_sounds({"siren": 0.6}, now=t)
    said = [e["text"] for e in m.decisions if e["kind"] == "sound"]
    assert said == ["A siren nearby. Take care."]
    assert [s["hazard"] for s in m.tick(5.5)["sounds"]] == ["siren"]
    assert m.tick(20.0)["sounds"] == []  # not heard for a while: no longer shown
    m.on_sounds({"siren": 0.6}, now=20.0)  # heard again after 10 s: said again
    said = [e["text"] for e in m.decisions if e["kind"] == "sound"]
    assert said == ["A siren nearby. Take care."] * 2


def test_the_server_feeds_the_phones_sound_to_the_listener():
    pytest.importorskip("aiohttp")
    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import build_app

    heard = []
    listener = sounds.Listener(lambda w: heard.append(w.size) or scores(siren=0.9))

    async def go():
        client = TestClient(TestServer(build_app(listener=listener)))
        await client.start_server()
        try:
            phone = await client.ws_connect("/ws/phone")
            dash = await client.ws_connect("/ws/dashboard")
            await phone.send_bytes(b"\x02" + array("f", [0.1] * 4800).tobytes())
            for _ in range(60):
                msg = await asyncio.wait_for(dash.receive_json(), 3)
                if msg.get("type") == "snapshot" and msg["sounds"]:
                    break
            assert msg["sounds"][0]["hazard"] == "siren" and msg["models"]["sounds"] is True
            assert heard and heard[0] == 320000
        finally:
            await client.close()

    asyncio.run(go())
