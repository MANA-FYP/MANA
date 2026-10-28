"""Build docs/diagrams/behaviour-tree.html: the decision core's behaviour tree, one tile per branch,
and scenarios played through the real core with the branch that decided each tick.

    python3 tools/behaviour_tree/build.py

It imports mana_core from src/, so the runs are what the core on this branch does. Then take the
PNG with any browser at 1440 x 900, opening the page at #stairs_down-95 (a run and a tick).
Icons: Phosphor Icons (duotone, MIT licence), in icons/.
"""

import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_core"))

from mana_core.decision import DecisionCore  # noqa: E402
from mana_core.evaluate import DT, feed  # noqa: E402
from mana_core.scenarios import SCENARIOS  # noqa: E402
from mana_core.world import World  # noqa: E402

RUNS = ("stairs", "stairs_down", "drop", "picture", "dark_stairs", "stop")


def record(name: str) -> dict:
    """Every tick of one scenario: time, chair position, the branch that decided, mode, speed,
    message, and the command the user gave at that tick."""
    sc = SCENARIOS[name]
    core, world = DecisionCore(), World(sc.route)
    says, t, ticks = sorted(sc.says, key=lambda s: s.at_s), 0.0, []
    while t <= sc.duration_s and not world.finished:
        for r in world.sense(t):
            feed(core, r)
        heard = None
        while says and says[0].at_s <= t:
            s = says.pop(0)
            core.on_command(s.command, s.trust)
            heard = s.command.name
        d = core.tick(t)
        speed = 0.0 if d.stop else round(d.speed_limit, 2)
        branch = core.tree.current_child.name
        ticks.append([round(t, 1), round(world.x, 2), branch, d.mode.name, speed, d.reason, heard])
        world.move(d.mode, d.stop, d.speed_limit, DT)
        t = round(t + DT, 3)
    return {
        "title": sc.title,
        "length": sc.route.length_m,
        "stretches": [[s.start_m, s.end_m, s.ground.name, s.real] for s in sc.route.stretches],
        "obstacles": [[o.at_m, o.label] for o in sc.route.obstacles],
        "ticks": ticks,
    }


# one entry per branch of the tree, highest priority first: what the page shows on a click
BRANCHES = json.loads((HERE / "branches.json").read_text(encoding="utf8"))


def main() -> None:
    names = [b["name"] for b in BRANCHES]
    tree = [c.name for c in DecisionCore().tree.children]
    assert names == tree, f"the page's branches {names} are not the tree's {tree}"
    icons = {}
    for icon in [b["icon"] for b in BRANCHES] + ["play"]:
        svg = (HERE / "icons" / f"{icon}.svg").read_text(encoding="utf8")
        inner = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
        icons[icon] = inner.replace('opacity="0.2"', 'opacity="0.28"')
    runs = {name: record(name) for name in RUNS}
    html = (HERE / "template.html").read_text(encoding="utf8")
    html = html.replace("__BRANCHES__", json.dumps(BRANCHES))
    html = html.replace("__RUNS__", json.dumps(runs, separators=(",", ":")))
    html = html.replace("__ICONS__", json.dumps(icons)).replace("__ICON_play__", icons["play"])
    out = ROOT / "docs" / "diagrams" / "behaviour-tree.html"
    out.write_bytes(html.encode("utf8"))
    ticks = sum(len(r["ticks"]) for r in runs.values())
    print(f"{out.relative_to(ROOT)}: {len(html)} bytes, {ticks} ticks")


if __name__ == "__main__":
    main()
