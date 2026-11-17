"""Play a recorded phone session into a running demo server (#89).

python -m demo.replay recordings/stairs-1 --server http://localhost:8000 --speed 1
python -m demo.replay recordings/stairs-1 --decisions stairs-1.csv   # and what MANA decided

With --decisions, every decision the server takes while the walk plays is written to a CSV, at the
seconds into the recording it came (walk_s), with its kind, state (stop, slow, go), reason and
sentence: the record to score against marks made by hand on the same walk (#89). Play it into a
server of its own (--port) with the same models as the walk, at speed 1, so the models keep up.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
from pathlib import Path

from demo import recording

FIELDS = ["walk_s", "n", "kind", "state", "why", "text"]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("folder", help="a recording made with python -m demo.server --record")
    p.add_argument("--server", default="http://localhost:8000")
    p.add_argument("--speed", type=float, default=1.0, help="2 plays it twice as fast")
    p.add_argument("--decisions", help="a CSV of what the server decided while it played")
    a = p.parse_args(argv)
    if not (Path(a.folder) / "session.jsonl").exists():
        print(f"no recording in {a.folder}", file=sys.stderr)
        return 2
    decisions = [] if a.decisions else None
    try:
        sent = asyncio.run(recording.play(a.server, a.folder, a.speed, decisions))
    except OSError as e:  # aiohttp's connection errors are OSErrors
        print(f"cannot reach the server at {a.server}: {e}", file=sys.stderr)
        return 1
    print(f"{sent} messages played from {a.folder}")
    if decisions is not None:
        with open(a.decisions, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS, extrasaction="ignore", restval="", lineterminator="\n")
            w.writeheader()
            w.writerows(decisions)
        print(f"{len(decisions)} decisions written to {a.decisions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
