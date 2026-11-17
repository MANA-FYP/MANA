"""A short summary of each drive, and the same drive with the baseline in place of the core (#81).

    python3 -m mana_sim.drive_summary TRACES [--update docs/results.md]

TRACES holds one JSON trace per drive (the format of gazebo_scenarios.trace_to_json), and, in a
folder named baseline inside it, the same drives played with the if-else baseline deciding instead
of the core (mana_core.evaluate --world gazebo --compare writes both). For each drive the summary
says how long and how far it went, each stop and slow-down with its cause and where it was, the
time on tracks, what the user was told, the takeovers, and any mishap. Next to it stands the
baseline's drive and the place where the two first decided differently.

The two drives are separate simulations, so they are compared at the same place on the route, not
at the same time. Nothing here needs ROS: it reads the traces.
"""

import sys
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path

from mana_core.decision import WAITING, Command, Mode
from mana_core.scenarios import Trace

from mana_sim import gazebo_scenarios as gs

START = "<!-- drives:start (written by mana_sim.drive_summary; do not edit) -->"
END = "<!-- drives:end -->"
SLOW_BELOW = 0.99  # a speed limit under this is a slow-down
SAME_SPEED = 0.05  # speed limits this close are the same decision
HOLD_SAMPLES = 3  # a difference counts when it lasts this many samples (0.3 s)


@dataclass(frozen=True)
class Episode:
    """A stop or a slow-down: when it began, how long it lasted, where, and why."""

    start_s: float
    length_s: float
    x_m: float
    reason: str


@dataclass(frozen=True)
class Summary:
    name: str
    duration_s: float
    distance_m: float
    tracks_s: float
    stops: tuple[Episode, ...]
    slowdowns: tuple[Episode, ...]
    messages: int
    takeovers: int  # hand-overs to the user (MANUAL); the hand-back (AUTOMATIC) is not counted
    mishaps: tuple[str, ...]


def episodes(trace: Trace, active) -> tuple[Episode, ...]:
    """The runs of samples for which active(sample) holds, each with the reason it began with."""
    out, start = [], None
    samples = trace.samples
    for i, s in enumerate(samples):
        if active(s) and start is None:
            start = i
        if start is not None and (not active(s) or i == len(samples) - 1):
            first = samples[start]
            out.append(Episode(first.t, max(0.0, s.t - first.t), first.x_m, first.reason))
            start = None
    return tuple(out)


def summarise(name: str, trace: Trace) -> Summary:
    samples = trace.samples
    duration = samples[-1].t - samples[0].t if len(samples) > 1 else 0.0
    distance = max(0.0, trace.end_m - samples[0].x_m) if samples else 0.0
    tracks = sum(
        b.t - a.t for a, b in zip(samples, samples[1:], strict=False) if a.mode == Mode.TRACKS
    )
    return Summary(
        name,
        duration,
        distance,
        tracks,
        episodes(trace, lambda s: s.stop),
        episodes(trace, lambda s: not s.stop and s.speed_limit < SLOW_BELOW),
        len(trace.messages),
        sum(c == Command.MANUAL for _, c in trace.said),
        tuple(trace.mishaps),
    )


def same_decision(a, b) -> bool:
    return (
        a.stop == b.stop and a.mode == b.mode and abs(a.speed_limit - b.speed_limit) <= SAME_SPEED
    )


def starting(*samples) -> bool:
    """Whether a sample is the start-up wait for the ground sensors."""
    return any(s.reason.startswith(WAITING) for s in samples)


def first_difference(mana: Trace, other: Trace, by: str = "x"):
    """The first place on the route (by="x", the default) or the first moment (by="t") where the
    two decided differently for a few samples in a row: (x_m or t, MANA's sample, the other's
    sample), or None. The other's sample is the nearest in x (or t); its samples are sorted by it
    first, so a drive that backs up, a takeover for example, is matched by place all the same.
    Use by="t" where something comes at a set time, as the user's stop does in the scenario
    `stop`: it comes at a different place in each simulation, and a place would show a difference
    that is not one. The start-up, while the core waits for its ground sensors, is not a decision
    about the route and is not compared."""
    if not mana.samples or not other.samples:
        return None
    key = (lambda s: s.t) if by == "t" else (lambda s: s.x_m)
    ordered = sorted(other.samples, key=key)
    ks = [key(s) for s in ordered]
    run = []
    for s in mana.samples:
        i = min(bisect_left(ks, key(s)), len(ks) - 1)
        if i > 0 and abs(ks[i - 1] - key(s)) < abs(ks[i] - key(s)):
            i -= 1
        if same_decision(s, ordered[i]) or starting(s, ordered[i]):
            run = []
            continue
        run.append((s, ordered[i]))
        if len(run) == HOLD_SAMPLES:
            return key(run[0][0]), run[0][0], run[0][1]
    return None


def seconds(x: float) -> str:
    return f"{x:.1f} s"


def kinds(items: tuple[Episode, ...]) -> str:
    """ "none", or each episode as "reason (length, at x m)"."""
    if not items:
        return "none"
    return "; ".join(f"{e.reason} ({seconds(e.length_s)}, at {e.x_m:.1f} m)" for e in items)


def drive_markdown(s: Summary) -> str:
    """The summary of one drive, as a short list."""
    return "\n".join(
        [
            f"- **{s.name}:** {seconds(s.duration_s)}, {s.distance_m:.1f} m; "
            f"{seconds(s.tracks_s)} on tracks; {s.messages} messages; {s.takeovers} takeovers",
            f"  - stops: {kinds(s.stops)}",
            f"  - slow-downs: {kinds(s.slowdowns)}",
            f"  - mishaps: {'; '.join(s.mishaps) if s.mishaps else 'none'}",
        ]
    )


def short(items: tuple[Episode, ...]) -> str:
    return str(len(items)) if not items else f"{len(items)} ({items[0].reason})"


def decided(s) -> str:
    return "stops" if s.stop else f"{s.mode.name.lower()} at {s.speed_limit:.0%}"


def comparison_row(title: str, mana: Summary, base: Summary, diff, by_time: bool = False) -> str:
    where = "the same everywhere"
    if diff:
        x, m, b = diff
        at = f"{x:.1f} s" if by_time else f"{x:.1f} m"
        where = f"{at}: MANA {decided(m)} ({m.reason}); baseline {decided(b)} ({b.reason})"
    return (
        f"| {title} | {short(mana.stops)} | {short(base.stops)} "
        f"| {seconds(mana.tracks_s)} | {seconds(base.tracks_s)} "
        f"| {len(mana.mishaps) or 'none'} | {len(base.mishaps) or 'none'} | {where} |"
    )


def load(path: Path) -> Trace:
    return gs.trace_from_json(path.read_text())


def report_markdown(traces: Path) -> str:
    """One section for docs/results.md: the drives, and where the baseline would have differed."""
    base_dir = traces / "baseline"
    rows, lists, played = [], [], 0
    for name in gs.RUNS:
        path = traces / f"{name}.json"
        if not path.exists():
            continue
        played += 1
        trace = load(path)
        mana = summarise(name, trace)
        lists.append(drive_markdown(mana))
        other = base_dir / f"{name}.json"
        if other.exists():
            base_trace = load(other)
            by_time = bool(gs.RUNS[name].says)  # the user speaks at set times: compare by time
            diff = first_difference(trace, base_trace, "t" if by_time else "x")
            row = comparison_row(f"`{name}`", mana, summarise(name, base_trace), diff, by_time)
            rows.append(row)
        else:
            rows.append(f"| `{name}` | {short(mana.stops)} | (not played) | | | | | |")
    return (
        START
        + "\n### Each drive, and the same drive with the baseline\n\n"
        + "Every drive is summarised, and each one is played again with the if-else baseline "
        + "(`mana_core/baseline.py`) deciding in place of the core, with the same route, faults "
        + "and driver commands. The two are separate simulations, so the last column gives "
        + "the first place on the route where they decided differently (for a scenario in which "
        + "the user speaks at set times, `stop`, the first moment instead, because the stop comes "
        + "at a different place in each simulation). "
        + "`python3 -m mana_sim.drive_summary`"
        + " writes this section from the traces; `mana_core.evaluate --world gazebo --compare`"
        + " plays both.\n\n"
        + "\n".join(lists)
        + "\n\n| Drive | MANA stops | Baseline stops | MANA on tracks | Baseline on tracks "
        + "| MANA mishaps | Baseline mishaps | First difference |\n"
        + "|---|---|---|---|---|---|---|---|\n"
        + "\n".join(rows)
        + f"\n\n{played} drives.\n"
        + END
        + "\n"
    )


def update_file(path: Path, section: str) -> None:
    """Put the section into the file: in place of the old one, or at the end."""
    text = path.read_text() if path.exists() else ""
    a, b = text.find(START), text.find(END)
    if a != -1 and b != -1:
        text = text[:a] + section.rstrip("\n") + text[b + len(END) :]
    else:
        text = text.rstrip("\n") + "\n\n" + section
    path.write_text(text, newline="\n")  # LF on Windows too


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    update = Path(args[args.index("--update") + 1]) if "--update" in args else None
    rest = [a for a in args if a != "--update" and (update is None or a != str(update))]
    traces = Path(rest[0] if rest else "gazebo_traces")
    if not traces.is_dir():
        print(f"no folder {traces}: play the scenarios first (mana_core.evaluate --world gazebo)")
        return 2
    section = report_markdown(traces)
    if update:
        update_file(update, section)
    else:
        print(section)
    return 0


if __name__ == "__main__":
    sys.exit(main())
