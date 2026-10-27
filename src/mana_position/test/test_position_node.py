"""End to end: when the GPS fix is lost, or the GPS falls silent, the node reports the lidar map
within 2 s. The test drives the node's clock (use_sim_time) and lets the node catch up after each
step, so a slow or busy machine cannot make the node look late."""

import time

import pytest
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from mana_position.position_node import PositionNode
from nav_msgs.msg import Odometry
from rclpy.duration import Duration
from rclpy.executors import SingleThreadedExecutor
from rclpy.time import Time
from rosgraph_msgs.msg import Clock
from sensor_msgs.msg import NavSatFix, NavSatStatus

from mana_msgs.msg import PositionReport

STEP = Duration(nanoseconds=50_000_000)  # each step moves the clock 0.05 s


@pytest.fixture
def ros():
    # a namespace of its own: colcon tests the packages in parallel
    rclpy.init(args=["--ros-args", "-r", "__ns:=/position_node_test", "-p", "use_sim_time:=true"])
    yield
    rclpy.shutdown()


def run(gps_after_loss, lidar_type=Odometry):
    """Good fixes until the node reports GPS and 0.5 s more, then gps_after_loss: "no fix" sends
    NO_FIX, "silent" sends nothing. The lidar localisation reports all along. Returns the reports
    before and after the loss, and the stamp of the last good fix."""
    node, probe = PositionNode(), rclpy.create_node("probe")
    reports = []
    probe.create_subscription(PositionReport, "position", reports.append, 10)
    clock = probe.create_publisher(Clock, "/clock", 10)
    gps = probe.create_publisher(NavSatFix, "gps/fix", 10)
    topic = "lidar/odometry" if lidar_type is Odometry else "lidar/pose"
    lidar = probe.create_publisher(lidar_type, topic, 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)
    now = Time(seconds=100)  # sim time 0 would mean "no clock yet"

    def until(done, what):
        end = time.monotonic() + 10.0
        while not done():
            assert time.monotonic() < end, f"timed out waiting for {what}"
            executor.spin_once(timeout_sec=0.01)

    until(
        lambda: (
            gps.get_subscription_count()
            and lidar.get_subscription_count()
            and node.report.get_subscription_count()
        ),
        "the test and the node to connect",
    )

    def step(fix_status=None):
        nonlocal now
        now += STEP
        stamp = now.to_msg()
        if fix_status is not None:
            fix = NavSatFix(latitude=33.6844, longitude=73.0479)
            fix.status.status = fix_status
            fix.position_covariance = [9.0, 0, 0, 0, 9.0, 0, 0, 0, 9.0]
            fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
            fix.header.stamp = stamp
            gps.publish(fix)
        odom = lidar_type()
        odom.pose.pose.position.x = 4.0
        odom.header.stamp = stamp
        lidar.publish(odom)

        def caught_up():
            clock.publish(Clock(clock=stamp))  # /clock is best effort, so send until it is heard
            return (
                node.get_clock().now().nanoseconds == now.nanoseconds
                and node.lidar is not None
                and node.lidar.header.stamp == stamp
                and (
                    fix_status is None or (node.fix is not None and node.fix.header.stamp == stamp)
                )
                and all(t.time_until_next_call() > 0 for t in node.timers)  # ticks done
            )

        until(caught_up, f"the node at {now.nanoseconds / 1e9:.2f} s")
        return stamp

    for _ in range(200):
        if reports and reports[-1].source == PositionReport.GPS:
            break
        step(NavSatStatus.STATUS_FIX)
    for _ in range(10):
        last_good = step(NavSatStatus.STATUS_FIX)
    lost_at = len(reports)
    for _ in range(80):  # 4 s
        if any(r.source == PositionReport.LIDAR_MAP for r in reports[lost_at:]):
            break
        step(NavSatStatus.STATUS_NO_FIX if gps_after_loss == "no fix" else None)
    for n in (node, probe):
        n.destroy_node()
    return reports[:lost_at], reports[lost_at:], last_good


@pytest.mark.parametrize(
    ("gps_after_loss", "lidar_type"),
    [("no fix", Odometry), ("silent", Odometry), ("silent", PoseWithCovarianceStamped)],
    ids=["no fix", "silent", "silent, slam_toolbox pose"],
)
def test_lost_gps_switches_to_the_lidar_map_within_two_seconds(ros, gps_after_loss, lidar_type):
    before, after, last_good = run(gps_after_loss, lidar_type)

    assert before, "no report arrived while the fix was good"
    assert before[-1].source == PositionReport.GPS and before[-1].error_m == pytest.approx(3.0)
    assert before[-1].header.frame_id == "gps_origin"
    switched = next(i for i, r in enumerate(after) if r.source == PositionReport.LIDAR_MAP)
    assert all(r.source == PositionReport.GPS for r in after[:switched])  # never odometry
    assert after[switched].position.x == 4.0 and after[switched].header.frame_id == "map"
    took = Time.from_msg(after[switched].header.stamp) - Time.from_msg(last_good)
    # not on the first missed fix (out_s and max_age_s are 1.5 s), and within 2 s
    assert 1.5 <= took.nanoseconds / 1e9 <= 2.0
