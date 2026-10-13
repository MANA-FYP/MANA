"""ROS 2 node for the decision core.

Listens to the module reports, decides at 10 Hz, and publishes a drive request for the host every
tick and a message for the user whenever the reason for the decision changes. The thresholds in
Limits are ROS parameters with the same names.
"""

from dataclasses import fields

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.time import Time

from mana_core import decision as d
from mana_msgs.msg import (
    DriveRequest,
    GroundReport,
    MotionReport,
    ObstacleReport,
    UserCommand,
    UserMessage,
)


class CoreNode(Node):
    def __init__(self):
        super().__init__("mana_core")
        limits = d.Limits(
            **{f.name: self.declare_parameter(f.name, f.default).value for f in fields(d.Limits)}
        )
        self.core = d.DecisionCore(limits)
        self.last_reason = None
        self.create_subscription(GroundReport, "ground", self.on_ground, 10)
        self.create_subscription(ObstacleReport, "obstacle", self.on_obstacle, 10)
        self.create_subscription(MotionReport, "motion", self.on_motion, 10)
        self.create_subscription(UserCommand, "user_command", self.on_command, 10)
        self.drive = self.create_publisher(DriveRequest, "drive_request", 10)
        self.message = self.create_publisher(UserMessage, "user_message", 10)
        self.create_timer(0.1, self.tick)

    @staticmethod
    def measured(msg) -> float:
        return Time.from_msg(msg.header.stamp).nanoseconds / 1e9

    def ignore(self, kind, msg) -> None:
        self.get_logger().warning(f"ignored a {kind} with an unknown code: {msg}")

    def on_ground(self, m: GroundReport) -> None:
        try:
            report = d.GroundReport(
                self.measured(m),
                m.trust,
                d.Source(m.source),
                d.Ground(m.ground),
                m.distance_m,
                m.step_height_m,
            )
        except ValueError:
            return self.ignore("ground report", m)
        self.core.on_ground(report)

    def on_obstacle(self, m: ObstacleReport) -> None:
        try:
            report = d.ObstacleReport(
                self.measured(m), m.trust, d.Source(m.source), m.distance_m, m.label
            )
        except ValueError:
            return self.ignore("obstacle report", m)
        self.core.on_obstacle(report)

    def on_motion(self, m: MotionReport) -> None:
        self.core.on_motion(
            d.MotionReport(self.measured(m), m.trust, m.pitch_rad, m.roll_rad, m.wheel_slip)
        )

    def on_command(self, m: UserCommand) -> None:
        try:
            command = d.Command(m.command)
        except ValueError:
            return self.ignore("user command", m)
        self.core.on_command(command, m.trust)

    def tick(self) -> None:
        now = self.get_clock().now()
        dec = self.core.tick(now.nanoseconds / 1e9)
        out = DriveRequest(
            mode=int(dec.mode), stop=dec.stop, speed_limit=dec.speed_limit, reason=dec.reason
        )
        out.header.stamp = now.to_msg()
        self.drive.publish(out)
        if dec.reason != self.last_reason:
            msg = UserMessage(
                priority=int(dec.priority),
                text=dec.reason,
                speak=dec.priority >= d.Priority.WARNING,
            )
            msg.header.stamp = out.header.stamp
            self.message.publish(msg)
            self.last_reason = dec.reason


def main(args=None):
    rclpy.init(args=args)
    node = CoreNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
