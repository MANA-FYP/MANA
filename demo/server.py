"""Laptop server for the MANA phone demo.

    python -m demo.server                      # http://localhost:8000/dashboard
    python -m demo.usb                         # the phone then opens http://localhost:8000/phone

The phone page opens /phone and streams its real sensors over a WebSocket. The dashboard opens
/dashboard and watches. Everything between them is the middleware and the decision layer. A
caregiver opens /caregiver on their own phone: a read-only status view (demo/caregiver.py).

Phone to laptop, text messages (JSON):
  hello   {"type": "hello", "caps": {"camera": {"available": true}, ...}, "audio_rate": 48000}
  motion  {"type": "motion", "ax": 0, "ay": 0, "az": 9.8}
  gps     {"type": "gps", "accuracy": 6.0}            (accuracy is null when there is no fix)
  word    {"type": "word", "word": "stop", "confidence": 0.9}   (browser speech recognition)
  button  {"type": "button", "name": "stop" | "go" | "automatic" | "manual"}
  ping    {"type": "ping", "t": 123}   answered with pong; the phone then sends an rtt message
  rtt     {"type": "rtt", "ms": 12}     the round trip it measured
  heard   {"type": "heard", "text": "stop the chair"}   (the phrase, shown as what the user said)
  sensor  {"type": "sensor", "id": "camera", "on": false}   the user tapped a tile
  mic     {"type": "mic", "state": "listening" | "<recogniser error>"}
  radio   {"type": "radio", "wifi": true, "bluetooth": false}   null when the browser cannot tell
Phone to laptop, binary messages: a first byte of 1 then a JPEG frame, or 2 then float32 audio.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import sys
import warnings
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src" / "mana_core", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from aiohttp import WSMsgType, web  # noqa: E402
from mana_core.decision import Command, Ground, Source  # noqa: E402

from demo import caregiver, speech, vision  # noqa: E402
from demo.middleware import Middleware  # noqa: E402

warnings.filterwarnings("ignore", category=web.NotAppKeyWarning)  # plain string keys are fine here

WEB = Path(__file__).parent / "web"
TICK_S = 0.1
BUTTONS = {
    "stop": Command.STOP,
    "go": Command.GO,
    "automatic": Command.AUTOMATIC,
    "manual": Command.MANUAL,
}


def brightness_of(jpeg: bytes) -> float:
    """Mean luminance of a JPEG frame, 0 (black) to 1 (white)."""
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg)).convert("L").resize((32, 24))
    pixels = list(img.getdata())
    return sum(pixels) / (len(pixels) * 255.0)


@web.middleware
async def no_cache(request, handler):
    """Pages and scripts are never cached, so an edited page shows up on the next refresh."""
    resp = await handler(request)
    if not request.path.startswith("/ws/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def build_app(mw=None, detector=None, classifier=None, recognizer=None) -> web.Application:
    app = web.Application(client_max_size=16 * 1024 * 1024, middlewares=[no_cache])
    app["mw"] = mw or Middleware()
    app["detector"], app["classifier"], app["recognizer"] = detector, classifier, recognizer
    app["phones"], app["dashboards"], app["caregivers"] = set(), set(), set()
    app["live"] = {"audio_rate": 48000, "busy": False, "phone_seen": None}
    app["frame"] = {"seq": 0, "jpeg": b"", "boxes": [], "w": 0, "h": 0}
    app["snapshot"] = None  # the last tick's state, for /api/status

    app.router.add_get("/", index)
    app.router.add_get("/dashboard", dashboard_page)
    app.router.add_get("/phone", phone_page)
    app.router.add_get("/caregiver", caregiver_page)
    app.router.add_get("/ws/phone", ws_phone)
    app.router.add_get("/ws/dashboard", ws_dashboard)
    app.router.add_get("/ws/caregiver", ws_caregiver)
    app.router.add_get("/api/status", status)
    app.router.add_get("/frame.jpg", frame_jpg)
    app.router.add_get("/api/evidence", evidence)
    app.router.add_static("/static/", WEB, show_index=False)
    app.on_startup.append(start_loop)
    app.on_cleanup.append(stop_loop)
    return app


# ---- pages


async def index(request: web.Request) -> web.Response:
    raise web.HTTPFound("/dashboard")


async def dashboard_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "dashboard.html")


async def phone_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "phone.html")


async def caregiver_page(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "caregiver.html")


# ---- the phone


async def ws_phone(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    ws = web.WebSocketResponse(max_msg_size=16 * 1024 * 1024)
    await ws.prepare(request)
    app["phones"].add(ws)
    try:
        async for msg in ws:
            app["live"]["phone_seen"] = app["mw"].clock()
            if msg.type == WSMsgType.TEXT:
                await on_text(app, ws, msg.data)
            elif msg.type == WSMsgType.BINARY:
                await on_binary(app, msg.data)
    finally:
        app["phones"].discard(ws)
    return ws


async def on_text(app: web.Application, ws: web.WebSocketResponse, text: str) -> None:
    mw: Middleware = app["mw"]
    try:
        d = json.loads(text)
    except ValueError:
        return
    kind, now = d.get("type"), mw.clock()
    if kind == "hello":
        mw.hello(d.get("caps", {}), now)
        app["live"]["audio_rate"] = int(d.get("audio_rate") or app["live"]["audio_rate"])
    elif kind == "motion":
        mw.on_motion(float(d["ax"]), float(d["ay"]), float(d["az"]), now)
    elif kind == "gps":
        acc = d.get("accuracy")
        mw.on_gps(None if acc is None else float(acc), now)
    elif kind == "word":
        # the phone sends the whole phrase first ("heard"), so a word is not written down twice
        mw.on_word(str(d.get("word", "")), float(d.get("confidence", 0.8)), now, record=False)
    elif kind == "heard":
        mw.on_heard(str(d.get("text", "")), now, final=bool(d.get("final", True)))
    elif kind == "button":
        command = BUTTONS.get(str(d.get("name", "")).lower())
        if command is not None:
            mw.on_command(command, 1.0, now, source="button")
    elif kind == "ping":
        mw.on_heartbeat(None, now)
        await ws.send_json({"type": "pong", "t": d.get("t")})
    elif kind == "rtt":
        mw.on_heartbeat(float(d["ms"]), now)
    elif kind == "sensor":
        mw.on_sensor_switch(str(d.get("id", "")), bool(d.get("on")), now)
    elif kind == "mic":
        mw.on_microphone_state(str(d.get("state", "")), now)
    elif kind == "radio":
        mw.on_radio(d.get("wifi"), d.get("bluetooth"), now)


async def on_binary(app: web.Application, data: bytes) -> None:
    if not data:
        return
    mw: Middleware = app["mw"]
    tag, payload = data[0], data[1:]
    now = mw.clock()
    if tag == 1:
        await on_frame(app, payload, now)
    elif tag == 2:
        samples = array("f")
        samples.frombytes(payload[: len(payload) // 4 * 4])
        mw.on_microphone(now)
        if app["recognizer"] is not None:
            pcm = speech.to_pcm16_16k(list(samples), app["live"]["audio_rate"])
            for word, confidence in app["recognizer"].feed(pcm):
                mw.on_word(word, confidence, now)


async def on_frame(app: web.Application, jpeg: bytes, now: float) -> None:
    mw: Middleware = app["mw"]
    try:
        mw.on_light(brightness_of(jpeg), now)
    except Exception:  # not a decodable frame
        return
    frame = app["frame"]
    # the last boxes stay until the next detection replaces them; frames arrive faster than the
    # detector runs, so clearing them here left most frames without boxes
    frame.update(seq=frame["seq"] + 1, jpeg=jpeg)
    if app["live"]["busy"] or (app["detector"] is None and app["classifier"] is None):
        return
    app["live"]["busy"] = True
    try:
        await asyncio.get_running_loop().run_in_executor(None, analyse, app, jpeg, now)
    finally:
        app["live"]["busy"] = False


def analyse(app: web.Application, jpeg: bytes, now: float) -> None:
    """Run the models on one frame (in a worker thread) and feed the middleware."""
    mw: Middleware = app["mw"]
    frame = app["frame"]
    if app["detector"] is not None:
        started = mw.clock()
        detections, w, h = app["detector"].detect(jpeg)
        mw.latency["detector_ms"] = round((mw.clock() - started) * 1000, 1)
        frame.update(w=w, h=h)
        found = vision.to_obstacles(detections, w, h)
        nearest: dict[str, float] = {}
        for ob in found:  # nearest first
            mw.on_obstacle(ob.label, ob.distance_m, ob.confidence, ob.bearing_rad, mw.clock())
            nearest.setdefault(ob.label, ob.distance_m)
        mw.on_detections([(d.label, nearest.get(d.label)) for d in detections], mw.clock())
        frame["boxes"] = [
            {"label": d.label, "confidence": round(d.confidence, 2), "x1": d.x1, "y1": d.y1,
             "x2": d.x2, "y2": d.y2} for d in detections
        ]  # fmt: skip
    if app["classifier"] is not None:
        label, conf = app["classifier"].classify(jpeg)
        ground = Ground.STAIRS_UP if vision.stair_ground(label) == "stairs" else Ground.FLAT
        mw.on_ground(Source.CLASSIFIER, ground, conf, now=mw.clock())


# ---- the dashboard and the test evidence


async def ws_dashboard(request: web.Request) -> web.WebSocketResponse:
    app = request.app
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app["dashboards"].add(ws)
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            try:
                d = json.loads(msg.data)
            except ValueError:
                continue
            command = BUTTONS.get(str(d.get("name", "")).lower())
            if d.get("type") == "button" and command is not None:
                app["mw"].on_command(command, 1.0, source="dashboard")
    finally:
        app["dashboards"].discard(ws)
    return ws


# ---- the caregiver: read-only


def caregiver_view(app: web.Application, snap: dict) -> dict:
    seen = app["live"]["phone_seen"]
    age = None if seen is None else app["mw"].clock() - seen
    return caregiver.view(snap, bool(app["phones"]), age)


async def ws_caregiver(request: web.Request) -> web.WebSocketResponse:
    """The caregiver's status, pushed every tick. Anything the page sends is ignored."""
    app = request.app
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    app["caregivers"].add(ws)
    try:
        async for _ in ws:
            pass  # read-only: no command is taken from a caregiver
    finally:
        app["caregivers"].discard(ws)
    return ws


async def status(request: web.Request) -> web.Response:
    """The caregiver's status once, as JSON, from the last tick (it does not tick the core)."""
    app = request.app
    if app["snapshot"] is None:
        raise web.HTTPServiceUnavailable(text="starting up")
    return web.json_response(caregiver_view(app, app["snapshot"]))


async def frame_jpg(request: web.Request) -> web.Response:
    jpeg = request.app["frame"]["jpeg"]
    if not jpeg:
        return web.Response(status=204)
    return web.Response(body=jpeg, content_type="image/jpeg", headers={"Cache-Control": "no-store"})


async def evidence(request: web.Request) -> web.Response:
    """The scripted scenarios run against the same decision layer, as automated test evidence."""
    from mana_core.evaluate import run
    from mana_core.scenarios import SCENARIOS, check

    scenarios = []
    for name, sc in SCENARIOS.items():
        marks = [
            {"text": m.text, "ok": bool(ok), "measured": measured}
            for m, ok, measured in check(sc, run(sc))
        ]
        scenarios.append({"name": name, "title": sc.title, "shows": sc.shows, "marks": marks,
                          "passed": all(m["ok"] for m in marks)})  # fmt: skip
    return web.json_response(
        {"label": "automated test with scripted inputs, not live data", "scenarios": scenarios}
    )


# ---- the loop that ticks the decision layer and pushes state


def models_status(app: web.Application) -> dict:
    return {
        "detector": app["detector"] is not None,
        "classifier": app["classifier"] is not None,
        "speech": app["recognizer"] is not None,
    }


async def broadcast(app: web.Application) -> None:
    mw: Middleware = app["mw"]
    snap = mw.tick()
    snap["models"] = models_status(app)
    app["snapshot"] = snap
    snap["frame"] = {"seq": app["frame"]["seq"], "w": app["frame"]["w"], "h": app["frame"]["h"],
                     "boxes": app["frame"]["boxes"]}  # fmt: skip
    for ws in list(app["dashboards"]):
        with contextlib.suppress(ConnectionError, RuntimeError):
            await ws.send_json({"type": "snapshot", **snap})
    if app["caregivers"]:
        care = caregiver_view(app, snap)
        for ws in list(app["caregivers"]):
            with contextlib.suppress(ConnectionError, RuntimeError):
                await ws.send_json(care)
    state = {"type": "state", "sensors": snap["sensors"], "decision": snap["decision"],
             "link": snap["link"], "position": snap["position"],
             "manual": snap["manual"], "models": snap["models"],
             "heard": snap["heard"]}  # fmt: skip
    for ws in list(app["phones"]):
        with contextlib.suppress(ConnectionError, RuntimeError):
            await ws.send_json(state)


async def loop(app: web.Application) -> None:
    while True:
        await broadcast(app)
        await asyncio.sleep(TICK_S)


async def start_loop(app: web.Application) -> None:
    app["loop"] = asyncio.create_task(loop(app))


async def stop_loop(app: web.Application) -> None:
    app["loop"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app["loop"]


# ---- command line


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="MANA phone demo server")
    p.add_argument("--host", default="127.0.0.1", help="the phone arrives through the USB cable")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--weights", default="yolo26n.pt", help="object detector weights (Ultralytics)")
    p.add_argument("--imgsz", type=int, default=416, help="detector input size; 640 finds more")
    p.add_argument("--no-detector", action="store_true")
    p.add_argument("--vosk-model", help="folder of a Vosk model, for offline voice commands")
    p.add_argument("--stairs-model", help="ONNX file of the stairs classifier")
    p.add_argument("--directions", action="store_true",
                   help="also say what is ahead, for a smart cane or glasses host")  # fmt: skip
    a = p.parse_args(argv)

    detector = None if a.no_detector else vision.Detector.load(a.weights, a.imgsz)
    classifier = vision.GroundClassifier.load(a.stairs_model) if a.stairs_model else None
    recognizer = speech.CommandRecognizer.load(a.vosk_model) if a.vosk_model else None
    app = build_app(Middleware(directions=a.directions), detector, classifier, recognizer)

    print(f"dashboard: http://localhost:{a.port}/dashboard")
    print(f"phone:     http://localhost:{a.port}/phone   (after: python -m demo.usb)")
    print(f"caregiver: http://localhost:{a.port}/caregiver   (read-only)")
    for name, on in models_status(app).items():
        print(f"model {name}: {'loaded' if on else 'not available'}")
    web.run_app(app, host=a.host, port=a.port, print=None)


if __name__ == "__main__":
    main()
