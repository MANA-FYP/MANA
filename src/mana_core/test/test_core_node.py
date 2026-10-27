"""End to end: a spoken stop reaches the host as a stop request and the user as a message, and a
lost alert link reaches the user as a warning."""

import time

import pytest
import rclpy
from mana_core.core_node import CoreNode
from rclpy.executors import SingleThreadedExecutor

from mana_msgs.msg import DriveRequest, GroundReport, LinkReport, UserCommand, UserMessage


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def test_spoken_stop_reaches_the_host_and_the_user(ros):
    core, probe = CoreNode(), rclpy.create_node("probe")
    drives, messages = [], []
    probe.create_subscription(DriveRequest, "drive_request", drives.append, 10)
    probe.create_subscription(UserMessage, "user_message", messages.append, 10)
    say = probe.create_publisher(UserCommand, "user_command", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)

    def spin_until(done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            executor.spin_once(timeout_sec=0.05)
        return done()

    assert spin_until(lambda: say.get_subscription_count() > 0 and drives)
    say.publish(UserCommand(command=UserCommand.STOP, source=UserCommand.VOICE, trust=0.9))
    stopped = spin_until(
        lambda: drives[-1].stop and any(m.text == "Stopped by you" for m in messages)
    )
    for node in (core, probe):
        node.destroy_node()

    assert stopped
    spoken = next(m for m in messages if m.text == "Stopped by you")
    assert spoken.speak and spoken.priority == UserMessage.URGENT


def test_a_lost_alert_link_slows_down_and_warns_the_user(ros):
    core, probe = CoreNode(), rclpy.create_node("probe")
    drives, messages = [], []
    probe.create_subscription(DriveRequest, "drive_request", drives.append, 10)
    probe.create_subscription(UserMessage, "user_message", messages.append, 10)
    link = probe.create_publisher(LinkReport, "link", 10)
    ground = probe.create_publisher(GroundReport, "ground", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)
    end = time.monotonic() + 5.0
    while time.monotonic() < end and not any(
        m.text == "No alert link, slowing down" for m in messages
    ):
        stamp = core.get_clock().now().to_msg()
        for source in (GroundReport.CAMERA, GroundReport.DEPTH_CAMERA):  # level ground ahead
            flat = GroundReport(source=source, ground=GroundReport.FLAT, trust=0.9)
            flat.header.stamp = stamp
            ground.publish(flat)
        report = LinkReport(link=LinkReport.WIFI, connected=False, trust=0.9)
        report.header.stamp = stamp
        link.publish(report)
        executor.spin_once(timeout_sec=0.05)
    for node in (core, probe):
        node.destroy_node()

    warned = [m for m in messages if m.text == "No alert link, slowing down"]
    assert warned and warned[0].speak and warned[0].priority == UserMessage.WARNING
    slowed = [d for d in drives if d.reason == "No alert link, slowing down"]
    assert slowed and all(d.speed_limit == pytest.approx(0.3) and not d.stop for d in slowed)


def steps_ahead_messages(args):
    """Run the node for a while with steps 1.5 m ahead, and return what the user was told."""
    rclpy.init(args=args)
    core, probe = CoreNode(), rclpy.create_node("probe")
    messages = []
    probe.create_subscription(UserMessage, "user_message", messages.append, 10)
    ground = probe.create_publisher(GroundReport, "ground", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)
    end = time.monotonic() + 8.0  # the decision is said first, then the direction
    while time.monotonic() < end:
        stamp = core.get_clock().now().to_msg()
        for source, distance in (
            (GroundReport.CAMERA, float("nan")),
            (GroundReport.DEPTH_CAMERA, 1.5),
        ):
            step = GroundReport(
                source=source, ground=GroundReport.STAIRS_UP, trust=0.9, distance_m=distance
            )
            step.header.stamp = (
                stamp  # two sensors agree: the camera names it, the 3D camera measures it
            )
            ground.publish(step)
        for _ in range(6):  # one call handles one message: let the node take both reports
            executor.spin_once(timeout_sec=0.01)
    for node in (core, probe):
        node.destroy_node()
    rclpy.shutdown()
    return [m.text for m in messages]


def test_a_cane_host_is_told_what_is_ahead():
    told = steps_ahead_messages(["--ros-args", "-p", "directions:=true"])
    assert "Steps up ahead, 1 metre." in told


def test_a_chair_host_gets_no_step_directions_by_default():
    told = steps_ahead_messages([])
    assert told and not any(text.startswith("Steps up ahead") for text in told)
