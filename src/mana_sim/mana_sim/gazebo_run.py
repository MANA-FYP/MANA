"""Plays the scenarios of gazebo_scenarios.py in Gazebo, one simulation for each, and scores them.

    python3 -m mana_sim.gazebo_run [scenario ...]    # play them (workspace sourced), then score

For every scenario it writes the world of the run (the chair at the start, the boxes), starts the
simulation headless, the core and the scenario runner, waits for the runner to write its trace,
and stops everything. A fresh simulation for each run keeps the runs apart: the chair is on
wheels, the slides are down and no fault is on. A run that could not be played (the chair did not settle, the core did not answer, the time
ran out) leaves no trace and is scored as failing every mark, and the next scenario is played.
mana_core.evaluate --world gazebo calls this.
"""

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from mana_core.scenarios import NO_MISHAP, check

from mana_sim import gazebo_scenarios as gs

STOP_GRACE_S = 5.0
READY_WAIT_S = 150.0  # for the simulation to start and the chair to settle


def share_dir() -> Path:
    from ament_index_python.packages import get_package_share_directory

    return Path(get_package_share_directory("mana_sim"))


class RunFailed(RuntimeError):
    """The scenario could not be played to the end, so there is no trace to score."""


def _start(cmd: list[str], log: Path) -> subprocess.Popen:
    with open(log, "w") as out:  # the child has its own copy of the file
        return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, start_new_session=True)


def why_not_played(runner_log: str, returncode: int | None) -> str:
    """Why the runner left no trace: what it said, or that it was stopped."""
    said = [
        line.split(gs.NOT_PLAYED, 1)[1] for line in runner_log.splitlines() if gs.NOT_PLAYED in line
    ]
    if said:
        return said[-1].strip()
    if returncode is None or returncode < 0:
        return "the runner did not finish in time"
    return f"the runner exited with {returncode} and wrote no trace"


def _stop(procs: list[subprocess.Popen]) -> None:
    """Ask each process group to stop, wait a little, then kill what is left."""
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + STOP_GRACE_S
    while time.time() < deadline and any(p.poll() is None for p in procs):
        time.sleep(0.2)
    for p in procs:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        p.wait()


def play(name: str, out_dir: Path, extra_s: float = 120.0) -> Path:
    """Run one scenario; returns the path of its trace. Raises RunFailed if it wrote none."""
    run = gs.RUNS[name]
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = out_dir / f"{name}.json"
    ready = out_dir / f"{name}.ready"
    trace.unlink(missing_ok=True)
    ready.unlink(missing_ok=True)
    world = out_dir / f"{name}.sdf"
    world.write_text(gs.world_for((share_dir() / "worlds" / "mana_route.sdf").read_text(), run))
    logs = out_dir / f"{name}_logs"
    logs.mkdir(exist_ok=True)
    clock = ["--ros-args", "-p", "use_sim_time:=true"]
    procs = [
        _start(
            ["ros2", "launch", "mana_sim", "world.launch.py", "headless:=true", f"world:={world}"],
            logs / "launch.log",
        ),
    ]
    try:
        runner = _start(
            [
                "ros2",
                "run",
                "mana_sim",
                "scenario_runner",
                name,
                "--out",
                str(trace),
                "--ready",
                str(ready),
                *clock,
            ],
            logs / "runner.log",
        )
        procs.append(runner)
        deadline = time.time() + READY_WAIT_S  # the runner says when the chair has settled
        while not ready.exists() and runner.poll() is None and time.time() < deadline:
            time.sleep(0.5)
        if ready.exists():
            procs.append(
                _start(["ros2", "run", "mana_core", "core_node", *clock], logs / "core.log")
            )
        try:
            runner.wait(timeout=run.duration_s * 4 + extra_s)
        except subprocess.TimeoutExpired:
            pass
        returncode = runner.poll()
    finally:
        _stop(procs)
    if returncode != 0 or not trace.exists():
        trace.unlink(missing_ok=True)
        why = why_not_played((logs / "runner.log").read_text(errors="replace"), returncode)
        raise RunFailed(f"{why}; logs are in {logs}")
    return trace


def score(name: str, trace_path: Path) -> list[tuple[str, bool, str]]:
    trace = gs.trace_from_json(trace_path.read_text())
    return [(m.text, ok, said) for m, ok, said in check(gs.RUNS[name].scenario(), trace)]


def not_played(name: str, why: str) -> list[tuple[str, bool, str]]:
    """Every mark of a run that could not be played, failed."""
    marks = (*gs.RUNS[name].scenario().marks, NO_MISHAP)
    return [(m.text, False, f"not played: {why}") for m in marks]


def results(name: str, traces: Path, rerun: bool = True) -> list[tuple[str, bool, str]]:
    """Play the scenario (or take its trace, unless rerun) and score it."""
    path = traces / f"{name}.json"
    if rerun or not path.exists():
        print(f"{name}: playing in Gazebo ...", flush=True)
        try:
            path = play(name, traces)
        except RunFailed as e:
            return not_played(name, str(e))
    return score(name, path)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    names = args or list(gs.RUNS)
    if bad := [n for n in names if n not in gs.RUNS]:
        print(f"unknown scenario {', '.join(bad)}; choose from: {', '.join(gs.RUNS)}")
        return 2
    out = Path("gazebo_traces")
    failed = 0
    for name in names:
        scored = results(name, out)
        print(f"\n{gs.RUNS[name].scenario().title} [{name}]")
        for text, ok, said in scored:
            failed += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {text:<70} {said}")
    print("\nAll pass marks met." if not failed else f"\n{failed} pass mark(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
