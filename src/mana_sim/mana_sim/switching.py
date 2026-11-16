"""How the position source switched during GPS outages (#42). No ROS.

The position node's reports (time, source) are compared with the outages (start, end): how long
after each start the first report from another source came and which source that was, how many
times the source changed from that start to the next outage (or the end of the run), how long was
spent on each source, and how long after the end GPS came back. The target: a switch to the lidar
map within 2 s, one switch away and one back per outage, and no time on odometry. Away from GPS
is not enough: if the lidar gave no fresh pose, the position node falls back to odometry, which
is also "away" (Waqas, #182).
"""

from __future__ import annotations

from dataclasses import dataclass

GPS, LIDAR_MAP, ODOMETRY = 0, 1, 2  # mana_msgs/PositionReport
NAMES = {GPS: "GPS", LIDAR_MAP: "lidar map", ODOMETRY: "odometry"}


@dataclass
class Outage:
    start_s: float
    end_s: float
    switch_s: float | None  # from the start to the first report from another source
    changes: int  # source changes from the start to the next outage
    back_s: float | None  # from the end to the first GPS report after it
    away_source: int | None = None  # the source of that first other report
    on_s: dict[int, float] | None = None  # seconds on each source, start to end of the outage

    @property
    def passed(self) -> bool:
        return (
            self.switch_s is not None
            and self.switch_s <= 2.0
            and self.away_source == LIDAR_MAP
            and self.changes <= 2
            and (self.on_s or {}).get(ODOMETRY, 0.0) == 0.0
        )


def outcomes(reports: list[tuple[float, int]], outages: list[tuple[float, float]]) -> list[Outage]:
    reports = sorted(reports)
    out = []
    for i, (start, end) in enumerate(outages):
        until = outages[i + 1][0] if i + 1 < len(outages) else float("inf")
        before = [s for t, s in reports if t < start]
        inside = [(t, s) for t, s in reports if start <= t < until]
        first = next(((t, s) for t, s in inside if s != GPS), None)
        away = None if first is None else first[0] - start
        back = next((t - end for t, s in inside if t >= end and s == GPS), None)
        sources = before[-1:] + [s for _, s in inside]
        changes = sum(a != b for a, b in zip(sources, sources[1:], strict=False))
        # each report holds until the next one; counted within the outage itself
        on: dict[int, float] = {}
        timeline = ([(start, before[-1])] if before else []) + [
            (t, s) for t, s in inside if t < end
        ]
        for (t, s), (t2, _) in zip(timeline, timeline[1:] + [(end, None)], strict=False):
            on[s] = on.get(s, 0.0) + max(0.0, min(t2, end) - max(t, start))
        out.append(Outage(start, end, away, changes, back, None if first is None else first[1], on))
    return out


def read_reports(csv_path: str) -> list[tuple[float, int]]:
    """(time, source) from `ros2 topic echo /position --csv`: sec, nanosec, frame, trust, source."""
    out = []
    with open(csv_path) as f:
        for line in f:
            cols = line.strip().split(",")
            if len(cols) >= 5 and cols[0].isdigit():
                out.append((int(cols[0]) + int(cols[1]) * 1e-9, int(cols[4])))
    return out


def read_outages(log_path: str) -> list[tuple[float, float]]:
    """(start, end) of each GPS dropout in fault_script's log ("(clock 123.45): gps dropout on")."""
    import re

    times = re.findall(r"clock ([\d.]+)\): gps dropout (on|off)", open(log_path).read())
    starts = [float(t) for t, s in times if s == "on"]
    ends = [float(t) for t, s in times if s == "off"]
    return list(zip(starts, ends, strict=False))


def main(argv=None) -> int:
    import sys

    position_csv, fault_log = (argv or sys.argv[1:])[:2]
    outs = outcomes(read_reports(position_csv), read_outages(fault_log))
    for i, o in enumerate(outs, 1):
        away = "never" if o.switch_s is None else f"{o.switch_s:.2f} s"
        to = NAMES.get(o.away_source, "nothing")
        back = "not yet" if o.back_s is None else f"{o.back_s:.2f} s after it ended"
        spent = ", ".join(f"{NAMES.get(s, s)} {v:.1f} s" for s, v in sorted((o.on_s or {}).items()))
        print(f"outage {i} ({o.end_s - o.start_s:.0f} s): to the {to} after {away}, "
              f"{o.changes} source changes, GPS back {back}; on {spent}")  # fmt: skip
    ok = bool(outs) and all(o.passed for o in outs)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
