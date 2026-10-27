"""ROS 2 node for the position source (M2).

Listens to the GPS fix, the lidar localisation and the fused odometry, chooses one source 10 times
a second with PositionSelector, and publishes a PositionReport for it on `position`. Nothing is
published while the chosen source has no recent data, so the core then sees the position as lost.

The GPS position is in metres east and north of the first fix, a flat-earth approximation that is
good over a building's grounds; aligning it with the lidar map is the job of robot_localization's
navsat_transform (#43). Trust is fixed per source: odometry drifts, so it is trusted less.
"""

import math

import rclpy
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import NavSatFix, NavSatStatus

from mana_msgs.msg import PositionReport
from mana_position.selector import PositionSelector, Source

EARTH_RADIUS_M = 6_371_000.0
TRUST = {Source.GPS: 0.9, Source.LIDAR_MAP: 0.9, Source.ODOMETRY: 0.6}
NAMES = {Source.GPS: "GPS", Source.LIDAR_MAP: "the lidar map", Source.ODOMETRY: "odometry"}


def gps_error_m(fix: NavSatFix, unknown_m: float) -> float | None:
    """Horizontal error of a fix, from its covariance; None when there is no fix."""
    if fix.status.status < NavSatStatus.STATUS_FIX:
        return None
    if fix.position_covariance_type == NavSatFix.COVARIANCE_TYPE_UNKNOWN:
        return unknown_m
    return math.sqrt(max(fix.position_covariance[0], fix.position_covariance[4]))


def odometry_error_m(odom: Odometry) -> float:
    c = odom.pose.covariance
    return math.sqrt(max(c[0], c[7])) if c[0] or c[7] else math.nan


class PositionNode(Node):
    def __init__(self):
        super().__init__("mana_position")
        p = {
            name: self.declare_parameter(name, default).value
            for name, default in (
                ("out_m", 15.0),
                ("out_s", 1.5),
                ("back_m", 8.0),
                ("back_s", 5.0),
                ("max_age_s", 1.5),  # GPS fixes come at 1 to 10 Hz
                ("unknown_error_m", 5.0),  # for a fix that gives no covariance
                ("map_frame", "map"),
            )
        }
        self.max_age_s, self.unknown_error_m, self.map_frame = (
            p["max_age_s"],
            p["unknown_error_m"],
            p["map_frame"],
        )
        self.selector = PositionSelector(p["out_m"], p["out_s"], p["back_m"], p["back_s"])
        self.fix: NavSatFix | None = None
        self.origin: tuple[float, float] | None = None
        self.lidar: Odometry | None = None
        self.odometry: Odometry | None = None
        self.create_subscription(NavSatFix, "gps/fix", self.on_fix, 10)
        self.create_subscription(Odometry, "lidar/odometry", self.on_lidar, 10)
        self.create_subscription(Odometry, "odometry/filtered", self.on_odometry, 10)
        self.report = self.create_publisher(PositionReport, "position", 10)
        self.create_timer(0.1, self.tick)

    def on_fix(self, m: NavSatFix) -> None:
        self.fix = m
        if self.origin is None and gps_error_m(m, self.unknown_error_m) is not None:
            self.origin = (m.latitude, m.longitude)

    def on_lidar(self, m: Odometry) -> None:
        self.lidar = m

    def on_odometry(self, m: Odometry) -> None:
        self.odometry = m

    def fresh(self, msg, now: float) -> bool:
        return msg is not None and now - Time.from_msg(msg.header.stamp).nanoseconds / 1e9 <= (
            self.max_age_s
        )

    def tick(self) -> None:
        stamp = self.get_clock().now()
        now = stamp.nanoseconds / 1e9
        fix = self.fix if self.fresh(self.fix, now) else None
        error = gps_error_m(fix, self.unknown_error_m) if fix else None
        before = self.selector.source
        source = self.selector.update(error, self.fresh(self.lidar, now), now)
        if source != before:
            self.get_logger().info(f"position source: {NAMES[before]} -> {NAMES[source]}")

        out = PositionReport(source=int(source), trust=TRUST[source])
        if source == Source.GPS and error is not None and self.origin is not None:
            lat0, lon0 = self.origin
            out.position.x = (
                math.radians(fix.longitude - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
            )
            out.position.y = math.radians(fix.latitude - lat0) * EARTH_RADIUS_M
            out.error_m = error
        else:
            odom = self.lidar if source == Source.LIDAR_MAP else self.odometry
            if source == Source.GPS or not self.fresh(odom, now):
                return  # nothing recent from the chosen source
            out.position = odom.pose.pose.position
            out.error_m = odometry_error_m(odom)
        out.header.stamp = stamp.to_msg()
        out.header.frame_id = self.map_frame
        self.report.publish(out)


def main(args=None):
    rclpy.init(args=args)
    node = PositionNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
