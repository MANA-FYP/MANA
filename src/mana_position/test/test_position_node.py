"""End to end: when the GPS fix is lost, the node reports the lidar map within 2 s."""

import time

import pytest
import rclpy
from mana_position.position_node import PositionNode
from nav_msgs.msg import Odometry
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import NavSatFix, NavSatStatus

from mana_msgs.msg import PositionReport


@pytest.fixture
def ros():
    rclpy.init()
    yield
    rclpy.shutdown()


def test_lost_gps_switches_to_the_lidar_map_within_two_seconds(ros):
    node, probe = PositionNode(), rclpy.create_node("probe")
    reports = []
    probe.create_subscription(PositionReport, "position", reports.append, 10)
    gps = probe.create_publisher(NavSatFix, "gps/fix", 10)
    lidar = probe.create_publisher(Odometry, "lidar/odometry", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    def spin(seconds, fix_status):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            stamp = node.get_clock().now().to_msg()
            fix = NavSatFix(latitude=33.6844, longitude=73.0479)
            fix.status.status = fix_status
            fix.position_covariance = [9.0, 0, 0, 0, 9.0, 0, 0, 0, 9.0]
            fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
            odom = Odometry()
            odom.pose.pose.position.x = 4.0
            for msg in (fix, odom):
                msg.header.stamp = stamp
            gps.publish(fix)
            lidar.publish(odom)
            executor.spin_once(timeout_sec=0.05)

    spin(1.5, NavSatStatus.STATUS_FIX)
    lost_at = len(reports)
    started = time.monotonic()
    while time.monotonic() - started < 4.0 and not any(
        r.source == PositionReport.LIDAR_MAP for r in reports[lost_at:]
    ):
        spin(0.1, NavSatStatus.STATUS_NO_FIX)
    took = time.monotonic() - started
    for n in (node, probe):
        n.destroy_node()

    assert reports[lost_at - 1].source == PositionReport.GPS
    assert reports[lost_at - 1].error_m == pytest.approx(3.0)
    switched = next(r for r in reports[lost_at:] if r.source == PositionReport.LIDAR_MAP)
    assert switched.position.x == 4.0 and took <= 2.0
