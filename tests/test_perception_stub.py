"""Checks on the perception stub that need no ROS install.

The stub must report only codes that exist in mana_msgs, and its scenarios must be the situations
the decision core is designed for: stairs seen by two sources, a drop seen by one, and sources that
disagree.
"""

import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "mana_perception"))

from mana_perception import scenarios as sc  # noqa: E402

MSG = ROOT / "src" / "mana_msgs" / "msg"


def constants(msg_file):
    """The NAME=value constants of a .msg file."""
    text = (MSG / msg_file).read_text()
    return {name: int(value) for name, value in re.findall(r"^uint8 (\w+)=(\d+)", text, re.M)}


def all_ground():
    return [g for name in sc.SCENARIOS for g in sc.reports(name)[0]]


def test_codes_match_the_message_files():
    ground = constants("GroundReport.msg")
    obstacle = constants("ObstacleReport.msg")
    for name in ("CAMERA", "DEPTH_CAMERA"):
        assert getattr(sc, name) == ground[name]
    for name in ("FLAT", "RAMP", "STAIRS_UP", "STAIRS_DOWN", "CURB", "DROP", "UNKNOWN"):
        assert getattr(sc, name) == ground[name]
    for name in ("CAMERA", "DEPTH_CAMERA", "LIDAR"):
        assert getattr(sc, name) == obstacle[name]


def test_every_scenario_uses_valid_codes_and_distances():
    ground = constants("GroundReport.msg")
    valid_ground = {v for k, v in ground.items() if k not in ("CAMERA", "DEPTH_CAMERA")}
    for name in sc.SCENARIOS:
        grounds, obstacles = sc.reports(name)
        assert grounds, name
        for g in grounds:
            assert g.source in (sc.CAMERA, sc.DEPTH_CAMERA), name
            assert g.ground in valid_ground, name
            assert math.isnan(g.distance_m) or g.distance_m > 0, name
        for o in obstacles:
            assert o.source in (sc.CAMERA, sc.DEPTH_CAMERA, sc.LIDAR), name
            assert o.distance_m > 0, name


def test_stairs_are_seen_by_both_cameras():
    grounds, _ = sc.reports("stairs")
    assert {g.source for g in grounds} == {sc.CAMERA, sc.DEPTH_CAMERA}
    assert all(g.ground == sc.STAIRS_UP for g in grounds)
    assert any(g.step_height_m > 0 for g in grounds)


def test_drop_is_seen_by_the_depth_camera_only():
    grounds, _ = sc.reports("drop")
    seen = {g.source: g.ground for g in grounds}
    assert seen[sc.DEPTH_CAMERA] == sc.DROP
    assert seen[sc.CAMERA] == sc.FLAT


def test_conflict_scenario_has_sources_that_disagree():
    grounds, _ = sc.reports("conflict")
    assert len({g.ground for g in grounds}) == 2


def test_level_ground_has_no_obstacle_and_no_step():
    grounds, obstacles = sc.reports("level")
    assert not obstacles
    assert all(g.ground == sc.FLAT for g in grounds)


def test_cycle_plays_every_scenario():
    assert set(sc.CYCLE) == set(sc.SCENARIOS)


def test_cycle_moves_on_and_wraps_around():
    assert sc.scenario_at(0.0, 5.0) == sc.CYCLE[0]
    assert sc.scenario_at(5.0, 5.0) == sc.CYCLE[1]
    assert sc.scenario_at(5.0 * len(sc.CYCLE), 5.0) == sc.CYCLE[0]
    assert sc.scenario_at(-3.0, 5.0) == sc.CYCLE[0]


def test_unknown_scenario_is_rejected():
    try:
        sc.reports("sunny")
    except ValueError as err:
        assert "sunny" in str(err)
    else:
        raise AssertionError("an unknown scenario must raise ValueError")


def test_reports_are_copies():
    first, _ = sc.reports("level")
    first.clear()
    again, _ = sc.reports("level")
    assert again
