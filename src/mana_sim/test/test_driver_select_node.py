"""End to end: the driver select node follows the autopilot, then the supervisor once they take
the controls (also when the take-over was sent before the node started), and says who drives."""

import time

import pytest
import rclpy
from geometry_msgs.msg import Twist, Vector3
from mana_sim.driver_select_node import DriverSelectNode
from rclpy.executors import SingleThreadedExecutor
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool, String


@pytest.fixture
def ros():
    # a namespace of its own: colcon tests the packages in parallel
    rclpy.init(args=["--ros-args", "-r", "__ns:=/driver_select_test"])
    yield
    rclpy.shutdown()


def test_the_supervisor_takes_over_from_the_autopilot(ros):
    probe = rclpy.create_node("probe")
    out, drivers = [], []
    probe.create_subscription(Twist, "cmd_vel", out.append, 10)
    probe.create_subscription(String, "driver", lambda m: drivers.append(m.data), 10)
    autopilot = probe.create_publisher(Twist, "cmd_vel/autopilot", 10)
    supervisor = probe.create_publisher(Twist, "cmd_vel/supervisor", 10)
    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    control = probe.create_publisher(Bool, "supervisor/control", latched)
    node = DriverSelectNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    def spin(seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            autopilot.publish(Twist(linear=Vector3(x=0.8)))
            supervisor.publish(Twist(linear=Vector3(x=0.2), angular=Vector3(z=0.5)))
            executor.spin_once(timeout_sec=0.05)

    spin(2.0)
    assert drivers and drivers[-1] == "autopilot" and out[-1].linear.x == pytest.approx(0.8)
    control.publish(Bool(data=True))
    spin(1.0)
    assert drivers[-1] == "supervisor"
    assert (out[-1].linear.x, out[-1].angular.z) == (pytest.approx(0.2), pytest.approx(0.5))
    control.publish(Bool(data=False))
    spin(1.0)
    assert drivers[-1] == "autopilot" and out[-1].linear.x == pytest.approx(0.8)
    for n in (node, probe):
        n.destroy_node()


def test_a_take_over_sent_before_the_node_started_still_counts(ros):
    probe = rclpy.create_node("probe")
    drivers = []
    probe.create_subscription(String, "driver", lambda m: drivers.append(m.data), 10)
    latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
    control = probe.create_publisher(Bool, "supervisor/control", latched)
    control.publish(Bool(data=True))  # before the select node exists
    node = DriverSelectNode()
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and not (drivers and drivers[-1] == "supervisor"):
        executor.spin_once(timeout_sec=0.05)
    assert drivers and drivers[-1] == "supervisor"
    for n in (node, probe):
        n.destroy_node()
