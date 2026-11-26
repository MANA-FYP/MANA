"""End to end: a spoken stop reaches the host as a stop request and the user as a message, a
lost alert link reaches the user as a warning, and a person sensed by Wi-Fi slows it down."""

import time

import pytest
import rclpy
from mana_core.core_node import CoreNode
from rclpy.executors import SingleThreadedExecutor

from mana_msgs.msg import (
    DriveRequest,
    GroundReport,
    LinkReport,
    PresenceReport,
    UserCommand,
    UserMessage,
)


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


def test_a_person_sensed_by_wifi_slows_down_and_never_stops(ros):
    core, probe = CoreNode(), rclpy.create_node("probe")
    drives, messages = [], []
    probe.create_subscription(DriveRequest, "drive_request", drives.append, 10)
    probe.create_subscription(UserMessage, "user_message", messages.append, 10)
    presence = probe.create_publisher(PresenceReport, "presence", 10)
    ground = probe.create_publisher(GroundReport, "ground", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)
    said = "Someone may be near (zone 2), slowing down"
    end = time.monotonic() + 5.0
    while time.monotonic() < end and not any(m.text == said for m in messages):
        stamp = core.get_clock().now().to_msg()
        for source in (GroundReport.CAMERA, GroundReport.DEPTH_CAMERA):  # level ground ahead
            flat = GroundReport(source=source, ground=GroundReport.FLAT, trust=0.9)
            flat.header.stamp = stamp
            ground.publish(flat)
        report = PresenceReport(
            source=PresenceReport.WIFI_CSI, present=True, zone="zone 2", trust=0.9
        )
        report.header.stamp = stamp
        presence.publish(report)
        executor.spin_once(timeout_sec=0.05)
    for node in (core, probe):
        node.destroy_node()

    warned = [m for m in messages if m.text == said]
    assert warned and warned[0].priority == UserMessage.WARNING
    slowed = [d for d in drives if d.reason == said]
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


def mode_after_one_source_sees_stairs(args):
    """Run the node a while with only the camera reporting stairs 1.5 m ahead; its last mode."""
    rclpy.init()
    core, probe = CoreNode(parameter_overrides=args), rclpy.create_node("probe")
    drives = []
    probe.create_subscription(DriveRequest, "drive_request", drives.append, 10)
    ground = probe.create_publisher(GroundReport, "ground", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)
    end = time.monotonic() + 3.0
    while time.monotonic() < end:
        step = GroundReport(
            source=GroundReport.CAMERA, ground=GroundReport.STAIRS_UP, trust=0.9, distance_m=1.5
        )
        step.header.stamp = core.get_clock().now().to_msg()
        ground.publish(step)
        executor.spin_once(timeout_sec=0.05)
    for node in (core, probe):
        node.destroy_node()
    rclpy.shutdown()
    return drives[-1].mode


def test_the_baseline_takes_one_source_for_tracks_and_the_core_wants_two():
    from mana_core.decision import Mode
    from rclpy.parameter import Parameter

    baseline = [Parameter("controller", value="baseline")]
    assert mode_after_one_source_sees_stairs(baseline) == int(Mode.TRACKS)
    assert mode_after_one_source_sees_stairs([]) == int(Mode.WHEELS)


def test_an_unknown_controller_is_refused():
    from rclpy.parameter import Parameter

    rclpy.init()
    try:
        with pytest.raises(ValueError, match="mana or baseline"):
            CoreNode(parameter_overrides=[Parameter("controller", value="magic")])
    finally:
        rclpy.shutdown()


def test_with_the_way_ahead_blocked_the_user_is_told_which_side_is_open(ros):
    """The 3D camera's three corridors (WayReport): blocked ahead, open to the right only. The
    user hears it once (two reports in a row, not again within 10 s); a stale one is ignored."""
    from mana_msgs.msg import WayReport

    core, probe = CoreNode(), rclpy.create_node("probe")
    messages = []
    probe.create_subscription(UserMessage, "user_message", messages.append, 10)
    way = probe.create_publisher(WayReport, "way", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(core)
    executor.add_node(probe)
    text = "Way ahead blocked. Open to your right."
    end = time.monotonic() + 5.0
    while time.monotonic() < end and not any(m.text == text for m in messages):
        r = WayReport(trust=0.9, left_m=1.0, ahead_m=1.2, right_m=float("inf"))
        r.header.stamp = core.get_clock().now().to_msg()
        way.publish(r)
        executor.spin_once(timeout_sec=0.05)
    end = time.monotonic() + 1.0  # more of the same: not said again
    while time.monotonic() < end:
        r = WayReport(trust=0.9, left_m=1.0, ahead_m=1.2, right_m=float("inf"))
        r.header.stamp = core.get_clock().now().to_msg()
        way.publish(r)
        executor.spin_once(timeout_sec=0.05)
    stale = WayReport(trust=0.9, left_m=float("inf"), ahead_m=1.0, right_m=1.0)  # stamped 0
    for _ in range(20):
        way.publish(stale)
        executor.spin_once(timeout_sec=0.05)
    for node in (core, probe):
        node.destroy_node()

    told = [m for m in messages if m.text.startswith("Way ahead")]
    assert [m.text for m in told] == [text]
    assert told[0].speak and told[0].priority == UserMessage.WARNING
