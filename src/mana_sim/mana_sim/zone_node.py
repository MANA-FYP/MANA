"""ROS 2 node: switches the faults of the place the chair is in (#79), on the campus world.

    ros2 run mana_sim zone_node --ros-args -p use_sim_time:=true

Follows the chair's true position (/model/mana_chair/ground_truth) and, on entering or leaving a
zone of mana_sim.campus, calls fault_node's set_fault: GPS lost indoors, the dark room dark, Wi-Fi
fading in the hall (mana_sim.zones). Run it beside world.launch.py with the campus world.
"""

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node

from mana_msgs.srv import SetFault
from mana_sim.campus import build
from mana_sim.zones import ZoneWatch


class ZoneNode(Node):
    def __init__(self) -> None:
        super().__init__("zone_node")
        self.watch = ZoneWatch(build().zones)  # the zones do not change with the seed
        self.client = self.create_client(SetFault, "set_fault")
        self.waiting: list = []  # changes not yet sent: the fault node may start after this one
        self.create_subscription(Odometry, "/model/mana_chair/ground_truth", self.on_pose, 10)
        self.create_timer(0.5, self.send)

    def on_pose(self, m: Odometry) -> None:
        self.waiting += self.watch.update(m.pose.pose.position.x, m.pose.pose.position.y)
        self.send()

    def send(self) -> None:
        if not self.waiting or not self.client.service_is_ready():
            return
        for sensor, kind, on, value, zone in self.waiting:
            req = SetFault.Request(sensor=sensor, kind=kind, active=on, value=value)
            self.client.call_async(req)
            self.get_logger().info(f"{'into' if on else 'out of'} {zone}: {sensor} {kind} {on}")
        self.waiting = []


def main() -> None:
    rclpy.init()
    node = ZoneNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
