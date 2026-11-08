"""Sweep the decision thresholds over the scripted scenarios, with #54's repeated noisy runs (#86).

    python3 tools/sweep_thresholds.py [--runs N] [--seed S] [param ...]

One threshold at a time is moved over a few values; every other limit keeps its default. Each
value plays every scenario N times with the same noise and seeds as `mana_core.repeat`, so the
values are compared on the same runs. For each value it prints:

- runs: the runs that met every pass mark, of all runs
- mishaps: runs with a mishap (over an edge, into an obstacle, on a step on wheels)
- level slowed: in `level`, where nothing is in the way, the mean seconds not at full speed
- time: the mean time to the end of the route, over the runs that got there

The scripted world brakes at once, so a smaller stop distance always looks better here; the
stop distances also have to leave room for the chair's real stopping distance (#84, D6).
"""

import math
import random
import statistics
import sys
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "mana_core"))

from mana_core.decision import Limits  # noqa: E402
from mana_core.evaluate import run  # noqa: E402
from mana_core.repeat import Noise, NoisyWorld, moved  # noqa: E402
from mana_core.scenarios import SCENARIOS, check  # noqa: E402

VALUES = {
    "min_trust": [0.3, 0.4, 0.5, 0.6, 0.7],
    "max_age_s": [0.2, 0.25, 0.3, 0.5, 0.8, 1.2],
    "edge_m": [0.2, 0.25, 0.3, 0.35, 0.5, 0.7, 1.0],
    "obstacle_stop_m": [0.5, 0.65, 0.8, 1.0, 1.3],
    "obstacle_slow_m": [1.2, 1.6, 2.0, 2.5, 3.0],
    "step_m": [1.0, 1.5, 1.75, 2.0, 2.5, 3.0],
    "slow": [0.2, 0.3, 0.4, 0.5, 0.6],
}


def one(job):
    """One noisy run of one scenario with one set of limits: (met all, mishap, slowed s, time)."""
    name, limits, seed = job
    scenario, noise = SCENARIOS[name], Noise()
    rng = random.Random(seed)
    s = moved(scenario, rng, noise)
    trace = run(s, limits=limits, world=NoisyWorld(s.route, rng, noise))
    marks = check(scenario, trace)
    dt = trace.samples[1].t - trace.samples[0].t if len(trace.samples) > 1 else 0.0
    slowed = sum(dt for x in trace.samples if x.stop or x.speed_limit < 1.0)
    reached = trace.samples[-1].t if trace.end_m >= s.route.length_m else math.nan
    return all(ok for _, ok, _ in marks), bool(trace.mishaps), slowed, reached


def sweep(param: str, runs: int, seed: int, pool) -> list[dict]:
    rows = []
    for value in VALUES[param]:
        limits = replace(Limits(), **{param: value})
        jobs = [(n, limits, seed + i) for n in SCENARIOS for i in range(runs)]
        out = list(pool.map(one, jobs, chunksize=20))
        level = [o[2] for (n, _, _), o in zip(jobs, out, strict=True) if n == "level"]
        times = [o[3] for o in out if not math.isnan(o[3])]
        rows.append(
            {
                "value": value,
                "default": value == getattr(Limits(), param),
                "passed": sum(o[0] for o in out),
                "runs": len(out),
                "mishaps": sum(o[1] for o in out),
                "level_slowed_s": statistics.fmean(level),
                "time_s": statistics.fmean(times) if times else math.nan,
            }
        )
    return rows


def markdown(param: str, rows: list[dict]) -> str:
    lines = [
        f"| `{param}` | runs that met every mark | mishaps | level: s slowed | mean time |",
        "|---|---|---|---|---|",
    ]
    for r in rows:
        value = f"**{r['value']}** (now)" if r["default"] else f"{r['value']}"
        lines.append(
            f"| {value} | {r['passed']} of {r['runs']} | {r['mishaps']} |"
            f" {r['level_slowed_s']:.2f} | {r['time_s']:.1f} s |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    runs = int(args[args.index("--runs") + 1]) if "--runs" in args else 50
    seed = int(args[args.index("--seed") + 1]) if "--seed" in args else 1
    flags = {"--runs", "--seed"}
    skip = {i + 1 for i, a in enumerate(args) if a in flags}
    params = [a for i, a in enumerate(args) if a not in flags and i not in skip] or list(VALUES)
    if bad := [p for p in params if p not in VALUES]:
        print(f"unknown: {', '.join(bad)}; choose from: {', '.join(VALUES)}")
        return 2
    print(f"{runs} noisy runs of each of the {len(SCENARIOS)} scenarios per value, seed {seed}\n")
    with ProcessPoolExecutor() as pool:
        for param in params:
            print(markdown(param, sweep(param, runs, seed, pool)) + "\n", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
