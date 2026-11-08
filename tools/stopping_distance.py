"""Where the chair comes to a standstill before a hazard, for a range of decelerations (#84).

    python3 tools/stopping_distance.py [--reaction-s 0.2]

The chair drives at top speed (1.2 m/s). The decision core slows it to 0.3 of that (0.36 m/s)
once the hazard is within the slow distance, and stops it once the hazard is within the stop
distance. Each request reaches the wheels reaction_s after the hazard crosses the distance (a
sensor frame to the wheels at 0, measured by tools/measure_latency.py), and the chair then
brakes at a constant deceleration. A real chair's deceleration is not known yet: the simulated
one allows 3 m/s^2, so the table runs from 0.5 to 3.

Cases:
    drop      seen at step_m (2 m), stop at edge_m (0.5 m)
    obstacle  seen at obstacle_slow_m (2 m), stop at obstacle_stop_m (0.8 m)
    late      an obstacle that appears 1.2 m ahead at top speed: a person stepping out
"""

import sys

TOP_MPS = 1.2
SLOW = 0.3
DT = 0.001


def standstill(seen_m: float, slow_m: float, stop_m: float, reaction_s: float, decel: float):
    """Distance left to the hazard when the chair stands still, and the speed at stop_m."""
    x, v, t = 0.0, TOP_MPS, 0.0  # x: distance driven since the hazard was first seen
    slow_at = stop_at = None  # when each request reaches the wheels
    speed_at_stop_m = None
    while v > 0.0:
        ahead = seen_m - x
        if slow_at is None and ahead <= slow_m:
            slow_at = t + reaction_s
        if stop_at is None and ahead <= stop_m:
            stop_at, speed_at_stop_m = t + reaction_s, v
        target = TOP_MPS
        if slow_at is not None and t >= slow_at:
            target = SLOW * TOP_MPS
        if stop_at is not None and t >= stop_at:
            target = 0.0
        v = max(target, v - decel * DT) if v > target else v
        x += v * DT
        t += DT
        if t > 60.0:
            break
    return seen_m - x, speed_at_stop_m


CASES = (
    ("drop: seen at 2 m, stop at 0.5 m", 2.0, 2.0, 0.5),
    ("obstacle: seen at 2 m, stop at 0.8 m", 2.0, 2.0, 0.8),
    ("late: an obstacle appears 1.2 m ahead", 1.2, 2.0, 0.8),
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    reaction_s = float(args[args.index("--reaction-s") + 1]) if "--reaction-s" in args else 0.2
    decels = (0.5, 1.0, 1.5, 2.0, 3.0)
    print(f"reaction {reaction_s * 1000:.0f} ms; metres left to the hazard at standstill")
    print("| Case | " + " | ".join(f"{a} m/s^2" for a in decels) + " |")
    print("|---|" + "---|" * len(decels))
    for name, seen, slow_m, stop_m in CASES:
        cells = []
        for a in decels:
            left, v = standstill(seen, slow_m, stop_m, reaction_s, a)
            cells.append(f"{left:.2f} ({v:.2f} m/s)" if v is not None else f"{left:.2f}")
        print(f"| {name} | " + " | ".join(cells) + " |")
    print("(in brackets: the speed when the hazard crossed the stop distance)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
