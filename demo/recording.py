"""Phone sessions recorded and played back (#89): a real walk, kept, and run through MANA again.

    python -m demo.server --record recordings/stairs-1      # record while the phone is used
    python -m demo.replay recordings/stairs-1               # play it into a running server

A recording is a folder: session.jsonl has one line per message the phone sent, with the seconds
since the first one ("t"), and each camera frame is a JPEG in frames/, named in its line. The
microphone's raw audio is left out: it is large, and what was said is in the "heard" and "word"
lines. The camera frames show people's faces, so keep recordings private (recordings/ is not
in git) and blur faces before anything from them is published.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

FRAME_TAG = 1  # the binary tag of a camera frame, as in phone.js


class Recorder:
    """Writes what the phone sends into a new recording folder. A folder that already holds a
    recording is refused (FileExistsError): a second session would overwrite its frames."""

    def __init__(self, folder: Path | str, clock=time.monotonic):
        self.folder = Path(folder)
        (self.folder / "frames").mkdir(parents=True, exist_ok=True)
        self.clock, self.t0, self.frames = clock, None, 0
        self.lines = (self.folder / "session.jsonl").open("x", encoding="utf-8", newline="\n")
        self.disk = ThreadPoolExecutor(1)  # frames are written off the message loop, in order
        # when the walk began, for its record: a copied folder's files show the day of the copy
        started = dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
        (self.folder / "meta.json").write_text(json.dumps({"started": started}) + "\n")

    def _t(self) -> float:
        now = self.clock()
        self.t0 = now if self.t0 is None else self.t0
        return round(now - self.t0, 4)

    def text(self, data: str) -> None:
        """A text message from the phone, as it came."""
        try:
            msg = json.loads(data)
        except ValueError:
            return
        self._write({"t": self._t(), "msg": msg})

    def binary(self, data: bytes) -> None:
        """A binary message: camera frames are kept, audio is not."""
        if not data or data[0] != FRAME_TAG:
            return
        self.frames += 1
        name = f"frames/{self.frames:06d}.jpg"
        self.disk.submit((self.folder / name).write_bytes, data[1:])
        self._write({"t": self._t(), "frame": name})

    def _write(self, line: dict) -> None:
        self.lines.write(json.dumps(line) + "\n")
        self.lines.flush()

    def close(self) -> None:
        self.disk.shutdown(wait=True)  # every frame on disk
        self.lines.close()


def started(folder: Path | str) -> str | None:
    """The day the recording began (UTC, YYYY-MM-DD), from its meta.json; None for a recording
    made before there was one."""
    try:
        return json.loads((Path(folder) / "meta.json").read_text())["started"][:10]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def read(folder: Path | str) -> list[dict]:
    """The lines of a recording, in order."""
    path = Path(folder) / "session.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


async def play(url: str, folder: Path | str, speed: float = 1.0) -> int:
    """Send a recording to a running server's phone socket at its own pace (speed 2 is twice as
    fast). Returns how many messages were sent."""
    import aiohttp

    from demo import access

    folder, sent = Path(folder), 0
    lines = read(folder)
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(access.with_key(url + "/ws/phone", access.read())) as ws,
    ):

        async def drain():  # read what the server sends, or its sends back up
            async for _ in ws:
                pass

        reader = asyncio.create_task(drain())
        loop = asyncio.get_running_loop()
        start = loop.time()
        for line in lines:
            wait = start + line["t"] / speed - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            if "frame" in line:
                await ws.send_bytes(bytes([FRAME_TAG]) + (folder / line["frame"]).read_bytes())
            else:
                await ws.send_json(line["msg"])
            sent += 1
        await asyncio.sleep(1.0)  # the server takes frames one at a time: let it finish
        reader.cancel()
    return sent
