"""ROS 2 program: plays one fault scenario on the running simulation.

    ros2 run mana_sim fault_script wheel_slip --ros-args -p use_sim_time:=true

Calls set_fault at the times of the scenario (simulation seconds from the start), then ends. With no
name it lists the scenarios.
"""

import sys

import rclpy
from rclpy.node import Node

from mana_msgs.srv import SetFault
from mana_sim.fault_scenarios import FAULT_SCENARIOS, due


class FaultScript(Node):
    def __init__(self, name: str) -> None:
        super().__init__("fault_script")
        self.scenario = FAULT_SCENARIOS[name]
        self.client = self.create_client(SetFault, "set_fault")
        self.done = 0
        self.start_s: float | None = None
        self.get_logger().info(f"{name}: {self.scenario.summary} Expect: {self.scenario.expect}")
        self.create_timer(0.1, self.tick)

    def tick(self) -> None:
        now = self.get_clock().now().nanoseconds / 1e9
        if now <= 0.0 or not self.client.service_is_ready():
            return  # the clock has not started, or the fault node is not there yet
        if self.start_s is None:
            self.start_s = now
        for step in due(self.scenario.steps, now - self.start_s, self.done):
            req = SetFault.Request(
                sensor=step.sensor, kind=step.kind, active=step.active, value=step.value
            )
            self.client.call_async(req)
            self.done += 1
            state = "on" if step.active else "off"
            self.get_logger().info(
                f"at {now - self.start_s:.1f} s: {step.sensor} {step.kind} {state}"
            )
        if self.done == len(self.scenario.steps):
            raise SystemExit


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in FAULT_SCENARIOS:
        print("choose a scenario:")
        for s in FAULT_SCENARIOS.values():
            print(f"  {s.name:14} {s.summary}")
        sys.exit(2)
    rclpy.init(args=sys.argv[2:])
    node = FaultScript(sys.argv[1])
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
