"""ROS 2 node for the perception stub.

Publishes scripted ground and obstacle reports at a fixed rate, on the topics the decision core
subscribes to. Parameters:

  scenario  one of level, ramp, stairs, curb, drop, obstacle, conflict, or "cycle" to play them
            all in turn (default "cycle")
  hold_s    seconds each scenario lasts in "cycle" (default 5.0)
  rate_hz   reports per second (default 10.0)
  trust     trust given to every report, 0.0 to 1.0 (default 0.9)
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from mana_msgs.msg import GroundReport, ObstacleReport
from mana_perception import scenarios as sc

FRAMES = {sc.CAMERA: "camera", sc.DEPTH_CAMERA: "depth_camera", sc.LIDAR: "lidar"}


class StubNode(Node):
    def __init__(self):
        super().__init__("mana_perception_stub")
        self.scenario = self.declare_parameter("scenario", "cycle").value
        self.hold_s = self.declare_parameter("hold_s", 5.0).value
        self.rate_hz = self.declare_parameter("rate_hz", 10.0).value
        self.trust = self.declare_parameter("trust", 0.9).value
        if self.scenario != "cycle":
            sc.reports(self.scenario)  # fails early on an unknown name
        self.ground = self.create_publisher(GroundReport, "ground", 10)
        self.obstacle = self.create_publisher(ObstacleReport, "obstacle", 10)
        self.started = self.get_clock().now()
        self.current = None
        self.create_timer(1.0 / self.rate_hz, self.tick)

    def tick(self) -> None:
        now = self.get_clock().now()
        seconds = (now - self.started).nanoseconds / 1e9
        name = sc.scenario_at(seconds, self.hold_s) if self.scenario == "cycle" else self.scenario
        if name != self.current:
            self.get_logger().info(f"scenario: {name}")
            self.current = name
        grounds, obstacles = sc.reports(name)
        stamp = now.to_msg()
        for g in grounds:
            msg = GroundReport(
                trust=self.trust,
                source=g.source,
                ground=g.ground,
                distance_m=g.distance_m,
                step_height_m=g.step_height_m,
            )
            msg.header.stamp = stamp
            msg.header.frame_id = FRAMES[g.source]
            self.ground.publish(msg)
        for o in obstacles:
            msg = ObstacleReport(
                trust=self.trust,
                source=o.source,
                label=o.label,
                distance_m=o.distance_m,
                bearing_rad=o.bearing_rad,
            )
            msg.header.stamp = stamp
            msg.header.frame_id = FRAMES[o.source]
            self.obstacle.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = StubNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
