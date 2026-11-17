"""People from Wi-Fi (demo/csi.py): ESP32 lines, windows, the model, the replay, the screen."""

import asyncio
from pathlib import Path

import numpy as np
import pytest

from demo import csi

HEADER = (
    "type,seq,mac,rssi,rate,noise_floor,fft_gain,agc_gain,channel,local_timestamp,sig_len,"
    "rx_state,len,first_word,data"
)
BASES = {  # a made-up amplitude shape per label, 56 subcarriers
    "empty": np.linspace(5, 15, 56),
    "one": np.linspace(15, 5, 56),
    "two": 10 + 4 * np.sin(np.arange(56) / 4),
}


def line(t_s: float, amps, rssi: int = -60) -> str:
    """A CSI_DATA line as esp-csi prints it: 64 (I, Q) pairs, guards and DC zero."""
    iq = np.zeros((64, 2), dtype=int)
    iq[csi.SUBCARRIERS, 0] = np.round(amps).astype(int)  # all in I: the amplitude is |I|
    data = ",".join(str(v) for v in iq.ravel())
    us = int(t_s * 1e6)
    return f'CSI_DATA,{us},1a:00:00:00:00:00,{rssi},11,-96,3,46,6,{us},47,0,128,0,"[{data}]"'


def recording(path: Path, label: str, seconds: float = 3.0, hz: int = 100, seed: int = 0) -> Path:
    rng = np.random.default_rng(seed)
    rows = [HEADER]
    for i in range(int(seconds * hz)):
        rows.append(line(100 + i / hz, BASES[label] + rng.normal(0, 0.3, 56)))
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def centroid_model(threshold: float = 0.05) -> csi.ZoneModel:
    """Nearest centroid written as a linear model: no scikit-learn in the tests."""
    rng = np.random.default_rng(1)
    feats = {
        k: np.array([csi.features(b + rng.normal(0, 0.3, (100, 56))) for _ in range(20)])
        for k, b in BASES.items()
    }
    X = np.vstack(list(feats.values()))
    mean, scale = X.mean(0), X.std(0) + 1e-9
    cents = np.array([((f - mean) / scale).mean(0) for f in feats.values()])
    return csi.ZoneModel(list(feats), mean, scale, cents, -0.5 * (cents**2).sum(1), threshold)


def test_an_esp32_line_gives_the_56_amplitudes_and_noise_is_skipped():
    amps = np.arange(1, 57, dtype=float)
    p = csi.parse_line(line(2.5, amps, rssi=-71))
    assert p.t == pytest.approx(2.5) and p.rssi == -71 and p.amp.shape == (56,)
    assert np.allclose(p.amp, amps)
    # what a real serial port also prints: boot messages, a watchdog warning on its own line and
    # spliced into a row, cut lines, an unclosed quote longer than the csv module's field limit
    wdt = "E (30736) task_wdt: Task watchdog got triggered."
    assert csi.parse_line("I (312) wifi: mode : sta") is None
    assert csi.parse_line(wdt) is None
    row = line(1, amps)
    assert csi.parse_line(row[:200] + wdt + row[200:]) is None
    assert csi.parse_line(row[:-40]) is None
    assert csi.parse_line('CSI_DATA,1,"' + "9" * 200_000) is None
    assert csi.parse_row(["CSI_DATA"] + ["x"] * 14) is None


def test_windows_of_one_second_and_an_esp32_restart():
    def packets(ts):
        return [csi.Packet(t, -60, np.ones(56)) for t in ts]

    ts = [i / 100 for i in range(300)]  # 3 s at 100 Hz
    assert [round(t) for t, _ in csi.windows(packets(ts))] == [0, 1, 2]
    restarted = ts[:150] + [i / 100 for i in range(250)]  # the clock starts again at 1.5 s
    starts = [round(t, 1) for t, _ in csi.windows(packets(restarted))]
    assert starts == [
        0.0,
        0.0,
        1.0,
        2.0,
    ]  # the half window before the restart is dropped, not mixed


def test_motion_is_the_flicker_and_features_hold_shape_and_flicker():
    rng = np.random.default_rng(0)
    still = 10 + rng.normal(0, 0.1, (100, 56))
    moving = 10 + rng.normal(0, 2.0, (100, 56))
    assert csi.motion(moving) > 10 * csi.motion(still)
    f = csi.features(still)
    assert f.shape == (112,) and f[56:].sum() == pytest.approx(1.0)


def test_the_model_saves_loads_and_tells_the_shapes_apart(tmp_path):
    m = centroid_model()
    m.save(tmp_path / "m.npz")
    m2 = csi.ZoneModel.load(tmp_path / "m.npz")
    rng = np.random.default_rng(5)
    for k, base in BASES.items():
        f = csi.features(base + rng.normal(0, 0.3, (100, 56)))
        s = m2.scores(f)
        assert s.sum() == pytest.approx(1.0) and m2.predict(f) == k


def test_the_committed_model_loads():
    m = csi.ZoneModel.load(Path(csi.__file__).parent / "csi_model" / "wifi_csi_office.npz")
    assert m.labels == ["empty", "one", "two", "five"] and m.coef.shape == (4, 112)
    assert 0 < m.threshold < 1


def test_the_replay_paces_by_the_recording_tours_and_scores_against_the_truth(tmp_path):
    recs = {k: recording(tmp_path / f"{k}_2.csv", k, seed=i) for i, k in enumerate(BASES)}
    sensing = csi.Sensing(centroid_model())
    r = csi.Replay(recs, sensing, turn_s=2.5)
    r.step(0.0)  # the first packet starts the clock
    out = []
    for i in range(1, 160):  # 8 s of wall time, in 50 ms steps
        out += r.step(i * 0.05)
    played = [v["truth"] for v in out]
    assert played[0] == "empty" and "one" in played and "two" in played  # the tour moved on
    assert all(v["label"] == v["truth"] for v in out)
    assert r.truth_hits == r.truth_total == len(out) > 4
    view = sensing.view()
    assert view["last"]["present"] in (True, False) and len(view["waterfall"]) > 20
    assert all(0 <= x <= 255 for row in view["waterfall"] for x in row)

    r.choose("two")  # a fixed recording: no tour
    out = []
    for i in range(200, 300):
        out += r.step(i * 0.05)
    assert out and {v["truth"] for v in out} == {"two"}
    with pytest.raises(KeyError):
        r.choose("the moon")


def test_the_wifi_screen_and_its_api(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import build_app

    recs = {k: recording(tmp_path / f"{k}_2.csv", k) for k in BASES}
    sensing = csi.Sensing(centroid_model())
    setup = {
        "sensing": sensing,
        "replay": csi.Replay(recs, sensing),
        "serial": None,
        "scores": {"csi": {}},
    }

    async def go():
        client = TestClient(TestServer(build_app(wifi_sensing=setup)))
        await client.start_server()
        try:
            page = await (await client.get("/wifi")).text()
            assert "/static/wifi.js" in page and "Wi-Fi sensing" in page
            for _ in range(60):  # the feed runs in the server's loop
                s = await (await client.get("/api/csi")).json()
                if s["last"]:
                    break
                await asyncio.sleep(0.1)
            assert s["on"] and s["replay"]["playing"] == "empty" and s["last"]["truth"] == "empty"
            r = await client.post("/api/csi/play", json={"label": "one"})
            assert r.status == 200 and (await r.json())["fixed"] == "one"
            assert (await client.post("/api/csi/play", json={"label": "nope"})).status == 400
            for bad in ([], {}, 3):
                assert (await client.post("/api/csi/play", json={"label": bad})).status == 400
            assert (await client.post("/api/csi/play", json={"label": None})).status == 200
        finally:
            await client.close()
        client = TestClient(TestServer(build_app()))  # no --csi-replay or --csi-serial
        await client.start_server()
        try:
            assert (await (await client.get("/api/csi")).json()) == {"on": False}
            assert (await client.post("/api/csi/play", json={"label": "one"})).status == 409
        finally:
            await client.close()

    asyncio.run(go())


def test_a_quiet_esp32_does_not_keep_the_server_from_stopping(monkeypatch):
    """Waqas (#195): the serial reader ran forever in the default executor, so with a board that
    printed nothing the process never exited. Its reads must stop once the server has."""
    import sys
    import threading
    import types

    from aiohttp.test_utils import TestClient, TestServer

    from demo.server import build_app

    reads = []

    class QuietPort:  # an idle board: every read waits its timeout and returns nothing
        def __init__(self, port, baud, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def readline(self):
            reads.append(1)
            threading.Event().wait(0.1)
            return b""

    monkeypatch.setitem(sys.modules, "serial", types.SimpleNamespace(Serial=QuietPort))
    setup = {"sensing": csi.Sensing(None), "replay": None, "serial": ("COM9", 115200)}

    async def go():
        client = TestClient(TestServer(build_app(wifi_sensing={**setup, "scores": None})))
        await client.start_server()
        await asyncio.sleep(0.5)
        await client.close()

    asyncio.run(go())
    assert reads  # it was reading
    threading.Event().wait(0.3)  # a read under way may finish
    n = len(reads)
    threading.Event().wait(0.5)
    assert len(reads) == n  # and stopped
