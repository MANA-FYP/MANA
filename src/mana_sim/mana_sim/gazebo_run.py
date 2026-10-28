"""Plays the scenarios of gazebo_scenarios.py in Gazebo, one simulation for each, and scores them.

    python3 -m mana_sim.gazebo_run [scenario ...]    # play them (workspace sourced), then score

For every scenario it writes the world of the run (the chair at the start, the boxes), starts the
simulation headless, the core and the scenario runner, waits for the runner to write its trace,
and stops everything. A fresh simulation for each run keeps the runs apart: the chair is on
wheels, the slides are down and no fault is on. mana_core.evaluate --world gazebo calls this.
"""

import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from mana_core.scenarios import check

from mana_sim import gazebo_scenarios as gs

STOP_GRACE_S = 5.0
READY_WAIT_S = 150.0  # for the simulation to start and the chair to settle


def share_dir() -> Path:
    from ament_index_python.packages import get_package_share_directory

    return Path(get_package_share_directory("mana_sim"))


def _start(cmd: list[str], log: Path) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdout=open(log, "w"), stderr=subprocess.STDOUT, start_new_session=True
    )


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
    """Run one scenario; returns the path of its trace. Raises if no trace was written."""
    run = gs.RUNS[name]
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = out_dir / f"{name}.json"
    ready = out_dir / f"{name}.ready"
    trace.unlink(missing_ok=True)
    ready.unlink(missing_ok=True)
    world = out_dir / f"{name}.sdf"
    world.write_text(gs.world_for((share_dir() / "worlds" / "mana_route.sdf").read_text(), run))
    logs = Path(tempfile.mkdtemp(prefix=f"mana_{name}_"))
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
    finally:
        _stop(procs)
    if not trace.exists():
        raise RuntimeError(f"{name}: no trace was written; logs are in {logs}")
    return trace


def score(name: str, trace_path: Path) -> list[tuple[str, bool, str]]:
    trace = gs.trace_from_json(trace_path.read_text())
    return [(m.text, ok, said) for m, ok, said in check(gs.RUNS[name].scenario(), trace)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    names = args or list(gs.RUNS)
    if bad := [n for n in names if n not in gs.RUNS]:
        print(f"unknown scenario {', '.join(bad)}; choose from: {', '.join(gs.RUNS)}")
        return 2
    out = Path("gazebo_traces")
    failed = 0
    for name in names:
        print(f"\n{name}: playing in Gazebo ...", flush=True)
        results = score(name, play(name, out))
        print(f"{gs.RUNS[name].scenario().title} [{name}]")
        for text, ok, said in results:
            failed += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {text:<70} {said}")
    print("\nAll pass marks met." if not failed else f"\n{failed} pass mark(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
