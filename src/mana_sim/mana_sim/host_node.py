"""ROS 2 node: makes the simulated chair follow the core's drive request.

Subscribes to drive_request (mana_msgs/DriveRequest) and publishes the commands of host.Host to
the simulated chair. The launch file bridges these topics to Gazebo. If no drive request arrives for
watchdog_s seconds the chair stops: a host must never keep driving on an old request.
"""

import rclpy
from geometry_msgs.msg import Twist, Vector3
from mana_msgs.msg import DriveRequest
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Float64

from mana_sim.host import TRACKS, WATCHDOG_S, WHEELS, Host, HostCommand

CHAIR = "/model/mana_chair"


class HostNode(Node):
    def __init__(self):
        super().__init__("mana_host")
        self.watchdog_s = self.declare_parameter("watchdog_s", WATCHDOG_S).value
        self.host = Host()
        self.request: tuple[int, bool, float] | None = None
        self.received_at = 0.0
        self.create_subscription(DriveRequest, "drive_request", self.on_request, 10)
        self.wheels = self.create_publisher(Twist, f"{CHAIR}/wheels_cmd_vel", 10)
        self.tracks = self.create_publisher(Twist, f"{CHAIR}/tracks_cmd_vel", 10)
        self.wheel_lift = self.create_publisher(Float64, f"{CHAIR}/joint/wheel_lift/cmd_pos", 10)
        self.track_lift = [
            self.create_publisher(Float64, f"{CHAIR}/joint/track_lift_{side}/cmd_pos", 10)
            for side in ("left", "right")
        ]
        self.create_timer(0.05, self.tick)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_request(self, m: DriveRequest) -> None:
        if int(m.mode) not in (WHEELS, TRACKS):
            self.get_logger().warning(
                f"ignored a drive request with mode {int(m.mode)}: "
                "the host has wheels (0) and tracks (1)",
                throttle_duration_sec=5.0,
            )
        self.request = (int(m.mode), bool(m.stop), float(m.speed_limit))
        self.received_at = self.now()

    def tick(self) -> None:
        now = self.now()  # the simulation clock (use_sim_time): the slides move on it
        self.send(self.host.follow(self.request, self.received_at, now, self.watchdog_s))

    def send(self, c: HostCommand) -> None:
        self.wheel_lift.publish(Float64(data=c.wheel_lift))
        for pub in self.track_lift:
            pub.publish(Float64(data=c.track_lift))
        self.wheels.publish(Twist(linear=Vector3(x=c.wheels_mps)))
        self.tracks.publish(Twist(linear=Vector3(x=c.tracks_mps)))


def main(args=None):
    rclpy.init(args=args)
    node = HostNode()
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
