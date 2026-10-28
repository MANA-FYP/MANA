"""Pass-mark acceptance tests: every scripted scenario, run against the decision core in fast time.
The last tests break the core or a sensor on purpose, to show the evaluator catches a failure."""

from dataclasses import replace

import pytest
from mana_core.decision import Limits
from mana_core.evaluate import main, run
from mana_core.scenarios import NO_MISHAP, SCENARIOS, check
from mana_core.world import Fault


def failed(scenario, limits=None):
    return {
        m.text: measured for m, ok, measured in check(scenario, run(scenario, limits)) if not ok
    }


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_meets_its_pass_marks(name):
    assert failed(SCENARIOS[name]) == {}


def test_the_planned_scenarios_are_all_there():
    assert {"level", "ramp", "stairs", "curb", "stop"} <= SCENARIOS.keys()


def test_confirming_stairs_too_late_fails():
    misses = failed(SCENARIOS["stairs"], Limits(step_m=1.0))
    assert "Track request at least 1.5 m before the first step" in misses


def test_ignoring_a_drop_goes_over_it():
    misses = failed(SCENARIOS["drop"], Limits(edge_m=-1.0))
    assert misses[NO_MISHAP.text] == "went over the drop at 8.0 m"


def test_in_the_dark_without_the_lidar_the_person_is_hit():
    dark = SCENARIOS["dark"]
    no_lidar = replace(dark, route=replace(dark.route, faults=(*dark.route.faults, Fault("lidar"))))
    misses = failed(no_lidar)
    assert misses[NO_MISHAP.text] == "hit the person at 8.0 m"


def test_command_line(capsys):
    assert main(["stop", "curb"]) == 0
    out = capsys.readouterr().out
    assert "PASS  Stop acted on within 1 s" in out and "All pass marks met." in out
    assert main(["nowhere"]) == 2


def test_the_world_option_is_checked(capsys):
    assert main(["--world", "mars"]) == 2
    assert "unknown world mars" in capsys.readouterr().out
    assert main(["--world"]) == 2


def test_the_gazebo_world_names_only_the_scenarios_that_play_there(capsys):
    pytest.importorskip("mana_sim")  # built and sourced workspace
    assert main(["--world", "gazebo", "entry"]) == 2
    assert "not in Gazebo: entry" in capsys.readouterr().out
