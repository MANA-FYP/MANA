"""Score what MANA decided on real phone walks against hazards marked by hand (#89).

    python3 -m demo.replay recordings/2026-11-20_stairs --server http://localhost:8001 \\
        --decisions out/2026-11-20_stairs.csv
    python3 tools/score_walks.py --marks recordings/marks.csv --decisions out/

The marks are a CSV with one row per hazard, marked by hand while watching the recording:

    recording,hazard,start_s,end_s,note
    2026-11-20_stairs,stairs_up,12.4,17.0,library stairs from the lawn

- recording: the recording's folder name; its decisions are <decisions>/<recording>.csv, as
  `demo.replay --decisions` writes them.
- hazard: one of HAZARDS (stairs_up, stairs_down, curb, drop, ramp, obstacle, person, door).
- start_s: seconds into the walk when the hazard is first in the camera's view and clear enough to
  call by eye: the earliest a reaction could fairly be asked for.
- end_s: when the walker reaches it (the first step, the edge, the person). A reaction after this
  is too late and counts as a miss.

A hazard is caught when MANA slows down or stops for a reason that fits it (REASONS) between
start_s and end_s. The reaction time is from start_s to the first such decision, and the lead is
what was left until end_s. A stop or slow-down for a hazard's reason that falls in no window of a
hazard it fits is a false alarm, given per minute of walking (the recording's length, from its
session.jsonl when --recordings is given, else its last decision). Slow-downs that report MANA's own
state (a sensor not ready, position unknown, no alert link, wheel slip, on tracks) are neither:
they are not about the ground ahead.

The phone has no 3D camera or lidar, so these walks test the camera path and the one-sensor rules;
the two-sensor confirmation is tested in Gazebo (#89).
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path

# the core's reasons (mana_core.decision) that count as reacting to each hazard: a reason is
# matched by how it starts, as the core adds details after a comma ("Drop ahead, stopping")
STEP = ("Step ahead", "Possible step ahead")
REASONS = {
    "stairs_up": ("Stairs ahead", "Curb ahead", *STEP),
    "stairs_down": ("Stairs down ahead", "Drop ahead", *STEP),
    "curb": ("Curb ahead", "Drop ahead", *STEP),
    "drop": ("Drop ahead", *STEP),
    "ramp": ("Ramp ahead",),
    "obstacle": ("Obstacle ahead",),
    "person": ("Obstacle ahead", "Someone may be near"),
    "door": ("Obstacle ahead",),
}
HAZARDS = tuple(REASONS)
# reasons that report MANA's own state, not the ground ahead
STATE = (
    "Ground sensors not available",
    "Waiting for the ground sensors",
    "Position unknown",
    "No alert link",
    "Wheel slip",
    "On tracks until the ground is level",
    "Going down the stairs on tracks",
    "You stopped it",
)


@dataclass
class Mark:
    recording: str
    hazard: str
    start_s: float
    end_s: float
    note: str = ""


@dataclass
class Decision:
    walk_s: float
    state: str  # stop, slow or go
    why: str


def fits(why: str, hazard: str) -> bool:
    return why.startswith(REASONS[hazard])


def about_ground(why: str) -> bool:
    """Whether a reason is about the way ahead, not about MANA's own state."""
    return not why.startswith(STATE) and any(fits(why, h) for h in HAZARDS)


def read_marks(path: Path) -> list[Mark]:
    marks = []
    with open(path, newline="", encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), 2):
            hazard = row["hazard"].strip()
            if hazard not in REASONS:
                raise ValueError(
                    f"{path}:{i}: hazard {hazard!r} is not one of {', '.join(HAZARDS)}"
                )
            start, end = float(row["start_s"]), float(row["end_s"])
            if end < start:
                raise ValueError(f"{path}:{i}: end_s {end} is before start_s {start}")
            marks.append(Mark(row["recording"].strip(), hazard, start, end, row.get("note", "")))
    return marks


def read_decisions(path: Path) -> list[Decision]:
    """The decisions `demo.replay --decisions` writes; only those with a state (stop, slow, go)."""
    with open(path, newline="", encoding="utf-8") as f:
        return [
            Decision(float(r["walk_s"]), r["state"], r.get("why") or "")
            for r in csv.DictReader(f)
            if r.get("state")
        ]


def walk_minutes(recording: Path | None, decisions: list[Decision]) -> float:
    """The walk's length: the last message of its session.jsonl, else its last decision."""
    if recording is not None and (recording / "session.jsonl").exists():
        last = 0.0
        for line in (recording / "session.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = max(last, float(json.loads(line).get("t", 0.0)))
        return last / 60
    return max((d.walk_s for d in decisions), default=0.0) / 60


def score(marks: list[Mark], decisions: list[Decision]) -> tuple[list[dict], list[Decision]]:
    """One row per mark (caught or not, the reaction time and the lead), and the false alarms,
    for one recording."""
    acting = [d for d in decisions if d.state in ("stop", "slow")]
    rows = []
    for m in marks:
        hit = next(
            (d for d in acting if m.start_s <= d.walk_s <= m.end_s and fits(d.why, m.hazard)), None
        )
        rows.append(
            {
                "recording": m.recording,
                "hazard": m.hazard,
                "start_s": m.start_s,
                "end_s": m.end_s,
                "caught": hit is not None,
                "reaction_s": round(hit.walk_s - m.start_s, 2) if hit else None,
                "lead_s": round(m.end_s - hit.walk_s, 2) if hit else None,
                "decision": f"{hit.state}: {hit.why}" if hit else "",
                "note": m.note,
            }
        )
    false = [
        d
        for d in acting
        if about_ground(d.why)
        and not any(m.start_s <= d.walk_s <= m.end_s and fits(d.why, m.hazard) for m in marks)
    ]
    return rows, false


def median(rows: list[dict], key: str) -> str:
    return f"{statistics.median(r[key] for r in rows):.1f} s" if rows else "-"


def table(rows: list[dict], false: int, minutes: float) -> str:
    """The results table, one line per hazard kind, as docs/results.md takes it."""
    lines = [
        "| Hazard | Marked | Caught | Reaction (median) | Lead left (median) |",
        "|---|---:|---:|---:|---:|",
    ]
    for hazard in HAZARDS:
        mine = [r for r in rows if r["hazard"] == hazard]
        if not mine:
            continue
        hit = [r for r in mine if r["caught"]]
        lines.append(
            f"| {hazard} | {len(mine)} | {len(hit)} | {median(hit, 'reaction_s')} "
            f"| {median(hit, 'lead_s')} |"
        )
    hit = sum(r["caught"] for r in rows)
    lines.append(f"| all | {len(rows)} | {hit} | | |")
    rate = f"{false / minutes:.2f} a minute" if minutes > 0 else "n/a"
    lines += ["", f"False alarms: {false} in {minutes:.1f} minutes of walking ({rate})."]
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--marks", type=Path, required=True, help="the hand marks, a CSV")
    ap.add_argument("--decisions", type=Path, required=True, help="the folder of <recording>.csv")
    ap.add_argument(
        "--recordings", type=Path, help="the recordings' folder, for each walk's length"
    )
    ap.add_argument("--out", type=Path, help="also write one row per mark here (CSV)")
    a = ap.parse_args(argv)

    marks = read_marks(a.marks)
    rows, false, minutes = [], 0, 0.0
    for name in dict.fromkeys(m.recording for m in marks):
        path = a.decisions / f"{name}.csv"
        if not path.exists():
            print(f"no decisions for {name} ({path}): replay it with --decisions", file=sys.stderr)
            return 2
        decided = read_decisions(path)
        mine, wrong = score([m for m in marks if m.recording == name], decided)
        rows += mine
        false += len(wrong)
        minutes += walk_minutes(a.recordings / name if a.recordings else None, decided)
        for d in wrong:
            print(f"false alarm in {name} at {d.walk_s:.1f} s: {d.state}: {d.why}")
    print(table(rows, false, minutes))
    if a.out and rows:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, list(rows[0]), lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
