"""ROS 2 node: passes the driving driver's command on to the host, and says who is driving.

Subscribes to cmd_vel/supervisor and cmd_vel/autopilot (geometry_msgs/Twist), and to
supervisor/control (std_msgs/Bool: true takes the controls, false hands them back). Publishes
cmd_vel for the host and driver (std_msgs/String: "supervisor" or "autopilot") at 20 Hz. A driver
that goes quiet for watchdog_s gives no motion (driver.DriverSelect).
"""

import rclpy
from geometry_msgs.msg import Twist, Vector3
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool, String

from mana_sim.driver import AUTOPILOT, SUPERVISOR, WATCHDOG_S, DriverSelect


class DriverSelectNode(Node):
    def __init__(self):
        super().__init__("mana_driver_select")
        self.select = DriverSelect(self.declare_parameter("watchdog_s", WATCHDOG_S).value)
        for who in (SUPERVISOR, AUTOPILOT):
            self.create_subscription(
                Twist, f"cmd_vel/{who}", lambda m, who=who: self.on_twist(who, m), 10
            )
        self.create_subscription(
            Bool,
            "supervisor/control",
            self.on_control,
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.cmd_vel = self.create_publisher(Twist, "cmd_vel", 10)
        self.driver = self.create_publisher(String, "driver", 10)
        self.create_timer(0.05, self.tick)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_twist(self, who: str, m: Twist) -> None:
        self.select.command(who, float(m.linear.x), float(m.angular.z), self.now())

    def on_control(self, m: Bool) -> None:
        before = self.select.driver
        self.select.take_over() if m.data else self.select.hand_back()
        if self.select.driver != before:
            self.get_logger().info(f"driver: {before} -> {self.select.driver}")

    def tick(self) -> None:
        forward, turn = self.select.output(self.now())
        self.cmd_vel.publish(Twist(linear=Vector3(x=forward), angular=Vector3(z=turn)))
        self.driver.publish(String(data=self.select.driver))


def main(args=None):
    rclpy.init(args=args)
    node = DriverSelectNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
