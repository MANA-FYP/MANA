"""Play a recorded phone session into a running demo server (#89).

python -m demo.replay recordings/stairs-1 --server http://localhost:8000 --speed 1
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from demo import recording


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", help="a recording made with python -m demo.server --record")
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--speed", type=float, default=1.0, help="2 plays it twice as fast")
    a = p.parse_args(argv)
    if not (Path(a.folder) / "session.jsonl").exists():
        print(f"no recording in {a.folder}", file=sys.stderr)
        return 2
    try:
        sent = asyncio.run(recording.play(a.server, a.folder, a.speed))
    except OSError as e:  # aiohttp's connection errors are OSErrors
        print(f"cannot reach the server at {a.server}: {e}", file=sys.stderr)
        return 1
    print(f"{sent} messages played from {a.folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
