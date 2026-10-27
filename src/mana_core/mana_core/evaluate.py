"""Evaluator: runs the scripted scenarios against the decision core in fast time and checks each
pass mark. tests/test_scenarios.py runs it in CI; from a shell:

    python3 -m mana_core.evaluate [scenario ...]     # or: ros2 run mana_core evaluate

It prints one line per pass mark and exits with 1 if any of them fails.
"""

import sys

from mana_core.decision import DecisionCore, GroundReport, Limits, ObstacleReport, Priority
from mana_core.messages import Message as Heard
from mana_core.messages import MessageQueue, direction
from mana_core.scenarios import SCENARIOS, Message, Sample, Scenario, Trace, check
from mana_core.world import World

DT = 0.1  # the core node decides 10 times a second


def feed(core: DecisionCore, report) -> None:
    if isinstance(report, GroundReport):
        core.on_ground(report)
    elif isinstance(report, ObstacleReport):
        core.on_obstacle(report)
    else:
        core.on_motion(report)


def run(scenario: Scenario, limits: Limits | None = None, cane: bool = False) -> Trace:
    """Play a scenario. trace.messages has every change of reason, as the node makes them;
    trace.heard has what the user would hear, one at a time, urgent first. With cane=True the
    host also says what is ahead (step directions), as a smart cane would."""
    core, world, trace = DecisionCore(limits), World(scenario.route), Trace()
    queue = MessageQueue()
    says = sorted(scenario.says, key=lambda s: s.at_s)
    t = 0.0
    while t <= scenario.duration_s and not world.finished:
        for report in world.sense(t):
            feed(core, report)
        while says and says[0].at_s <= t:
            say = says.pop(0)
            core.on_command(say.command, say.trust)
            trace.said.append((t, say.command))
        d = core.tick(t)
        if not trace.samples or d.reason != trace.samples[-1].reason:  # as the node does
            speak = d.priority >= Priority.WARNING
            trace.messages.append(Message(t, d.reason, speak))
            queue.push(Heard(d.reason, d.priority, speak, t), t)
        if cane:
            if ahead := direction(core, t):
                queue.push(ahead, t)
            else:
                queue.direction_gone()
        if out := queue.release(t):
            trace.heard.append((t, out))
        trace.samples.append(Sample(t, world.x, d.mode, d.stop, d.speed_limit, d.reason))
        world.move(d.mode, d.stop, d.speed_limit, DT)
        t = round(t + DT, 3)
    trace.end_m, trace.mishaps = world.x, world.mishaps
    return trace


def report(scenario: Scenario, trace: Trace) -> int:
    """Print the pass marks of one run; return how many failed."""
    print(f"\n{scenario.title} [{scenario.name}]: {scenario.shows}")
    failed = 0
    for mark, ok, measured in check(scenario, trace):
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {mark.text:<70} {measured}")
    return failed


def main(argv: list[str] | None = None) -> int:
    names = (sys.argv[1:] if argv is None else argv) or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        print(f"unknown scenario {', '.join(unknown)}; choose from: {', '.join(SCENARIOS)}")
        return 2
    failed = sum(report(SCENARIOS[n], run(SCENARIOS[n])) for n in names)
    print("\nAll pass marks met." if not failed else f"\n{failed} pass mark(s) failed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
