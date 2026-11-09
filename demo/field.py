"""How well the position track did on a real walk (#133): a recording made with
`python -m demo.server --record FOLDER`, played through a fresh server, scored against what is
known about the walk.

    python -m demo.field recordings/loop-1 --loop --steps 212 --place "corridor, floor 2"
    python -m demo.field recordings/road-1 --distance 120 --steps 160 --place "campus road"

--loop: the walk ended where it started, so the gap between the track's start and end is its
error. --distance: the walk's real length (a tape, or a route measured on a map). --steps: the
steps taken, counted by the walker. It prints the numbers and a row for the table in
docs/results.md. The recording plays at its own pace: GPS fixes are timed by the server, so a
faster play would change how the track takes them. --speed is for walks on steps alone.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import math
import sys
from pathlib import Path

ROW = "| {date} | {place} | {source} | {steps} | {distance} | {gap} |"
HEADER = (
    "| Date | Place | Source | Steps counted / taken | Distance tracked / real"
    " | Gap end to start |\n|---|---|---|---|---|---|"
)


async def replay(folder: Path, speed: float = 1.0):
    """The track after the recording has played through a fresh server, as the phone sent it."""
    from aiohttp.test_utils import TestServer

    from demo import recording
    from demo.server import build_app

    app = build_app()
    server = TestServer(app)
    await server.start_server()
    try:
        await recording.play(str(server.make_url("")).rstrip("/"), folder, speed)
        return app["mw"].nav.track
    finally:
        await server.close()


def score(track, loop=False, distance_m=None, steps=None) -> dict:
    """What the walk says about the track. A source of "gps" means GPS was in use at the end."""
    out = {
        "steps": track.steps,
        "steps_taken": steps,
        "distance_m": round(track.distance_m, 1),
        "real_m": distance_m,
        "source": track.source,
        "gap_m": round(math.hypot(track.x, track.y), 1) if loop else None,
    }
    if steps:
        out["steps_error"] = f"{(track.steps - steps) / steps:+.1%}"
    if distance_m:
        out["distance_error"] = f"{(track.distance_m - distance_m) / distance_m:+.1%}"
    if loop and track.distance_m > 0:
        out["gap_share"] = f"{math.hypot(track.x, track.y) / track.distance_m:.1%} of the walk"
    return out


def row(result: dict, place: str, date: str) -> str:
    def pair(a, b, unit=""):
        return f"{a}{unit} / {b}{unit}" if b else f"{a}{unit} / ?"

    return ROW.format(
        date=date,
        place=place,
        source=result["source"],
        steps=pair(result["steps"], result["steps_taken"]),
        distance=pair(result["distance_m"], result["real_m"], " m"),
        gap="" if result["gap_m"] is None else f"{result['gap_m']} m",
    )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", help="a recording made with python -m demo.server --record")
    p.add_argument("--loop", action="store_true", help="the walk ended where it started")
    p.add_argument("--distance", type=float, help="the walk's real length in metres")
    p.add_argument("--steps", type=int, help="the steps taken, counted by the walker")
    p.add_argument("--place", default="?", help="where, for the table")
    p.add_argument("--speed", type=float, default=1.0, help="only for walks on steps alone")
    a = p.parse_args(argv)
    folder = Path(a.folder)
    if not (folder / "session.jsonl").exists():
        print(f"no recording in {folder}", file=sys.stderr)
        return 2
    track = asyncio.run(replay(folder, a.speed))
    result = score(track, a.loop, a.distance, a.steps)
    for k, v in result.items():
        if v is not None:
            print(f"{k}: {v}")
    date = dt.date.fromtimestamp((folder / "session.jsonl").stat().st_mtime).isoformat()
    print("\n" + HEADER + "\n" + row(result, a.place, date))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
