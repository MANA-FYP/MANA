"""Any camera as the vision input (#132): a webcam, a USB or IP camera, or a video file.

    python -m demo.camera                          # the laptop's webcam (camera 0)
    python -m demo.camera --source 1               # a USB camera
    python -m demo.camera --source rtsp://192.168.1.20/stream    # an IP camera (RTSP or HTTP)
    python -m demo.camera --source walk.mp4 --loop # a video file, again and again

It sends frames to the running demo server exactly as the phone page does: a small JPEG about five
times a second over /ws/phone, so the detector, the depth and the decision layer see them the same
way. A camera without motion sensors does not know how it is tilted, so --pitch says how far below
level it looks (degrees) and that tilt is sent as a steady gravity reading. --height is for the
server (--camera-height) and is only printed here as a reminder.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time

FRAME_TAG = 1  # the binary tag of a camera frame, as in phone.js


def open_source(source: str):
    """An OpenCV capture: a number is a camera index, anything else a file or a URL."""
    import cv2

    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        raise SystemExit(f"cannot open the camera or file {source!r}")
    return cap


def jpeg_of(frame, width: int = 480, quality: int = 60) -> bytes:
    """The frame scaled to `width` pixels across, as a JPEG: what the phone page sends."""
    import cv2

    h, w = frame.shape[:2]
    if w != width:
        frame = cv2.resize(frame, (width, round(h * width / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("the frame could not be encoded")
    return buf.tobytes()


def gravity(pitch_deg: float) -> dict:
    """The motion message of a still camera looking pitch_deg below level: what a phone held that
    way would read (accelerationIncludingGravity, +9.81 m/s^2 on the axis pointing up)."""
    p = math.radians(pitch_deg)
    return {"type": "motion", "ax": 0.0, "ay": 9.81 * math.cos(p), "az": 9.81 * math.sin(p)}


def frames(cap, loop: bool):
    """Frames from the capture; a file starts again at its end when loop is set."""
    import cv2

    while True:
        ok, frame = cap.read()
        if ok:
            yield frame
            continue
        if not loop:
            return
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = cap.read()
        if not ok:
            return
        yield frame


async def stream(
    url: str, cap, pitch_deg: float | None, fps: float = 5.0, loop: bool = False, limit=None
) -> int:
    """Send frames to the server's phone socket until the source ends; returns how many."""
    import aiohttp

    sent, period = 0, 1.0 / fps
    async with aiohttp.ClientSession() as session, session.ws_connect(url + "/ws/phone") as ws:

        async def drain():  # the server sends its state; read it so its sends do not back up
            async for _ in ws:
                pass

        reader = asyncio.create_task(drain())
        await ws.send_json({"type": "hello", "caps": {"camera": {"available": True}}})
        next_at = time.monotonic()
        for frame in frames(cap, loop):
            if pitch_deg is not None:
                await ws.send_json(gravity(pitch_deg))
            await ws.send_bytes(bytes([FRAME_TAG]) + jpeg_of(frame))
            sent += 1
            if limit is not None and sent >= limit:
                break
            next_at += period
            await asyncio.sleep(max(0.0, next_at - time.monotonic()))
        await asyncio.sleep(1.0)  # the server takes frames one at a time: let it finish
        reader.cancel()
    return sent


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Send any camera to the MANA demo server")
    p.add_argument("--source", default="0", help="camera number, video file, or rtsp/http URL")
    p.add_argument("--server", default="http://localhost:8000", help="the demo server")
    # no default tilt: a wrong one turns stairs up into stairs down with no warning (#153)
    tilt = p.add_mutually_exclusive_group(required=True)
    tilt.add_argument("--pitch", type=float,
                      help="degrees below level the camera looks; measure it, stairs up or down"
                           " depend on it")  # fmt: skip
    tilt.add_argument("--no-pitch", action="store_true",
                      help="send no tilt: stairs then come without a distance")  # fmt: skip
    p.add_argument("--fps", type=float, default=5.0, help="frames a second, as the phone sends")
    p.add_argument("--loop", action="store_true", help="play a video file again at its end")
    a = p.parse_args(argv)

    cap = open_source(a.source)
    pitch = None if a.no_pitch else a.pitch
    print(f"sending {a.source} to {a.server} at {a.fps:g} frames a second"
          + ("" if pitch is None else f", tilted {pitch:g} degrees down"))  # fmt: skip
    try:
        sent = asyncio.run(stream(a.server, cap, pitch, a.fps, a.loop))
    except KeyboardInterrupt:
        return 0
    except OSError as e:  # aiohttp's connection errors are OSErrors
        print(f"cannot reach the server at {a.server}: {e}", file=sys.stderr)
        return 1
    finally:
        cap.release()
    print(f"{sent} frames sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
