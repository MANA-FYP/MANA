"""End to end: the spoken-stop scenario played live against the core node over ROS 2 topics."""

import time

import pytest
import rclpy
from mana_core.core_node import CoreNode
from mana_core.scenario_node import ScenarioNode
from rclpy.executors import SingleThreadedExecutor
from rclpy.parameter import Parameter


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def test_stop_scenario_meets_its_pass_marks_live(ros):
    core = CoreNode()
    scenario = ScenarioNode(parameter_overrides=[Parameter("scenario", value="stop")])
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(scenario)
    end = time.monotonic() + 20.0
    while scenario.failed is None and time.monotonic() < end:
        executor.spin_once(timeout_sec=0.05)
    for node in (core, scenario):
        node.destroy_node()

    assert scenario.failed == 0
    assert scenario.trace.said and scenario.trace.messages
