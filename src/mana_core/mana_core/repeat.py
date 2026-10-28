"""Repeated noisy runs (#54): every scenario many times, with sensor noise, lost reports, false
one-sensor reports, and faults and spoken commands moved in time, for MANA and the baseline.

    python3 -m mana_core.repeat [--runs N] [--seed S] [--no-false-reports] [scenario ...]

The scripted world is exact; real sensors are not. Each run draws its noise from its own seeded
generator, and MANA and the baseline get the same seed for the same run, so a table can be made
again exactly. Only what no pass mark depends on is moved: the stretches and obstacles stay where
the marks expect them, faults start and end up to fault_shift_m earlier or later (the marks keep
that far clear of them), and spoken commands come up to say_shift_s earlier or later.

The noise levels in Noise are assumptions, not measurements of our sensors: they are there to
show how each controller copes when its inputs are not perfect.
"""

import math
import random
import statistics
import sys
from dataclasses import dataclass, replace

from mana_core.baseline import BaselineController
from mana_core.decision import (
    DecisionCore,
    Ground,
    GroundReport,
    MotionReport,
    ObstacleReport,
    Source,
)
from mana_core.evaluate import run
from mana_core.scenarios import SCENARIOS, Scenario, check
from mana_core.world import World


@dataclass(frozen=True)
class Noise:
    lost_p: float = 0.05  # a report never arrives
    trust_sd: float = 0.05
    distance_sd_m: float = 0.02  # plus distance_rel times the distance
    distance_rel: float = 0.05
    pitch_sd_rad: float = 0.01
    false_p: float = 0.002  # per tick: the camera alone says stairs ahead (a picture, a pattern)
    fault_shift_m: float = 0.5
    say_shift_s: float = 0.3


NO_NOISE = Noise(0, 0, 0, 0, 0, 0, 0, 0)


class NoisyWorld(World):
    """The scripted world, with each report changed or lost as Noise says."""

    def __init__(self, route, rng: random.Random, noise: Noise):
        super().__init__(route)
        self.rng, self.noise = rng, noise

    def sense(self, t: float) -> list:
        n, rng, out = self.noise, self.rng, []
        for r in super().sense(t):
            if rng.random() < n.lost_p:
                continue
            r = replace(r, trust=min(1.0, max(0.0, r.trust + rng.gauss(0.0, n.trust_sd))))
            if isinstance(r, GroundReport | ObstacleReport) and math.isfinite(r.distance_m):
                sd = n.distance_sd_m + n.distance_rel * r.distance_m
                r = replace(r, distance_m=max(0.0, r.distance_m + rng.gauss(0.0, sd)))
            elif isinstance(r, MotionReport):
                r = replace(r, pitch_rad=r.pitch_rad + rng.gauss(0.0, n.pitch_sd_rad))
            out.append(r)
        if rng.random() < n.false_p:
            out.append(GroundReport(t, 0.9, Source.CAMERA, Ground.STAIRS_UP))
        return out


def moved(scenario: Scenario, rng: random.Random, noise: Noise) -> Scenario:
    """The scenario with its faults and spoken commands moved; the start and end of the route are
    kept (a fault from 0 m stays from 0 m, and one to the end stays to the end)."""

    def shift(x: float) -> float:
        return x if x <= 0.0 or math.isinf(x) else x + rng.uniform(-1, 1) * noise.fault_shift_m

    faults = tuple(
        replace(f, from_m=shift(f.from_m), to_m=shift(f.to_m)) for f in scenario.route.faults
    )
    says = tuple(
        replace(s, at_s=max(0.0, s.at_s + rng.uniform(-1, 1) * noise.say_shift_s)) if s.at_s else s
        for s in scenario.says
    )
    return replace(scenario, route=replace(scenario.route, faults=faults), says=says)


@dataclass(frozen=True)
class Result:
    marks_met: int
    marks: int
    mishap: bool


def one(scenario: Scenario, controller, seed: int, noise: Noise) -> Result:
    rng = random.Random(seed)
    s = moved(scenario, rng, noise)
    trace = run(s, controller=controller, world=NoisyWorld(s.route, rng, noise))
    results = check(scenario, trace)  # the original marks: they do not depend on what was moved
    return Result(sum(ok for _, ok, _ in results), len(results), bool(trace.mishaps))


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 percent interval for a pass rate of k in n (Wilson score)."""
    if n == 0:
        return 0.0, 1.0
    p, d = k / n, 1 + z * z / n
    centre, half = (
        (p + z * z / (2 * n)) / d,
        z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d,
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def summary(results: list[Result]) -> dict:
    passed = sum(r.marks_met == r.marks for r in results)
    met = [r.marks_met / r.marks for r in results]
    return {
        "runs": len(results),
        "passed": passed,
        "interval": wilson(passed, len(results)),
        "mishaps": sum(r.mishap for r in results),
        "met_mean": statistics.fmean(met),
        "met_sd": statistics.pstdev(met),
    }


def table(names: list[str], runs: int, seed: int, noise: Noise | None = None) -> tuple[str, dict]:
    """A markdown table, and the summaries by scenario and controller."""
    noise = noise or Noise()
    rows = [
        "| Scenario | MANA: all marks met | Baseline: all marks met | MANA mishaps | "
        "Baseline mishaps | Marks met, MANA | Marks met, baseline |",
        "|---|---|---|---|---|---|---|",
    ]
    out = {}
    for name in names:
        sc = SCENARIOS[name]
        stats = {
            label: summary([one(sc, c, seed * 100_003 + i, noise) for i in range(runs)])
            for label, c in (("MANA", DecisionCore), ("baseline", BaselineController))
        }
        out[name] = stats

        def rate(s):
            lo, hi = s["interval"]
            return f"{s['passed']}/{s['runs']} ({lo:.1%} to {hi:.1%})"

        def spread(s):
            return f"{s['met_mean']:.1%} (sd {s['met_sd']:.1%})"

        m, b = stats["MANA"], stats["baseline"]
        rows.append(
            f"| {sc.title} | {rate(m)} | {rate(b)} | {m['mishaps']}/{runs} | {b['mishaps']}/{runs} "
            f"| {spread(m)} | {spread(b)} |"
        )
    return "\n".join(rows), out


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    runs, seed, names, noise = 200, 1, [], Noise()
    it = iter(args)
    for a in it:
        if a in ("--runs", "--seed"):
            value = next(it, None)
            if value is None or not value.isdigit():
                print(f"{a} needs a whole number")
                return 2
            runs, seed = (int(value), seed) if a == "--runs" else (runs, int(value))
        elif a == "--no-false-reports":
            noise = replace(noise, false_p=0.0)
        elif a.startswith("--"):
            print(f"unknown option {a}; use --runs N, --seed S or --no-false-reports")
            return 2
        else:
            names.append(a)
    names = names or list(SCENARIOS)
    if unknown := [n for n in names if n not in SCENARIOS]:
        print(f"unknown scenario {', '.join(unknown)}; choose from: {', '.join(SCENARIOS)}")
        return 2
    text, _ = table(names, runs, seed, noise)
    which = "with no false reports" if noise.false_p == 0 else "as in Noise()"
    print(f"{runs} runs per scenario and controller, seed {seed}, noise {which}:\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
