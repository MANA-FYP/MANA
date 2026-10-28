"""ROS 2 node: the simulated sensors, as the frames and the reports the rest of MANA reads.

- Transforms: map > odom (identity), odom > base_link (from the ground truth of the simulation) and
  base_link > each sensor frame (frames.SENSORS). Also publishes `odom` (nav_msgs/Odometry).
- `motion` from the IMU orientation and the wheel encoders (slip is only judged in wheels mode).
- `obstacle` from the nearest echo of the lidar in front of the chair. (`position` is made by
  mana_position's position_node from `gps/fix`, and `link` by mana_connectivity's link_node from the
  heartbeats of fault_node.)
"""

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Imu, JointState, LaserScan
from tf2_ros import StaticTransformBroadcaster, TransformBroadcaster

from mana_msgs.msg import DriveRequest, MotionReport, ObstacleReport
from mana_sim import converters as conv
from mana_sim.frames import BASE, MAP, ODOM, SENSORS, quaternion
from mana_sim.host import WHEELS

GROUND_TRUTH = "/model/mana_chair/ground_truth"


class SensorsNode(Node):
    def __init__(self) -> None:
        super().__init__("sensors_node")
        p = self.declare_parameter
        self.cone_rad = p("obstacle_cone_rad", 0.35).value
        self.obstacle_max_m = p("obstacle_max_m", 4.0).value
        self.obstacle_trust = p("obstacle_trust", 0.9).value

        self.mode = WHEELS
        self.wheel_rad_s: list[float] = []
        self.speed_mps = 0.0

        self.tf = TransformBroadcaster(self)
        self.static_tf = StaticTransformBroadcaster(self)
        self.send_static()

        self.odom = self.create_publisher(Odometry, "odom", 10)
        self.motion = self.create_publisher(MotionReport, "motion", 10)
        self.obstacle = self.create_publisher(ObstacleReport, "obstacle", 10)

        self.create_subscription(Odometry, GROUND_TRUTH, self.on_ground_truth, 10)
        self.create_subscription(Imu, "imu", self.on_imu, 10)
        self.create_subscription(JointState, "joint_states", self.on_joints, 10)
        self.create_subscription(LaserScan, "scan", self.on_scan, 10)
        self.create_subscription(DriveRequest, "drive_request", self.on_request, 10)

    def send_static(self) -> None:
        now = self.get_clock().now().to_msg()
        out = [self.transform(now, MAP, ODOM, (0.0,) * 6)]
        out += [self.transform(now, BASE, name, pose) for name, pose in SENSORS.items()]
        self.static_tf.sendTransform(out)

    @staticmethod
    def transform(stamp, parent: str, child: str, pose) -> TransformStamped:
        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = stamp, parent, child
        t.transform.translation.x, t.transform.translation.y, t.transform.translation.z = pose[:3]
        r = t.transform.rotation
        r.x, r.y, r.z, r.w = quaternion(*pose[3:])
        return t

    def on_request(self, m: DriveRequest) -> None:
        self.mode = int(m.mode)

    def on_ground_truth(self, m: Odometry) -> None:
        self.speed_mps = m.twist.twist.linear.x
        t = TransformStamped()
        t.header.stamp, t.header.frame_id, t.child_frame_id = m.header.stamp, ODOM, BASE
        t.transform.translation.x = m.pose.pose.position.x
        t.transform.translation.y = m.pose.pose.position.y
        t.transform.translation.z = m.pose.pose.position.z
        t.transform.rotation = m.pose.pose.orientation
        self.tf.sendTransform(t)
        m.header.frame_id, m.child_frame_id = ODOM, BASE
        self.odom.publish(m)

    def on_joints(self, m: JointState) -> None:
        self.wheel_rad_s = [
            v for n, v in zip(m.name, m.velocity, strict=False) if n.startswith("wheel_")
        ]

    def on_imu(self, m: Imu) -> None:
        q = m.orientation
        pitch, roll = conv.tilt(q.x, q.y, q.z, q.w)
        slip = self.mode == WHEELS and conv.wheel_slip(self.wheel_rad_s, self.speed_mps)
        r = MotionReport()
        r.header.stamp, r.header.frame_id = m.header.stamp, BASE
        r.trust, r.pitch_rad, r.roll_rad, r.wheel_slip = 1.0, pitch, roll, slip
        self.motion.publish(r)

    def on_scan(self, m: LaserScan) -> None:
        found = conv.nearest_ahead(
            list(m.ranges),
            m.angle_min,
            m.angle_increment,
            self.cone_rad,
            self.obstacle_max_m,
            m.range_min,
        )
        if found is None:
            return
        r = ObstacleReport()
        r.header.stamp, r.header.frame_id = m.header.stamp, m.header.frame_id
        r.trust, r.source = self.obstacle_trust, ObstacleReport.LIDAR
        r.distance_m, r.bearing_rad = found
        self.obstacle.publish(r)


def main() -> None:
    rclpy.init()
    node = SensorsNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, rclpy.executors.ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
