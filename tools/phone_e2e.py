"""The phone demo end to end, with no phone: headless Chrome opens the real phone page with a video
file as its camera, and its tilt, compass and GPS overridden, walking at 1.8 steps a second.

    python -m demo.server --depth --weights yoloe-26s-mana.pt --imgsz 480 --on-foot   # first
    python tools/phone_e2e.py --video stairs.mp4 --seconds 60 --out e2e

Every few seconds it prints what the dashboard shows (the depth line, the position line, the last
decision) and saves a screenshot of it; at the end one of the phone page. The phone page and the
dashboard run in two Chrome instances: a phone page in a background tab is hidden, and Chrome then
stops its motion sensors and GPS.

It found three bugs the unit tests did not: the phone's messages waited for the detector (#141),
the depth scale was never learned on real frames (#142), and a phone held upright stayed on tracks
(#143). Needs Google Chrome and aiohttp; OpenCV to turn a video into the MJPEG Chrome plays.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

G = 9.81
CHROMES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_chrome() -> str | None:
    return next((c for c in CHROMES if Path(c).exists()), None) or shutil.which("google-chrome")


def to_mjpeg(video: Path, out: Path, width: int = 640, fps: float = 30.0) -> Path:
    """A video (any format OpenCV reads) as the MJPEG file Chrome's fake camera plays: the JPEGs
    one after another, 30 a second. An .mjpeg file is used as it is."""
    if video.suffix.lower() in (".mjpeg", ".mjpg", ".y4m"):
        return video
    import cv2

    cap = cv2.VideoCapture(str(video))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    with out.open("wb") as f:
        n, t = 0, 0.0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            frame = cv2.resize(frame, (width, round(h * width / w)))
            jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
            t += fps / src_fps  # repeat or skip frames to play at the video's own speed
            while n < t:
                f.write(jpeg)
                n += 1
    cap.release()
    return out


class Tab:
    """One Chrome page over the DevTools protocol."""

    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method: str, **params) -> dict:
        self.n += 1
        await self.ws.send_json({"id": self.n, "method": method, "params": params})
        async for msg in self.ws:
            d = json.loads(msg.data)
            if d.get("id") == self.n:
                return d.get("result", {})
        return {}


async def chrome(session, port: int, *flags: str):
    """A headless Chrome on this debugging port, and its first page."""
    proc = subprocess.Popen(
        [find_chrome(), "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={tempfile.mkdtemp()}", *flags, "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    for _ in range(100):
        try:
            tabs = await (await session.get(f"http://127.0.0.1:{port}/json")).json()
            break
        except Exception:  # not up yet
            await asyncio.sleep(0.2)
    else:
        proc.kill()
        raise SystemExit("Chrome did not start")
    page = next(t for t in tabs if t["type"] == "page")
    return proc, await session.ws_connect(page["webSocketDebuggerUrl"], max_msg_size=0)


async def walk(phone: Tab, pitch: float, lat: float, lon: float, speed: float = 1.1) -> None:
    """The phone held pitch radians down, walking north at speed m/s, 1.8 steps a second."""
    t = 0.0
    while True:
        swing = 1.5 * math.sin(2 * math.pi * 1.8 * t)
        up = G + swing
        acc = {"x": 0, "y": up * math.cos(pitch), "z": up * math.sin(pitch)}
        lin = {"x": 0, "y": swing * math.cos(pitch), "z": swing * math.sin(pitch)}
        await phone.call("Emulation.setSensorOverrideReadings", type="accelerometer",
                         reading={"xyz": acc})  # fmt: skip
        await phone.call("Emulation.setSensorOverrideReadings", type="linear-acceleration",
                         reading={"xyz": lin})  # fmt: skip
        if int(t * 25) % 25 == 0:  # GPS once a second
            await phone.call("Emulation.setGeolocationOverride", latitude=lat + speed * t / 111_000,
                             longitude=lon, accuracy=6)  # fmt: skip
        await asyncio.sleep(0.04)
        t += 0.04


async def run(a) -> int:
    import aiohttp

    tmp = Path(tempfile.mkdtemp())
    video = to_mjpeg(Path(a.video), tmp / "camera.mjpeg")
    pitch = math.radians(a.pitch)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    async with aiohttp.ClientSession() as s:
        p1, w1 = await chrome(s, 9334, "--use-fake-ui-for-media-stream",
                              "--use-fake-device-for-media-stream",
                              f"--use-file-for-fake-video-capture={video}")  # fmt: skip
        p2, w2 = await chrome(s, 9336)
        try:
            phone, dash = Tab(w1), Tab(w2)
            await phone.call("Emulation.setDeviceMetricsOverride", width=412, height=915,
                             deviceScaleFactor=1, mobile=True)  # fmt: skip
            allowed = ["geolocation", "videoCapture", "audioCapture", "sensors"]
            await phone.call("Browser.grantPermissions", origin=a.server, permissions=allowed)
            await phone.call("Page.navigate", url=a.server + "/phone")
            await asyncio.sleep(3)
            # after the page has loaded: set before it, they did not reach it
            await phone.call("Emulation.setGeolocationOverride", latitude=a.lat, longitude=a.lon,
                             accuracy=6)  # fmt: skip
            for kind in ("accelerometer", "gravity", "linear-acceleration", "gyroscope",
                         "absolute-orientation"):  # fmt: skip
                await phone.call("Emulation.setSensorOverrideEnabled", enabled=True, type=kind)
            half = (math.pi / 2 - pitch) / 2  # beta = 90 degrees less the tilt, facing north
            await phone.call("Emulation.setSensorOverrideReadings", type="absolute-orientation",
                             reading={"quaternion": {"x": math.sin(half), "y": 0, "z": 0,
                                                     "w": math.cos(half)}})  # fmt: skip
            await phone.call("Emulation.setSensorOverrideReadings", type="gravity",
                             reading={"xyz": {"x": 0, "y": G * math.cos(pitch),
                                              "z": G * math.sin(pitch)}})  # fmt: skip
            await phone.call("Emulation.setSensorOverrideReadings", type="gyroscope",
                             reading={"xyz": {"x": 0, "y": 0, "z": 0}})  # fmt: skip
            await phone.call("Runtime.evaluate", userGesture=True,
                             expression="document.getElementById('startBtn').click()")  # fmt: skip
            walker = asyncio.create_task(walk(phone, pitch, a.lat, a.lon))
            await dash.call("Emulation.setDeviceMetricsOverride", width=1600, height=900,
                            deviceScaleFactor=1, mobile=False)  # fmt: skip
            await dash.call("Page.navigate", url=a.server + "/dashboard")
            shown = ("[document.getElementById('depthText').textContent,"
                     " document.getElementById('posText').textContent,"
                     " (document.querySelector('#mode p.main') || {}).textContent]")  # fmt: skip
            loop = asyncio.get_running_loop()
            start, n = loop.time(), 0
            while loop.time() - start < a.seconds:
                await asyncio.sleep(a.every)
                got = await dash.call("Runtime.evaluate", expression=f"JSON.stringify({shown})")
                depth, position, decision = json.loads(got["result"]["value"])
                print(f"{loop.time() - start:5.0f} s | {position} | {depth} | {decision}")
                shot = await dash.call("Page.captureScreenshot", format="png")
                (out / f"dashboard_{n:02d}.png").write_bytes(base64.b64decode(shot["data"]))
                n += 1
            walker.cancel()
            try:
                await walker
            except asyncio.CancelledError:
                pass
            shot = await phone.call("Page.captureScreenshot", format="png")
            (out / "phone.png").write_bytes(base64.b64decode(shot["data"]))
        finally:
            await w1.close()
            await w2.close()
            p1.kill()
            p2.kill()
    print(f"screenshots in {out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--video", required=True, help="the camera: a video file, or an .mjpeg")
    p.add_argument("--server", default="http://localhost:8000", help="the running demo server")
    p.add_argument("--seconds", type=float, default=60.0)
    p.add_argument("--every", type=float, default=6.0, help="seconds between screenshots")
    p.add_argument("--pitch", type=float, default=12.0, help="degrees the phone looks down")
    p.add_argument("--lat", type=float, default=33.6425, help="where the walk starts")
    p.add_argument("--lon", type=float, default=72.9930)
    p.add_argument("--out", default="e2e", help="folder for the screenshots")
    a = p.parse_args(argv)
    if find_chrome() is None:
        print("Google Chrome is needed", file=sys.stderr)
        return 2
    return asyncio.run(run(a))


if __name__ == "__main__":
    raise SystemExit(main())
