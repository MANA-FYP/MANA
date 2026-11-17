"""ROS 2 node between slam_toolbox and the rest: the lidar map pose with slam_toolbox's jumps taken
out, the wheels being the check (mana_position.lidar_gate, #43).

    ros2 run mana_position lidar_gate --ros-args -r pose:=/pose -r odom:=/odom

In: `pose` (PoseWithCovarianceStamped, slam_toolbox) and `odom` (Odometry, the wheels).
Out: `lidar/pose`, which the position node and ekf_global read.
"""

import math

import rclpy
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from mana_position.lidar_gate import JumpFilter


def heading(q) -> float:
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


def pose_of(p) -> tuple[float, float, float]:
    return p.position.x, p.position.y, heading(p.orientation)


class LidarGateNode(Node):
    def __init__(self):
        super().__init__("lidar_gate")
        jump = self.declare_parameter("jump_m", 0.5).value
        fade = self.declare_parameter("fade_s", 60.0).value
        self.filter = JumpFilter(jump_m=float(jump), fade_s=float(fade))
        self.out = self.create_publisher(PoseWithCovarianceStamped, "lidar/pose", 10)
        self.create_subscription(Odometry, "odom", self.on_odom, 50)
        self.create_subscription(PoseWithCovarianceStamped, "pose", self.on_pose, 10)

    def on_odom(self, m: Odometry) -> None:
        self.filter.on_odom(pose_of(m.pose.pose))

    def on_pose(self, m: PoseWithCovarianceStamped) -> None:
        t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
        jumps = self.filter.jumps
        x, y, _ = self.filter.correct(t, pose_of(m.pose.pose))
        if self.filter.jumps > jumps:
            off = self.filter.offset
            self.get_logger().warn(f"lidar jump taken out: offset ({off[0]:+.2f}, {off[1]:+.2f}) m")
        m.pose.pose.position.x, m.pose.pose.position.y = x, y
        self.out.publish(m)


def main(args=None):
    rclpy.init(args=args)
    node = LidarGateNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
