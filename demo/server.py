"""Laptop server for the MANA phone demo.

    python -m demo.server                      # http://localhost:8000/dashboard
    python -m demo.usb                         # the phone then opens http://localhost:8000/phone

The phone page opens /phone and streams its real sensors over a WebSocket. The dashboard opens
/dashboard and watches. Everything between them is the middleware and the decision layer. A
caregiver opens /caregiver on their own phone: a read-only status view (demo/caregiver.py).

Phone to laptop, text messages (JSON):
  hello   {"type": "hello", "caps": {"camera": {"available": true}, ...}, "audio_rate": 48000}
  motion  {"type": "motion", "ax": 0, "ay": 0, "az": 9.8, "rot": 3.1, "t": 12.34}
          (t: the phone's clock, s; rot: how fast it turns, deg/s, or null)
  gps     {"type": "gps", "accuracy": 6.0, "lat": .., "lon": .., "speed": .., "heading": ..}
          (accuracy is null when there is no fix; speed and heading are null when unknown)
  orient  {"type": "orientation", "alpha": .., "beta": .., "gamma": .., "compass": ..}
          (absolute orientation in degrees; compass is the heading an iPhone gives, else null)
  word    {"type": "word", "word": "stop", "confidence": 0.9}   (browser speech recognition)
  button  {"type": "button", "name": "stop" | "go" | "automatic" | "manual"}
  ping    {"type": "ping", "t": 123}   answered with pong; the phone then sends an rtt message
  rtt     {"type": "rtt", "ms": 12}     the round trip it measured
  heard   {"type": "heard", "text": "stop the chair"}   (the phrase, shown as what the user said)
  sensor  {"type": "sensor", "id": "camera", "on": false}   the user tapped a tile
  mic     {"type": "mic", "state": "listening" | "<recogniser error>"}
  radio   {"type": "radio", "wifi": true, "bluetooth": false}   null when the browser cannot tell
Phone to laptop, binary messages: a first byte of 1 then a JPEG frame, or 2 then float32 audio.
Laptop to phone, first: {"type": "config", "frame_ms": 200}, how often to send a camera frame
(--frame-ms). A frame that comes while the models are busy is skipped, so a faster phone builds
no backlog; 200 ms suits a laptop CPU, and a GPU keeps up with 80.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import math
import re
import sys
import threading
import time
import warnings
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "src" / "mana_core", ROOT):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import numpy as np  # noqa: E402
from aiohttp import WSMsgType, web  # noqa: E402
from mana_core.decision import Command, Ground, GroundReport, Source  # noqa: E402

from demo import (  # noqa: E402
    access,
    caregiver,
    csi,
    doorsign,
    floors,
    navigation,
    places,
    recording,
    sounds,
    speech,
    tiles,
    vision,
    wifi,
)
from demo import depth as dz  # noqa: E402
from demo.middleware import Middleware, in_sentence  # noqa: E402

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
    """Mean luminance of a JPEG frame, 0 (black) to 1 (white), the same as the ROS node's."""
    from PIL import Image

    return vision.brightness(Image.open(io.BytesIO(jpeg)).convert("RGB"))


@web.middleware
async def no_cache(request, handler):
    """Pages and scripts are never cached, so an edited page shows up on the next refresh."""
    resp = await handler(request)
    if not request.path.startswith("/ws/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def build_app(
    mw=None,
    detector=None,
    classifier=None,
    recognizer=None,
    depth=None,
    mount=None,
    depth_ground=False,
    recorder=None,
    tile_cache=None,
    key=None,
    listener=None,
    laptop_wifi=None,
    wifi_sensing=None,
    frame_ms=200,
    door_signs=None,
    glass=None,
) -> web.Application:
    """key: what a request must carry (demo.access); None for none, as in the tests."""
    checks = [no_cache] if key is None else [access.middleware(key), no_cache]
    app = web.Application(client_max_size=16 * 1024 * 1024, middlewares=checks)
    app["mw"] = mw or Middleware()
    app["detector"], app["classifier"], app["recognizer"] = detector, classifier, recognizer
    app["listener"] = listener  # sirens and horns on the microphone (demo.sounds), or None
    app["frame_ms"] = frame_ms  # told to the phone: how often to send a camera frame
    app["door_signs"] = door_signs  # reads PUSH or PULL on a door seen (demo.doorsign), or None
    app["glass"] = glass  # glass in the path, pixel by pixel (vision.GlassSegmenter), or None
    # depth from the camera image (#131): the model, how the camera is held, the last result
    app["depth_model"], app["mount"] = depth, mount or dz.Mount()
    app["depth_reader"] = dz.Reader(app["mount"])  # holds the scale learned from level floor
    # the ground read from the depth's shape goes to the core only when asked: on real phone
    # frames it took level floors for ramps and drops (docs/results.md, #131)
    app["depth_ground"] = depth_ground
    app["depth"] = {
        "seq": 0, "jpeg": b"", "seen": None, "t": None, "busy": False, "ms": None, "raw": None,
    }  # fmt: skip
    app["phones"], app["dashboards"], app["caregivers"] = set(), set(), set()
    # what changes while the server runs lives in these dicts: aiohttp deprecates setting the
    # app's own keys once it has started
    app["live"] = {
        "audio_rate": 48000,
        "busy": False,
        "phone_seen": None,
        "snapshot": None,  # the last tick's state, for /api/status
        "stairs": None,  # the detector's last stairs, measured from the camera (#159)
        "door_read_at": None,  # when a door's sign was last read: at most once a second
        "door_reading": False,
        "glass_frames": 0,  # frames in a row with glass in the path (GLASS_FRAMES)
    }
    app["frame"] = {"seq": 0, "jpeg": b"", "boxes": [], "w": 0, "h": 0}
    app["recorder"] = recorder  # keeps what the phone sends (#89), or None
    app["floors"] = floors.FloorTracker()  # which floor, from motion and the camera's stairs
    app["wifi"] = laptop_wifi  # the laptop's own Wi-Fi signal (demo.wifi), or None
    # people from an ESP32's Wi-Fi channel state (demo.csi): sensing, replay, serial; or None
    app["csi"] = wifi_sensing
    app["tiles"] = tile_cache  # street map tiles kept on the laptop (#161), or None: no map
    app["satellite"] = (  # the satellite layer, kept beside them
        None if tile_cache is None else tile_cache.layer("satellite", tiles.SATELLITE, "image/jpeg")
    )
    app["labels"] = (  # place names over the satellite view
        None if tile_cache is None else tile_cache.layer("labels", tiles.LABELS, "image/png")
    )
    # places near the map and their photos, kept beside the tiles (memory only without a folder)
    kept = None if tile_cache is None else tile_cache.folder
    app["places"] = places.Places(None if kept is None else kept / "places")
    app["photos"] = places.Photos(None if kept is None else kept / "photos")

    app.router.add_get("/", index)
    app.router.add_get("/live", live_page)
    app.router.add_get("/indoor", indoor_page)
    app.router.add_get("/api/imagery", imagery)
    app.router.add_get("/api/places", places_near)
    app.router.add_get("/api/photo", photo)
    app.router.add_get("/wifi", wifi_page)
    app.router.add_get("/api/csi", csi_view)
    app.router.add_post("/api/csi/play", csi_play)
    app.router.add_get("/dashboard", dashboard_page)
    app.router.add_get("/phone", phone_page)
    app.router.add_get("/caregiver", caregiver_page)
    app.router.add_get("/ws/phone", ws_phone)
    app.router.add_get("/ws/dashboard", ws_dashboard)
    app.router.add_get("/ws/caregiver", ws_caregiver)
    app.router.add_get("/api/status", status)
    app.router.add_get("/frame.jpg", frame_jpg)
    app.router.add_get("/depth.jpg", depth_jpg)
    app.router.add_get("/api/evidence", evidence)
    app.router.add_get("/api/walk.gpx", walk_gpx)
    app.router.add_get("/api/walk.geojson", walk_geojson)
    app.router.add_get(r"/tiles/{z:\d+}/{x:\d+}/{y:\d+}.png", tile)
    app.router.add_get(r"/tiles/satellite/{z:\d+}/{x:\d+}/{y:\d+}.jpg", tile)
    app.router.add_get(r"/tiles/labels/{z:\d+}/{x:\d+}/{y:\d+}.png", tile)
    app.router.add_static("/static/", WEB, show_index=False)
    app.on_startup.append(start_loop)
    app.on_cleanup.append(stop_loop)
    return app


# ---- pages


async def index(request: web.Request) -> web.Response:
    raise web.HTTPFound("/live")


async def live_page(request: web.Request) -> web.FileResponse:
    """The screen to show: the camera, the decision and the sensors at a glance."""
    return web.FileResponse(WEB / "live.html")


async def indoor_page(request: web.Request) -> web.FileResponse:
    """The second screen: which floor, the height travelled, the newest map and the Wi-Fi."""
    return web.FileResponse(WEB / "indoor.html")


async def wifi_page(request: web.Request) -> web.FileResponse:
    """The third screen: people sensed from Wi-Fi (an ESP32's CSI, live or recorded)."""
    return web.FileResponse(WEB / "wifi.html")


async def csi_view(request: web.Request) -> web.Response:
    c = request.app["csi"]
    if c is None:
        return web.json_response({"on": False})
    out = {"on": True, **c["sensing"].view(), "scores": c.get("scores")}
    out["replay"] = None if c.get("replay") is None else c["replay"].view()
    return web.json_response(out)


async def csi_play(request: web.Request) -> web.Response:
    """Which recording plays: {"label": "two"}, or {"label": null} for the tour."""
    c = request.app["csi"]
    if c is None or c.get("replay") is None:
        raise web.HTTPConflict(text="no recordings: start the server with --csi-replay")
    try:
        c["replay"].choose((await request.json()).get("label"))
    except (KeyError, ValueError, AttributeError, TypeError) as e:
        raise web.HTTPBadRequest(text="label: one of the recordings, or null") from e
    return web.json_response(c["replay"].view())


async def csi_task(app: web.Application) -> None:
    """Feeds the Wi-Fi sensing: recorded sessions at their own pace, or a live ESP32."""
    c = app["csi"]
    loop = asyncio.get_running_loop()
    if c.get("serial"):
        port, baud = c["serial"]
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        stop = threading.Event()  # set on the way out: the reader stops within its 1 s timeout

        def put(p) -> None:
            if not queue.full():  # behind by 10 s: drop rather than raise in the loop
                queue.put_nowait(p)

        def read() -> None:
            parse = csi.LineParser()
            try:
                for line in csi.serial_lines(port, baud, stop):
                    p = parse(line)
                    if p is not None:
                        loop.call_soon_threadsafe(put, p)
                    elif why := parse.unread(time.monotonic()):
                        print(f"wifi: the ESP32 on {port}: {why}", flush=True)
            except Exception as e:  # unplugged, or the port is busy: said once
                if not stop.is_set():
                    print(f"wifi: the ESP32 on {port} stopped ({e!r})", flush=True)

        c["sensing"].restart(f"ESP32 on {port}")
        threading.Thread(target=read, name="esp32", daemon=True).start()
        try:
            while True:
                c["sensing"].feed(await queue.get())
        finally:
            stop.set()
    while True:
        c["replay"].step(loop.time())
        await asyncio.sleep(0.05)


async def imagery(request: web.Request) -> web.Response:
    """When the satellite picture at a point was taken (Esri), kept per ~100 m. 503 offline."""
    try:
        lat, lon = float(request.query["lat"]), float(request.query["lon"])
    except (KeyError, ValueError) as e:
        raise web.HTTPBadRequest(text="lat and lon") from e
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        raise web.HTTPBadRequest(text="lat and lon")
    kept = request.app["live"].setdefault("imagery", {})
    key = (round(lat, 3), round(lon, 3))
    if key not in kept:
        import aiohttp

        url = tiles.IMAGERY_INFO.format(
            lat=lat, lon=lon, lat0=lat - 0.01, lat1=lat + 0.01, lon0=lon - 0.01, lon1=lon + 0.01
        )
        try:
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s,
                s.get(url) as r,
            ):
                body = await r.json(content_type=None)
                # an error is not "no date here": not kept, so the next ask tries again
                if r.status != 200 or not isinstance(body, dict) or "error" in body:
                    raise ValueError(f"imagery service: {r.status}")
                info = tiles.imagery_info(body)
        except (aiohttp.ClientError, TimeoutError, ValueError):
            raise web.HTTPServiceUnavailable(text="no answer from the imagery service") from None
        if len(kept) >= 1000:  # one per ~100 m cell visited: a walk never gets near
            kept.clear()
        kept[key] = info
    if kept[key] is None:
        raise web.HTTPNotFound(text="no date for the imagery here")
    return web.json_response(kept[key])


async def places_near(request: web.Request) -> web.Response:
    """Places around a point (OSM and Wikipedia), kept per ~100 m cell. 503 offline, never seen."""
    try:
        lat, lon = float(request.query["lat"]), float(request.query["lon"])
    except (KeyError, ValueError) as e:
        raise web.HTTPBadRequest(text="lat and lon") from e
    if not (-85 <= lat <= 85 and -180 <= lon <= 180):
        raise web.HTTPBadRequest(text="lat and lon")
    got = await request.app["places"].get(lat, lon)
    if got is None:
        raise web.HTTPServiceUnavailable(text="no answer from OpenStreetMap or Wikipedia")
    return web.json_response(got)


async def photo(request: web.Request) -> web.Response:
    """A place's photo from Wikimedia, through the laptop, which keeps it: the phone needs no
    internet of its own. Only images from Wikimedia's hosts are fetched."""
    try:
        got = await request.app["photos"].get(request.query.get("u", ""))
    except ValueError as e:
        raise web.HTTPBadRequest(text="only Wikimedia images") from e
    if got is None:
        raise web.HTTPNotFound()
    return web.Response(body=got[0], content_type=got[1])


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
    await ws.send_json({"type": "config", "frame_ms": app["frame_ms"]})
    app["phones"].add(ws)
    try:
        async for msg in ws:
            app["live"]["phone_seen"] = app["mw"].clock()
            rec = app["recorder"]
            if msg.type == WSMsgType.TEXT:
                if rec is not None:
                    rec.text(msg.data)
                await on_text(app, ws, msg.data)
            elif msg.type == WSMsgType.BINARY:
                if rec is not None:
                    rec.binary(msg.data)
                await on_binary(app, msg.data)
    finally:
        app["phones"].discard(ws)
        if not app["phones"]:  # no round trip is measured now: not shown as if it were live
            app["mw"].on_phone_gone()
            if app["listener"] is not None:  # nor its last seconds of sound heard again
                app["listener"].clear()
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
        t = d.get("t")
        phone_t = None if t is None else float(t)
        rot = d.get("rot")
        mw.on_motion(
            float(d["ax"]),
            float(d["ay"]),
            float(d["az"]),
            now,
            phone_t,
            None if rot is None else float(rot),
        )
        app["floors"].on_motion(
            now if phone_t is None else phone_t, float(d["ax"]), float(d["ay"]), float(d["az"]), now
        )
    elif kind == "gps":
        acc, lat, lon, speed, heading = (
            number(d.get(k)) for k in ("accuracy", "lat", "lon", "speed", "heading")
        )
        mw.on_gps(acc, now, lat, lon, speed, heading)
        app["floors"].on_gps_altitude(
            now, number(d.get("altitude")), number(d.get("altitude_accuracy"))
        )
    elif kind == "orientation":
        mw.on_orientation(*(number(d.get(k)) for k in ("alpha", "beta", "gamma", "compass")))
    elif kind == "word":
        # the phone sends the whole phrase first ("heard"), so a word is not written down twice;
        # and in a sentence only "stop" acts: "where do I go" is not "go"
        word = str(d.get("word", ""))
        if not app["live"].get("in_sentence") or re.sub(r"[^a-z]", "", word.lower()) == "stop":
            mw.on_word(word, float(d.get("confidence", 0.8)), now, record=False)
    elif kind == "heard":
        text, final = str(d.get("text", "")), bool(d.get("final", True))
        if final:
            app["live"]["in_sentence"] = in_sentence(text)
        mw.on_heard(text, now, final=final)
    elif kind == "button":
        command = BUTTONS.get(str(d.get("name", "")).lower())
        if command is not None:
            mw.on_command(command, 1.0, now, source="button")
    elif kind == "ping":
        mw.on_heartbeat(None, now)
        await ws.send_json({"type": "pong", "t": d.get("t")})
    elif kind == "rtt":
        ms = number(d.get("ms"))  # as the phone says it: never below 0, never not a number
        if ms is not None:
            mw.on_heartbeat(max(0.0, ms), now)
            app["live"]["rtt_ms"] = max(0.0, ms)  # half of it: how long ago a frame was taken
    elif kind == "sensor":
        mw.on_sensor_switch(str(d.get("id", "")), bool(d.get("on")), now)
    elif kind == "mic":
        mw.on_microphone_state(str(d.get("state", "")), now)
    elif kind == "radio":
        mw.on_radio(d.get("wifi"), d.get("bluetooth"), now)


def number(v) -> float | None:
    """A finite number from the phone, or None for null, missing or not a number."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


async def on_binary(app: web.Application, data: bytes) -> None:
    if not data:
        return
    mw: Middleware = app["mw"]
    tag, payload = data[0], data[1:]
    now = mw.clock()
    if tag == 1:
        # every report from this frame is stamped when it was taken, about half the round trip
        # before it came, not when a model finished with it: then the core's max_age_s also
        # counts the network and the models, and a late report is dropped (readiness review)
        taken = now - (app["live"].get("rtt_ms") or 0.0) / 2000.0
        await on_frame(app, payload, taken)
    elif tag == 2:
        samples = array("f")
        samples.frombytes(payload[: len(payload) // 4 * 4])
        mw.on_microphone(now)
        hear(app, samples, now)
        if app["recognizer"] is not None:
            pcm = speech.to_pcm16_16k(list(samples), app["live"]["audio_rate"])
            words = app["recognizer"].feed(pcm)
            phrase = app["recognizer"].last_phrase
            if phrase:  # "mark this place", "floor two": in a sentence only "stop" acts, so
                mw.on_heard(phrase, now)  # "on the first floor" does not switch modes
                words = [(w, c) for w, c in words if w == "stop"]
            for word, confidence in words:
                mw.on_word(word, confidence, now)


def hear(app: web.Application, samples, now: float) -> None:
    """Keep the sound, and once a second score the last 10 s for sirens and horns, off the loop."""
    listener = app["listener"]
    if listener is None:
        return
    listener.feed(np.frombuffer(samples, dtype=np.float32), app["live"]["audio_rate"])
    if listener.due(now) and not app["live"].get("hearing"):
        app["live"]["hearing"] = True
        asyncio.get_running_loop().create_task(hear_task(app, now))


async def hear_task(app: web.Application, now: float) -> None:
    try:
        heard = await asyncio.get_running_loop().run_in_executor(None, app["listener"].hear, now)
        app["mw"].on_sounds(heard)
    except Exception as e:  # a broken model: said once, not as an unhandled task every second
        if not app["live"].get("hear_failed"):
            app["live"]["hear_failed"] = True
            print(f"sounds: the model failed ({e!r}); sirens and horns are not heard", flush=True)
    finally:
        app["live"]["hearing"] = False


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
    if app["depth_model"] is not None and not app["depth"]["busy"]:
        app["depth"]["busy"] = True  # its own worker: the detector does not wait for it
        asyncio.get_running_loop().create_task(depth_task(app, jpeg, now))
    if app["live"]["busy"] or (app["detector"] is None and app["classifier"] is None):
        return
    app["live"]["busy"] = True
    # not awaited: the phone's next messages (a stop, the tilt, GPS, heartbeats) must not wait for
    # the detector, which takes a few hundred milliseconds a frame, or seconds on a busy laptop
    app["live"]["analysing"] = asyncio.get_running_loop().create_task(analyse_task(app, jpeg, now))


async def analyse_task(app: web.Application, jpeg: bytes, now: float) -> None:
    try:
        await asyncio.get_running_loop().run_in_executor(None, analyse, app, jpeg, now)
    except Exception as e:  # a model failing on one frame must not stop the server
        print(f"analyse: {e}", file=sys.stderr)
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
        per_box: dict[int, float] = {}
        found = obstacles(app, detections, w, h, now, per_box)
        glass = glass_ahead(app, jpeg, w, h)
        if glass is not None:  # among the detector's obstacles: the nearest is sent last
            found = sorted([*found, glass], key=lambda o: o.distance_m)
        nearest: dict[str, float] = {}
        for ob in found:
            nearest.setdefault(ob.label, ob.distance_m)
        # farthest first: the core keeps the newest report of the detector, so the nearest
        # obstacle must be the last one it hears
        for ob in reversed(found):  # stamped when the frame was taken (on_frame), as below
            mw.on_obstacle(ob.label, ob.distance_m, ob.confidence, ob.bearing_rad, now)
        mw.on_detections([(d.label, nearest.get(d.label)) for d in detections], now)
        stairs_seen(app, detections, w, h, now)
        door_sign(app, jpeg, detections, now)
        frame["boxes"] = [
            {"label": d.label, "confidence": round(d.confidence, 2), "x1": d.x1, "y1": d.y1,
             "x2": d.x2, "y2": d.y2,
             "distance_m": None if i not in per_box else round(per_box[i], 1)}
            for i, d in enumerate(detections)
        ]  # fmt: skip
        if glass is not None:
            box = app["glass"].in_path(w, h)
            frame["boxes"].append(
                {"label": GLASS, "confidence": round(glass.confidence, 2), "x1": box.x1,
                 "y1": box.y1, "x2": box.x2, "y2": box.y2,
                 "distance_m": round(glass.distance_m, 1)}
            )  # fmt: skip
    if app["classifier"] is not None:
        extra = {}
        if getattr(app["classifier"], "wants_depth", False):
            # the model's own depth for this frame, raw as it was trained on, if it is that recent
            d = app["depth"]
            fresh = d["t"] is not None and now - d["t"] <= DEPTH_FOR_STAIRS_S
            extra["depth"] = d["raw"] if fresh else None
        label, conf = app["classifier"].classify(jpeg, **extra)
        ground = Ground.STAIRS_UP if vision.stair_ground(label) == "stairs" else Ground.FLAT
        region = getattr(app["classifier"], "region", None)  # the segmenter's stairs, as a box
        box = None
        if region is not None and ground == Ground.STAIRS_UP:
            w, h = frame.get("w") or 0, frame.get("h") or 0
            if not (w and h):
                from PIL import Image

                with Image.open(io.BytesIO(jpeg)) as im:
                    w, h = im.size
                frame.update(w=w, h=h)
            box = region(w, h)
        if box is None:
            mw.on_ground(Source.CLASSIFIER, ground, conf, now=now)
        else:
            # where and which way, as for the detector's stairs box: the floor under its bottom
            # edge, and the depth's height of its middle
            pitch = None if mw.gravity is None else dz.camera_pitch(*mw.gravity)
            d = app["depth"]
            seen = d["seen"] if d["t"] is not None and now - d["t"] <= 1.5 else None
            code, distance = dz.stairs_ground(box, w, h, app["mount"], pitch, seen)
            mw.on_ground(Source.CLASSIFIER, Ground(code), conf, distance, now)
            frame["boxes"] = [b for b in frame.get("boxes", []) if b["label"] != SEGMENTED] + [
                {"label": SEGMENTED, "confidence": round(conf, 2), "x1": box.x1, "y1": box.y1,
                 "x2": box.x2, "y2": box.y2,
                 "distance_m": None if distance != distance else round(distance, 1)}
            ]  # fmt: skip


# ---- the walk, to keep


def wall_of(app: web.Application):
    """Turns the middleware's clock (monotonic) into Unix time, for the times in a saved walk."""
    import time

    offset = time.time() - app["mw"].clock()
    return lambda t: None if t is None else t + offset


async def walk_gpx(request: web.Request) -> web.Response:
    gpx = navigation.to_gpx(request.app["mw"].nav.track, wall_of(request.app))
    if gpx is None:
        raise web.HTTPConflict(
            text="no GPS fix yet: GPX needs latitude and longitude; take GeoJSON"
        )
    disposition = 'attachment; filename="mana-walk.gpx"'
    return web.Response(text=gpx, content_type="application/gpx+xml",
                        headers={"Content-Disposition": disposition})  # fmt: skip


async def tile(request: web.Request) -> web.Response:
    """A map tile, street, satellite or place names: from the laptop's copy, or fetched when
    online (#161)."""
    layer = request.path.split("/")[2]
    cache = request.app[layer if layer in ("satellite", "labels") else "tiles"]
    if cache is None:
        raise web.HTTPNotFound()
    try:
        z, x, y = (int(request.match_info[k]) for k in "zxy")
        data = await cache.get(z, x, y)
    except ValueError as e:
        raise web.HTTPBadRequest() from e
    if data is None:  # offline and never seen: the dashboard keeps its grid
        raise web.HTTPNotFound()
    return web.Response(body=data, content_type=cache.content_type)


async def walk_geojson(request: web.Request) -> web.Response:
    data = navigation.to_geojson(request.app["mw"].nav.track, wall_of(request.app))
    disposition = 'attachment; filename="mana-walk.geojson"'
    return web.json_response(data, content_type="application/geo+json",
                             headers={"Content-Disposition": disposition})  # fmt: skip


GLASS = "glass (pixels)"  # the glass segmenter's glass on the dashboard
# frames in a row with glass in the path before it is told: on a real walk at home, 7 of the 13
# times it called a glossy wardrobe or a screen glass lasted 1 or 2 frames (docs/results.md)
GLASS_FRAMES = 3


def glass_ahead(app: web.Application, jpeg: bytes, w: int, h: int):
    """Glass in the path as an obstacle, or None. Its distance is where its bottom edge meets
    the floor, from the camera's height and tilt, as for the detector's boxes without depth: a
    depth camera may see through the glass."""
    seg, mw = app["glass"], app["mw"]
    pitch = None if seg is None or mw.gravity is None else dz.camera_pitch(*mw.gravity)
    if pitch is None:
        return None
    seg.segment(jpeg)
    box = seg.in_path(w, h)
    live = app["live"]
    if box is None or box.confidence < MIN_OBSTACLE_CONFIDENCE:
        live["glass_frames"] = 0
        return None
    live["glass_frames"] += 1
    if live["glass_frames"] < GLASS_FRAMES:
        return None
    distance = dz.floor_at(app["mount"].camera(w, h, pitch), box.y2)
    if distance is None:  # its bottom edge above the horizon: not on the floor ahead
        return None
    bearing = vision.bearing_rad((box.x1 + box.x2) / 2.0, w)
    return vision.Obstacle("glass", box.confidence, distance, bearing)


DOORS = ("door", "glass door")
DOOR_CONFIDENCE = 0.3
DOOR_READ_S = 1.0  # OCR takes longer than the detector: a door's sign is read this often
# and the whole frame this often when no door is boxed: the detector finds doors poorly (about
# 0.04 AP50 on SANPO), and on 80 real photos without the words the reader never said push or pull
FRAME_READ_S = 2.0


def door_sign(app: web.Application, jpeg: bytes, detections, now: float) -> None:
    """Read the sign of the largest door in view, or the whole frame when none is boxed, in its
    own thread so that the next frame does not wait for it; the middleware says which way the
    door opens."""
    reader, live = app["door_signs"], app["live"]
    if reader is None or live["door_reading"]:
        return
    doors = [d for d in detections if d.label in DOORS and d.confidence >= DOOR_CONFIDENCE]
    wait = DOOR_READ_S if doors else FRAME_READ_S
    if live["door_read_at"] is not None and now - live["door_read_at"] < wait:
        return
    door = max(doors, key=lambda d: (d.x2 - d.x1) * (d.y2 - d.y1)) if doors else None
    try:  # the reader runs in its own thread; what it says goes back to the server's loop
        loop = asyncio.get_running_loop()
    except RuntimeError:  # called with no loop running (a test): said from the thread
        loop = None
    box = None if door is None else (door.x1, door.y1, door.x2, door.y2)
    live["door_reading"], live["door_read_at"] = True, now

    def read():
        try:
            side, _ = reader.read(jpeg, box)
            if side is None and box is not None:
                # the detector may box another door than the one with the sign, or the sign may
                # be on the wall beside it: on a door photo with PULL drawn on, the detector boxed
                # the door next to it, and only the whole frame read the sign
                side, _ = reader.read(jpeg)
            # with a door boxed it is a door's sign, even when found beside its box; with none,
            # the text may be on anything, so only what it says is told
            if side is not None and loop is not None:
                loop.call_soon_threadsafe(app["mw"].on_door_sign, side, None, box is not None)
            elif side is not None:
                app["mw"].on_door_sign(side, None, box is not None)
        except Exception as e:  # one bad frame must not stop the reading
            print(f"door sign: {e}", file=sys.stderr)
        finally:
            live["door_reading"] = False

    threading.Thread(target=read, daemon=True).start()


STAIRS_CONFIDENCE = 0.3  # YOLOE's stairs: no false alarm on 16 real level and ramp frames
SEGMENTED = "stairs (pixels)"  # the segmenter's stairs on the dashboard
MIN_OBSTACLE_CONFIDENCE = 0.4  # as vision.to_obstacles
DEPTH_FOR_STAIRS_S = (
    0.5  # depth older than this is from another view: the stairs model goes without
)
# the depth sees stairs up as something in the path from the second or third riser: on real frames
# 0.2 to 1.3 m past where they start (#159)
STAIRS_RISE_M = 1.5


def the_stairs(report, ahead_m: float, now: float) -> bool:
    """The depth's something in the path is the stairs up that the detector saw in the last second:
    from where they start to STAIRS_RISE_M past it. Not an obstacle, so the stairs rule handles
    them; something nearer than the stairs, or further past them, still is. Stairs without a
    distance (no tilt) cannot be placed, and the obstacle stays. Both distances are from the
    camera: the report is the one the server made (app["live"]["stairs"]), not the core's copy,
    which counts from the device's front (Waqas, #160)."""
    return (
        report is not None
        and report.ground == Ground.STAIRS_UP
        and 0.0 <= now - report.stamp <= 1.0
        and math.isfinite(report.distance_m)
        and report.distance_m - 0.2 <= ahead_m <= report.distance_m + STAIRS_RISE_M
    )


def stairs_seen(app: web.Application, detections, w: int, h: int, now: float) -> None:
    """The nearest stairs the detector sees, as the camera's ground report: what it is from the
    box, where from the floor under its bottom edge (the camera's height and tilt), and up or
    down from the depth when it is there."""
    stairs = [d for d in detections if d.label == "stairs" and d.confidence >= STAIRS_CONFIDENCE]
    if not stairs:
        return
    mw: Middleware = app["mw"]
    box = max(stairs, key=lambda d: d.y2)  # the lowest bottom edge is the nearest
    pitch = None if mw.gravity is None else dz.camera_pitch(*mw.gravity)
    d = app["depth"]
    seen = d["seen"] if d["t"] is not None and now - d["t"] <= 1.5 else None
    ground, distance = dz.stairs_ground(box, w, h, app["mount"], pitch, seen)
    stamp = now  # when the frame was taken
    app["live"]["stairs"] = GroundReport(
        stamp, box.confidence, Source.DETECTOR, Ground(ground), distance
    )
    mw.on_ground(Source.DETECTOR, Ground(ground), box.confidence, distance, stamp)


# Things that stand on the floor in the path but have no typical height for the box-height
# distance (vision.OBSTACLE_HEIGHT_M). Without one they were never obstacles: a box, a pole or a mop
# the detector named still did not slow the device. Things on a desk or in a hand, and doors, are
# left out: their box's bottom edge is not where they meet the floor ahead.
ON_THE_FLOOR = {
    "box", "pole", "pillar", "shelf", "cabinet", "backpack", "handbag", "mop", "broom", "bucket",
    "squeegee", "wiper", "plastic bag", "cable", "wet floor sign", "ladder", "cart",
}  # fmt: skip


def on_the_floor(app: web.Application, det, w: int, h: int, seen):
    """An obstacle for a detection in ON_THE_FLOOR: how far from the depth scaled to the floor,
    else from where the box's bottom edge meets the floor (the camera's height and tilt). None
    without either, or below the detector's confidence for obstacles."""
    if det.label not in ON_THE_FLOOR or det.confidence < MIN_OBSTACLE_CONFIDENCE:
        return None
    distance = None if seen is None else dz.box_distance(seen, det, w, h)
    if distance is None:
        mw: Middleware = app["mw"]
        if mw.gravity is not None:
            cam = app["mount"].camera(w, h, dz.camera_pitch(*mw.gravity))
            distance = dz.floor_at(cam, det.y2)
    if distance is None:
        return None
    bearing = vision.bearing_rad((det.x1 + det.x2) / 2.0, w)
    return vision.Obstacle(det.label, det.confidence, distance, bearing)


# an obstacle is in the walker's path when its nearer side is within this of the line straight
# ahead: about half a person's width with the cane's sweep, and a margin (readiness review)
PATH_HALF_M = 0.6


def obstacles(
    app: web.Application, detections, w: int, h: int, now: float, per_box: dict | None = None
) -> list:
    """The detector's obstacles, nearest first. Each distance is the box height's estimate, or the
    depth image's (one from the last 1.5 s, scaled to the floor) when that is nearer: on 413 boxes
    of real SANPO walks the depth was better from 2 m out, but read 1.5 times too far under 2 m,
    where box height did better; the nearer of the two read over 20 percent too far on 6 percent
    of the boxes (#88, docs/decisions.md D12). per_box, when given, gets each detection's distance
    by its place in detections, for the dashboard."""
    d = app["depth"]
    seen = d["seen"] if d["t"] is not None and now - d["t"] <= 1.5 else None
    found = []
    for i, det in enumerate(detections):
        estimate = vision.to_obstacles([det], w, h)
        if not estimate:
            ob = on_the_floor(app, det, w, h, seen)
            if ob is None:
                continue
        else:
            ob = estimate[0]
            measured = None if seen is None else dz.box_distance(seen, det, w, h)
            if measured is not None and measured < ob.distance_m:
                ob = vision.Obstacle(ob.label, ob.confidence, measured, ob.bearing_rad)
        if per_box is not None:
            per_box[i] = ob.distance_m
        if vision.side_gap_m(det, ob.distance_m, w) <= PATH_HALF_M:  # beside the path: shown only
            found.append(ob)
    return sorted(found, key=lambda o: o.distance_m)


async def depth_task(app: web.Application, jpeg: bytes, taken: float | None = None) -> None:
    try:
        await asyncio.get_running_loop().run_in_executor(None, depth_step, app, jpeg, taken)
    except Exception as e:  # a bad frame or the model failing must not stop the server
        print(f"depth: {e}", file=sys.stderr)
    finally:
        app["depth"]["busy"] = False


def depth_step(app: web.Application, jpeg: bytes, taken: float | None = None) -> None:
    """Depth for one frame (in a worker thread): the ground and the nearest thing in the path go to
    the middleware as the camera's own reports, stamped when the frame was taken (taken; now when
    not given): a slow model's reports then come late and are dropped by the core, not believed."""
    mw: Middleware = app["mw"]
    started = mw.clock()
    image = app["depth_model"].estimate(jpeg)
    pitch = None if mw.gravity is None else dz.camera_pitch(*mw.gravity)
    seen = app["depth_reader"].read(image, pitch, mw.nav.track.steps)
    mw.latency["depth_ms"] = round((mw.clock() - started) * 1000, 1)
    now = mw.clock() if taken is None else taken
    r = seen.reading
    if app["depth_ground"] and r is not None and r.ground != Ground.UNKNOWN:
        mw.on_ground(Source.CAMERA, Ground(r.ground), 0.8, r.distance_m, now, r.step_height_m)
    stairs = seen.ahead_m is not None and the_stairs(app["live"]["stairs"], seen.ahead_m, now)
    if seen.ahead_m is not None and not stairs:
        mw.on_obstacle("something", seen.ahead_m, 0.8, 0.0, now, source=Source.CAMERA)
    if seen.cam is not None and not stairs:  # which way is open, when the way ahead is blocked
        left, _, right = dz.ahead_by_side(seen.depth, seen.cam)
        mw.on_way(left, seen.ahead_m, right, now)
    d = app["depth"]
    d.update(seq=d["seq"] + 1, jpeg=dz.colour(seen.depth), seen=seen, t=now, raw=image)
    d["view"] = {
        "seq": d["seq"],
        "ms": mw.latency["depth_ms"],
        "pitch_deg": None if pitch is None else round(math.degrees(pitch)),
        "scale": None if seen.scale is None else round(seen.scale, 2),
        "ground": None if r is None else Ground(r.ground).name.lower(),
        "distance_m": None if r is None or math.isnan(r.distance_m) else round(r.distance_m, 2),
        "step_m": None if r is None or math.isnan(r.step_height_m) else round(r.step_height_m, 2),
        "ahead_m": None if seen.ahead_m is None else round(seen.ahead_m, 2),
        "ahead_is_stairs": stairs,  # then not sent as an obstacle (#159)
        "why": seen.why,
        "sent": app["depth_ground"],  # the ground goes to the core only with --depth-ground
    }


async def depth_jpg(request: web.Request) -> web.Response:
    jpeg = request.app["depth"]["jpeg"]
    if not jpeg:
        raise web.HTTPNotFound()
    return web.Response(body=jpeg, content_type="image/jpeg")


# ---- the dashboard and the test evidence


def metres(x, y, within_m: float = 20_000.0) -> tuple[float, float] | None:
    """A point picked on the map, metres east and north of the start; None unless both are
    finite numbers within reach."""
    if not all(isinstance(v, int | float) and not isinstance(v, bool) for v in (x, y)):
        return None
    try:  # a JSON integer can be too large for a float (10**400): refused, not raised (#188)
        x, y = float(x), float(y)
    except OverflowError:
        return None
    if not (math.isfinite(x) and math.isfinite(y)) or math.hypot(x, y) > within_m:
        return None
    return x, y


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
            elif d.get("type") == "carried" and isinstance(d.get("on_foot"), bool):
                app["mw"].set_on_foot(d["on_foot"])
            elif d.get("type") == "place" and d.get("do") == "mark":
                app["mw"].mark_place()
            elif d.get("type") == "place" and d.get("do") == "way":
                app["mw"].way_back()
            elif d.get("type") == "place" and d.get("do") == "destination":
                if (where := metres(d.get("x"), d.get("y"))) is not None:
                    app["mw"].set_destination(*where)
            elif d.get("type") == "place" and d.get("do") == "clear":
                app["mw"].clear_destination()
            elif d.get("type") == "place" and d.get("do") == "restart":
                app["mw"].start_again()
                old = app["floors"]  # the floor stays; its history and height trace go
                app["floors"] = floors.FloorTracker(floor=old.floor, floor_h=old.floor_h)
            elif (
                d.get("type") == "floor"
                and isinstance(d.get("set"), int)
                and not isinstance(d["set"], bool)  # JSON true is an int to Python
                and -5 <= d["set"] <= 60
            ):
                app["floors"].set_floor(app["mw"].clock(), d["set"])
            elif d.get("type") == "floor" and (h := number(d.get("height"))) and 2.4 <= h <= 6.0:
                app["floors"].floor_h = h
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
    snap = app["live"]["snapshot"]
    if snap is None:
        raise web.HTTPServiceUnavailable(text="starting up")
    return web.json_response(caregiver_view(app, snap))


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
        "depth": app["depth_model"] is not None,
        "sounds": app["listener"] is not None,
        "door signs": app["door_signs"] is not None,
        "glass": app["glass"] is not None,
    }


def floor_step(app: web.Application, snap: dict) -> dict:
    """Feed the floor tracker what the tick knows: the steps, and the stairs the camera saw (each
    report once, with its distance: the tracker counts only stairs right in front); then its view
    for the pages."""
    tr, mw = app["floors"], app["mw"]
    now = mw.clock()
    tr.on_steps(now, mw.nav.track.steps)
    tr.on_position_source(now, snap["position"]["track"]["source"])
    seen = app["live"]["stairs"]
    if seen is not None and seen.stamp != app["live"].get("stairs_fed"):
        app["live"]["stairs_fed"] = seen.stamp
        way = {Ground.STAIRS_UP: 1, Ground.STAIRS_DOWN: -1}.get(seen.ground)
        if way:
            tr.on_stairs(now, way, seen.distance_m)
    said = mw.floor_said
    if said is not None and said[1] != app["live"].get("floor_said_t"):
        app["live"]["floor_said_t"] = said[1]
        tr.set_floor(now, said[0], "said by the user", "voice")
    tr.tick(now)
    return tr.view(now)


async def broadcast(app: web.Application) -> None:
    mw: Middleware = app["mw"]
    snap = mw.tick()
    snap["models"] = models_status(app)
    snap["floors"] = floor_step(app, snap)
    snap["phone_online"] = bool(app["phones"])
    wifi_now = app["wifi"]
    if wifi_now is not None:
        if mw.clock() - app["live"].get("wifi_read", -9.0) >= 0.5:  # two reads a second
            app["live"]["wifi_read"] = mw.clock()
            wifi_now.sample(mw.clock())
        snap["wifi_laptop"] = wifi_now.view()
    app["live"]["snapshot"] = snap
    snap["frame"] = {"seq": app["frame"]["seq"], "w": app["frame"]["w"], "h": app["frame"]["h"],
                     "boxes": app["frame"]["boxes"]}  # fmt: skip
    snap["depth"] = app["depth"].get("view")
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
    if app["csi"] is not None:
        app["live"]["csi_task"] = asyncio.create_task(csi_task(app))


async def stop_loop(app: web.Application) -> None:
    task = app["live"].get("csi_task")
    if task is not None:
        task.cancel()
    app["loop"].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await app["loop"]
    for cache in (app["tiles"], app["satellite"], app["labels"]):
        if cache is not None:
            await cache.close()
    if app["recorder"] is not None:
        app["recorder"].close()
    if app["wifi"] is not None:
        app["wifi"].close()


# ---- command line


def wifi_sensing_from(a) -> dict | None:
    """The Wi-Fi sensing set up from the command line: None without --csi-replay or --csi-serial."""
    if not (a.csi_replay or a.csi_serial):
        return None
    model_path = Path(a.csi_model)
    model = csi.ZoneModel.load(model_path) if model_path.exists() else None
    scores_path = model_path.with_suffix(".json")
    scores = json.loads(scores_path.read_text(encoding="utf-8")) if scores_path.exists() else None
    sensing = csi.Sensing(model)
    out = {"sensing": sensing, "scores": scores, "replay": None, "serial": None}
    if a.csi_serial:
        try:
            import serial  # noqa: F401
        except ImportError:
            raise SystemExit("--csi-serial needs pyserial: pip install pyserial") from None
        out["serial"] = (a.csi_serial, a.csi_baud)
    else:  # the held-out sessions: the model never saw them
        folder = Path(a.csi_replay)
        recs = {k: folder / f"{k}_2.csv" for k in ("empty", "one", "two", "five")}
        recs = {k: v for k, v in recs.items() if v.exists()}
        if not recs:
            raise SystemExit(f"--csi-replay: no *_2.csv recordings in {folder}")
        out["replay"] = csi.Replay(recs, sensing)
    print(
        f"wifi sensing: {'ESP32 on ' + a.csi_serial if a.csi_serial else 'recordings'}, "
        f"model {'loaded' if model else 'not found: ' + str(model_path)}",
        flush=True,
    )
    return out


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description="MANA phone demo server")
    p.add_argument("--host", default="127.0.0.1", help="the phone arrives through the USB cable")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--weights", default="yolo26n.pt", help="object detector weights (Ultralytics)")
    p.add_argument("--imgsz", type=int, default=416, help="detector input size; 640 finds more")
    p.add_argument(
        "--frame-ms",
        type=int,
        default=200,
        help="how often the phone sends a camera frame; 80 when the models run on a GPU",
    )
    p.add_argument("--no-detector", action="store_true")
    p.add_argument("--vosk-model", help="folder of a Vosk model, for offline voice commands")
    p.add_argument("--kws", metavar="MODEL",
                   help="BC-ResNet checkpoint, for offline command words")  # fmt: skip
    p.add_argument("--kws-threshold", type=float, default=0.9,
                   help="the least probability of a command word to act on")  # fmt: skip
    p.add_argument("--kws-stop-threshold", type=float, default=0.7,
                   help="the least probability of stop, lower: a missed stop is the unsafe "
                        "case (0.7 for the 100-epoch model: docs/results.md); the cost: at "
                        "10 dB about 1 in 65 other words is heard as stop")  # fmt: skip
    p.add_argument("--stairs-model", help="ONNX file of the stairs classifier")
    p.add_argument(
        "--stairs-seg",
        help="weights of the stairs segmentation (tools/train_stair_seg.py); used instead of "
        "--stairs-model, on the GPU when there is one",
    )
    p.add_argument(
        "--glass-seg", help="weights of the glass segmentation (tools/train_glass_seg.py)"
    )
    p.add_argument("--door-signs", action="store_true",
                   help="read PUSH or PULL on doors seen (needs rapidocr_onnxruntime)")  # fmt: skip
    p.add_argument("--sounds", metavar="EFFICIENTAT",
                   help="EfficientAT checkout, to hear sirens and horns")  # fmt: skip
    p.add_argument("--depth", nargs="?", const=dz.MODEL, metavar="MODEL",
                   help="depth from the camera image (Depth Anything V2 by default)")  # fmt: skip
    p.add_argument("--depth-size", type=int, default=266, help="depth model input; 518 is finer")
    p.add_argument("--depth-ground", action="store_true",
                   help="also send the ground read from the depth's shape (unreliable on "
                        "real frames, see docs/results.md)")  # fmt: skip
    p.add_argument("--camera-height", type=float, default=1.2,
                   help="metres from the floor to the camera, as it is held")  # fmt: skip
    p.add_argument("--camera-fov", type=float, default=66.0,
                   help="degrees across the long side of the picture")  # fmt: skip
    p.add_argument("--tile-cache", metavar="FOLDER", default="tile-cache",
                   help="where the street map tiles are kept, for the map offline")  # fmt: skip
    p.add_argument("--https", action="store_true",
                   help="serve https on the network, so a phone on the same Wi-Fi can open it "
                        "with no cable (demo/certs.py)")  # fmt: skip
    p.add_argument("--record", metavar="FOLDER",
                   help="keep what the phone sends, to play with demo.replay")  # fmt: skip
    p.add_argument("--on-foot", action="store_true",
                   help="the phone is carried by a person walking, not on the device")  # fmt: skip
    p.add_argument(
        "--csi-replay",
        metavar="FOLDER",
        help="Wi-Fi sensing from recorded ESP32 CSI (tools/wifi_csi.py --fetch)",
    )
    p.add_argument("--csi-serial", metavar="PORT", help="Wi-Fi sensing from a live ESP32 (esp-csi)")
    p.add_argument("--csi-baud", type=int, default=115200)
    p.add_argument(
        "--csi-model",
        default=str(Path(__file__).parent / "csi_model" / "wifi_csi_office.npz"),
        help="the model from tools/wifi_csi.py; it holds for the room it was fitted in",
    )
    p.add_argument("--directions", action="store_true",
                   help="also say what is ahead, for a smart cane or glasses host")  # fmt: skip
    a = p.parse_args(argv)

    try:  # before the models, which take a while to load
        recorder = recording.Recorder(a.record) if a.record else None
    except FileExistsError:
        p.error(f"{a.record} already has a recording: give a new folder")
    detector = None if a.no_detector else vision.Detector.load(a.weights, a.imgsz)
    classifier = vision.GroundClassifier.load(a.stairs_model) if a.stairs_model else None
    if a.stairs_seg:
        classifier = vision.StairSegmenter.load(a.stairs_seg)
    recognizer = speech.CommandRecognizer.load(a.vosk_model) if a.vosk_model else None
    if a.kws:  # needs torch and torchaudio
        from demo import kws

        recognizer = kws.Spotter.load(a.kws, a.kws_threshold, a.kws_stop_threshold)
    depth = dz.DepthModel.load(a.depth, a.depth_size) if a.depth else None
    listener = sounds.Listener(sounds.load(a.sounds)) if a.sounds else None
    if listener is not None and recognizer is None:
        print(
            "sounds: the phone now streams its microphone for sirens and horns, so its own voice "
            "recognition is off; add --vosk-model for voice commands",
            flush=True,
        )
    mount = dz.Mount(a.camera_height, a.camera_fov)
    key = access.load_or_make()  # the server can drive the device: no answer without it
    app = build_app(
        Middleware(directions=a.directions, on_foot=a.on_foot),
        detector,
        classifier,
        recognizer,
        depth,
        mount,
        depth_ground=a.depth_ground,
        recorder=recorder,
        tile_cache=tiles.TileCache(a.tile_cache),
        key=key,
        listener=listener,
        frame_ms=max(30, a.frame_ms),
        laptop_wifi=wifi.LaptopWifi(),
        wifi_sensing=wifi_sensing_from(a),
        door_signs=doorsign.DoorSign.load() if a.door_signs else None,
        glass=vision.GlassSegmenter.load(a.glass_seg) if a.glass_seg else None,
    )
    if a.record:
        print(f"recording the phone into {a.record}")
    ssl_context, host = None, a.host
    if a.https:
        from demo import certs

        ssl_context = certs.context()
        host = "0.0.0.0" if a.host == "127.0.0.1" else a.host
        ips = certs.addresses()
        if ips:
            url = access.with_key(f"https://{ips[0]}:{a.port}/phone", key)
            print(f"phone:     {url}   (same Wi-Fi; accept the warning)")
        for ip in ips[1:]:
            url = access.with_key(f"https://{ip}:{a.port}/phone", key)
            print(f"           {url}   (another network of this laptop)")
    scheme = "https" if a.https else "http"
    print(f"live:      {access.with_key(f'{scheme}://localhost:{a.port}/live', key)}")
    print(f"dashboard: {access.with_key(f'{scheme}://localhost:{a.port}/dashboard', key)}")
    if not a.https:
        url = access.with_key(f"http://localhost:{a.port}/phone", key)
        print(f"phone:     {url}   (after: python -m demo.usb)")
    url = access.with_key(f"{scheme}://localhost:{a.port}/caregiver", key)
    print(f"caregiver: {url}   (read-only)")
    print("The key (k) lets anyone with it drive the device: share it with no one.")
    for name, on in models_status(app).items():
        print(f"model {name}: {'loaded' if on else 'not available'}")
    web.run_app(app, host=host, port=a.port, ssl_context=ssl_context, print=None)


if __name__ == "__main__":
    main()
