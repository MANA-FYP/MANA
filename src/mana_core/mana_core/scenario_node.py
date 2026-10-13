"""Live demo: plays one scripted scenario against the core node over ROS 2 topics, in real time.

It publishes what the sensors would report and the user's spoken commands, drives the chair as
the core's drive requests say, prints each decision and message as it happens, and ends with the
scenario's pass marks. Exits with 1 if any of them fails.

    ros2 launch mana_core demo.launch.py scenario:=stairs
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from mana_core import decision as d
from mana_core.scenarios import SCENARIOS, Message, Sample, Trace, check
from mana_core.world import World
from mana_msgs.msg import (
    DriveRequest,
    GroundReport,
    MotionReport,
    ObstacleReport,
    UserCommand,
    UserMessage,
)


class ScenarioNode(Node):
    def __init__(self, **kwargs):
        super().__init__("mana_scenario", **kwargs)
        name = self.declare_parameter("scenario", "stairs").value
        if name not in SCENARIOS:
            raise ValueError(f"unknown scenario {name}; choose from: {', '.join(SCENARIOS)}")
        self.scenario = SCENARIOS[name]
        self.world = World(self.scenario.route)
        self.trace = Trace()
        self.says = sorted(self.scenario.says, key=lambda s: s.at_s)
        self.drive: DriveRequest | None = None
        self.start = None  # the scenario clock starts once the core is listening and deciding
        self.last_t = 0.0
        self.next_line = 0.0
        self.failed: int | None = None  # set when the scenario is over
        self.pub = {
            GroundReport: self.create_publisher(GroundReport, "ground", 10),
            ObstacleReport: self.create_publisher(ObstacleReport, "obstacle", 10),
            MotionReport: self.create_publisher(MotionReport, "motion", 10),
            UserCommand: self.create_publisher(UserCommand, "user_command", 10),
        }
        self.create_subscription(DriveRequest, "drive_request", self.on_drive, 10)
        self.create_subscription(UserMessage, "user_message", self.on_message, 10)
        self.create_timer(0.1, self.step)
        print(f"\n{self.scenario.title}: {self.scenario.shows}\n", flush=True)

    def elapsed(self) -> float:
        return (self.get_clock().now() - self.start).nanoseconds / 1e9

    def on_drive(self, m: DriveRequest) -> None:
        self.drive = m

    def on_message(self, m: UserMessage) -> None:
        if self.start is None or self.failed is not None:
            return
        t = self.elapsed()
        self.trace.messages.append(Message(t, m.text, m.speak))
        print(f"{t:6.1f} s   {'says ' if m.speak else 'shows'}  {m.text}", flush=True)

    def send(self, now) -> None:
        stamp = now.to_msg()
        for r in self.world.sense(0.0):  # the ROS stamp below is what the core reads
            if isinstance(r, d.GroundReport):
                m = GroundReport(trust=r.trust, source=int(r.source), ground=int(r.ground))
                m.distance_m, m.step_height_m = r.distance_m, r.step_height_m
            elif isinstance(r, d.ObstacleReport):
                m = ObstacleReport(trust=r.trust, source=int(r.source), label=r.label)
                m.distance_m = r.distance_m
            else:
                m = MotionReport(trust=r.trust, pitch_rad=r.pitch_rad, roll_rad=r.roll_rad)
                m.wheel_slip = r.wheel_slip
            m.header.stamp = stamp
            self.pub[type(m)].publish(m)

    def step(self) -> None:
        if self.failed is not None:
            return
        now = self.get_clock().now()
        if self.start is None:
            self.send(now)
            if self.drive is not None and self.pub[UserCommand].get_subscription_count() > 0:
                self.start = now
            return
        t, dr = self.elapsed(), self.drive
        mode = d.Mode(dr.mode)
        self.world.move(mode, dr.stop, dr.speed_limit, t - self.last_t)
        self.last_t = t
        self.trace.samples.append(Sample(t, self.world.x, mode, dr.stop, dr.speed_limit, dr.reason))
        if t >= self.next_line:
            speed = "stop" if dr.stop else f"{dr.speed_limit:.0%}"
            print(
                f"{t:6.1f} s   at {self.world.x:5.2f} m   {mode.name.lower():6}  {speed}",
                flush=True,
            )
            self.next_line += 1.0
        if self.world.finished or t >= self.scenario.duration_s:
            return self.finish()
        self.send(now)
        while self.says and self.says[0].at_s <= t:
            say = self.says.pop(0)
            cmd = UserCommand(command=int(say.command), source=UserCommand.VOICE, trust=say.trust)
            cmd.header.stamp = now.to_msg()
            self.pub[UserCommand].publish(cmd)
            self.trace.said.append((t, say.command))
            print(f"{t:6.1f} s   user   {say.command.name.lower().replace('_', ' ')}", flush=True)

    def finish(self) -> None:
        self.trace.end_m, self.trace.mishaps = self.world.x, self.world.mishaps
        self.failed = 0
        print("\nPass marks:", flush=True)
        for mark, ok, measured in check(self.scenario, self.trace):
            self.failed += not ok
            print(f"  {'PASS' if ok else 'FAIL'}  {mark.text:<70} {measured}", flush=True)


def main(args=None):
    rclpy.init(args=args)
    try:
        node = ScenarioNode()
    except ValueError as e:
        print(e, flush=True)
        rclpy.try_shutdown()
        return 2
    try:
        while rclpy.ok() and node.failed is None:
            rclpy.spin_once(node, timeout_sec=0.1)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
    return 1 if node.failed else 0
