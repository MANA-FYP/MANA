"""End to end: when the GPS fix is lost, or the GPS falls silent, the node reports the lidar map
within 2 s."""

import time

import pytest
import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from mana_position.position_node import PositionNode
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import NavSatFix, NavSatStatus

from mana_msgs.msg import PositionReport


@pytest.fixture
def ros():
    # a namespace of its own: colcon tests the packages in parallel
    rclpy.init(args=["--ros-args", "-r", "__ns:=/position_node_test"])
    yield
    rclpy.shutdown()


def run(gps_after_loss, lidar_type=Odometry):
    """Good fixes for 1.5 s, then gps_after_loss: "no fix" sends NO_FIX, "silent" sends nothing.
    The lidar localisation reports all along. Returns the reports before and after the loss,
    and how long after the last good fix the lidar map was reported."""
    node, probe = PositionNode(), rclpy.create_node("probe")
    reports = []
    probe.create_subscription(PositionReport, "position", reports.append, 10)
    gps = probe.create_publisher(NavSatFix, "gps/fix", 10)
    topic = "lidar/odometry" if lidar_type is Odometry else "lidar/pose"
    lidar = probe.create_publisher(lidar_type, topic, 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    def spin(seconds, fix_status=None):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            stamp = node.get_clock().now().to_msg()
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
            executor.spin_once(timeout_sec=0.05)

    spin(1.5, NavSatStatus.STATUS_FIX)
    lost_at, last_good = len(reports), time.monotonic()
    while time.monotonic() - last_good < 4.0 and not any(
        r.source == PositionReport.LIDAR_MAP for r in reports[lost_at:]
    ):
        spin(0.1, NavSatStatus.STATUS_NO_FIX if gps_after_loss == "no fix" else None)
    took = time.monotonic() - last_good
    for n in (node, probe):
        n.destroy_node()
    return reports[:lost_at], reports[lost_at:], took


@pytest.mark.parametrize(
    ("gps_after_loss", "lidar_type"),
    [("no fix", Odometry), ("silent", Odometry), ("silent", PoseWithCovarianceStamped)],
    ids=["no fix", "silent", "silent, slam_toolbox pose"],
)
def test_lost_gps_switches_to_the_lidar_map_within_two_seconds(ros, gps_after_loss, lidar_type):
    before, after, took = run(gps_after_loss, lidar_type)

    assert before, "no report arrived while the fix was good"
    assert before[-1].source == PositionReport.GPS and before[-1].error_m == pytest.approx(3.0)
    assert before[-1].header.frame_id == "gps_origin"
    switched = next(i for i, r in enumerate(after) if r.source == PositionReport.LIDAR_MAP)
    assert all(r.source == PositionReport.GPS for r in after[:switched])  # never odometry
    assert after[switched].position.x == 4.0 and after[switched].header.frame_id == "map"
    assert took <= 2.0
