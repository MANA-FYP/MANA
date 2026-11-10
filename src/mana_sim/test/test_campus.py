"""The open test world (#79), made by mana_sim.campus. No ROS, no Gazebo."""

import math
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from mana_sim.campus import CHAIR_Z, Variation, build, main, to_sdf
from mana_sim.world_variants import without_rendering_sensors

WORLD = Path(__file__).parents[1] / "worlds" / "mana_campus.sdf"
SEEDS = range(25)
CHAIR_HALF = 0.35  # half the chair's width, with a little room


def rot(roll, pitch, yaw, v):
    """v turned as SDF turns a pose: yaw, then pitch, then roll (R = Rz Ry Rx)."""
    x, y, z = v
    y, z = y * math.cos(roll) - z * math.sin(roll), y * math.sin(roll) + z * math.cos(roll)
    x, z = x * math.cos(pitch) + z * math.sin(pitch), -x * math.sin(pitch) + z * math.cos(pitch)
    x, y = x * math.cos(yaw) - y * math.sin(yaw), x * math.sin(yaw) + y * math.cos(yaw)
    return x, y, z


def test_the_world_file_is_the_default_campus():
    assert WORLD.read_text() == to_sdf(build())


def test_the_world_parses_with_the_chair_on_the_plaza_and_runs_without_a_gpu():
    sdf = to_sdf(build())
    world = ET.fromstring(sdf).find("world")
    assert world.get("name") == "mana_campus"
    pose = [float(v) for v in world.find("include/pose").text.split()]
    x, y, yaw, ground = build().starts["plaza"]
    assert pose == pytest.approx([x, y, ground + CHAIR_Z, 0, 0, yaw], abs=1e-3)
    assert "gz-sim-sensors-system" not in without_rendering_sensors(sdf)
    assert "--" not in sdf.split("<!--")[1].split("-->")[0]  # not allowed in an XML comment


def test_every_hazard_is_there_at_its_height():
    c = build()
    v = c.variation
    names = {b.name for b in c.boxes} | {r.name for r in c.ramps}
    for hazard in ("steps_up_1", "stairs_down_1", "entrance_ramp", "terrace", "terrace_ramp",
                   "curb_cut", "dark_room_roof", "table_hall_0"):  # fmt: skip
        assert hazard in names
    assert c.ground(-4, -11) == 0.0 and c.ground(-4, -5) == pytest.approx(v.curb_m)  # the curb
    assert c.ground(10, 5.9) == pytest.approx(c.plaza + 2 * v.riser_m)  # a step below the floor
    assert c.ground(10, 8) == pytest.approx(c.floor)
    assert c.ground(18, 26.5) == pytest.approx(c.plaza + v.riser_m)  # the stairs down
    assert c.ground(-19, 19) - c.ground(-19, 21) == pytest.approx(v.drop_m)  # the drop


@pytest.mark.parametrize("seed", [None, *SEEDS])
def test_each_ramp_runs_from_its_low_end_to_its_high_end(seed):
    c = build(None if seed is None else Variation.from_seed(seed))
    for r in c.ramps:
        (x, y, z, roll, pitch, yaw), (sx, sy, sz) = r.pose()
        # the middle of the top face's two ends, as Gazebo places them
        along = (sx / 2, 0, sz / 2) if "x" in r.rises else (0, sy / 2, sz / 2)
        ends = []
        for s in (1, -1):
            dx, dy, dz = rot(roll, pitch, yaw, (along[0] * s, along[1] * s, along[2]))
            ends.append((x + dx, y + dy, z + dz))
        low, high = sorted(ends, key=lambda e: e[2])
        assert low[2] == pytest.approx(r.z_low, abs=1e-6)
        assert high[2] == pytest.approx(r.z_high, abs=1e-6)
        axis = 0 if "x" in r.rises else 1
        lo, hi = (r.x0, r.x1) if axis == 0 else (r.y0, r.y1)
        assert high[axis] == pytest.approx(hi if r.rises[0] == "+" else lo, abs=1e-6)


def test_a_seed_varies_the_world_within_the_ranges_of_the_issue():
    vs = [Variation.from_seed(s) for s in SEEDS]
    assert all(0.10 <= v.riser_m <= 0.20 and 0.10 <= v.curb_m <= 0.20 for v in vs)
    assert all(3.0 <= v.entrance_ramp_deg <= 6.0 and 5.0 <= v.terrace_ramp_deg <= 9.0 for v in vs)
    assert len({v.riser_m for v in vs}) > 20  # not all the same
    assert to_sdf(build(Variation.from_seed(3))) == to_sdf(build(Variation.from_seed(3)))
    assert to_sdf(build(Variation.from_seed(3))) != to_sdf(build(Variation.from_seed(4)))


def walkable_from(c, start, cell=0.5):
    """The cells a chair can reach from `start`: free of walls and furniture, and each move no
    higher than a step or the curb (the tracks climb those), so never down a drop."""
    v = c.variation
    climb = max(v.riser_m, v.curb_m) + 0.01
    seen, todo = set(), [(round(start[0] / cell), round(start[1] / cell))]
    while todo:
        i, j = todo.pop()
        if (i, j) in seen:
            continue
        seen.add((i, j))
        z = c.ground(i * cell, j * cell)
        for a, b in ((i + 1, j), (i - 1, j), (i, j + 1), (i, j - 1)):
            x, y = a * cell, b * cell
            g = c.ground(x, y)
            if (a, b) in seen or g is None or abs(g - z) > climb:
                continue
            if not c.blocked(x, y, CHAIR_HALF, g):
                todo.append((a, b))
    return seen


@pytest.mark.parametrize("seed", [None, 1, 2, 5, 11])
def test_every_start_is_clear_and_reachable_from_the_plaza(seed):
    c = build(None if seed is None else Variation.from_seed(seed))
    reached = walkable_from(c, c.starts["plaza"])
    for name, (x, y, _, ground) in c.starts.items():
        assert c.blocked(x, y, CHAIR_HALF, ground) == [], name
        assert (round(x / 0.5), round(y / 0.5)) in reached, name
    # the terrace is reached up its ramp; beside it is the plaza, a drop below
    x, y, _, ground = c.starts["terrace"]
    assert ground == pytest.approx(c.terrace) and c.ground(-26.2, y) == pytest.approx(c.plaza)
    # every ramp starts on the plaza, not in the street
    assert all(r.y0 > -6.0 for r in c.ramps if r.name != "curb_cut")


def test_the_command_line_writes_a_start_and_refuses_an_unknown_one(capsys):
    assert main(["--seed", "2", "--start", "terrace"]) == 0
    out = capsys.readouterr().out
    c = build(Variation.from_seed(2))
    x, y, yaw, ground = c.starts["terrace"]
    pose = ET.fromstring(out).find("world/include/pose").text.split()
    assert [float(p) for p in pose[:3]] == pytest.approx([x, y, ground + CHAIR_Z], abs=1e-3)
    with pytest.raises(SystemExit):
        main(["--start", "the moon"])
