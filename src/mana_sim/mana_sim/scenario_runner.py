"""ROS 2 program: plays one scenario on the running simulation and records what happens.

    ros2 run mana_sim scenario_runner stairs --out stairs.json --ready ready \
        --ros-args -p use_sim_time:=true

The simulation (world.launch.py with the world of the run) must be running. The runner waits for
the chair to settle after it is put into the world, says so by writing the --ready file, and the
core is started only then; the run begins with the core's first drive request, as the scripted
runs begin with the first tick. The runner is the user and the test bench for the length of the run:
- it says the commands of the scenario (UserCommand) at their times,
- it switches the faults of the scenario (SetFault) at their times or places,
- it stands in for the camera's ground classifier (GroundReport, source CAMERA), because the real
  one is still to be trained, with the trust falling with the light of the camera image,
- it records the chair's place (ground truth), what the core asks of the host (DriveRequest) and
  what the user hears (UserMessage) as a mana_core Trace, and writes it as JSON.

The run ends at the end of the route, at a mishap, or at the time limit. See gazebo_scenarios.py.
"""

import argparse
import sys

import rclpy
from mana_core.decision import Mode
from mana_core.messages import Message as Heard
from mana_core.scenarios import Message, Sample
from nav_msgs.msg import Odometry
from rclpy.node import Node
from sensor_msgs.msg import Image

from mana_msgs.msg import DriveRequest, GroundReport, UserCommand, UserMessage
from mana_msgs.srv import SetFault
from mana_sim import gazebo_scenarios as gs

TICK_S = 0.1  # as the core node and the scripted evaluator
READY_TIMEOUT_S = 90.0
SETTLE_AFTER_S = 5.0  # simulation time before the chair is looked at
SETTLED_M = 0.002  # the chair has not moved this much in the last second


class ScenarioRunner(Node):
    def __init__(self, run: gs.GazeboRun, out: str, ready: str) -> None:
        super().__init__("scenario_runner")
        self.run_, self.out, self.ready_file = run, out, ready
        self.settling: list[tuple[float, float, float]] = []  # (time, x, z) of the last second
        self.ready_at: float | None = None
        self.rec = gs.Recording()
        self.says = sorted(run.says, key=lambda s: s.at_s)
        self.faults = list(run.faults)
        self.x: float | None = None  # metres from the start, from the ground truth
        self.z = 0.38
        self.request: DriveRequest | None = None
        self.mean_pixel = gs.LIT_MEAN
        self.t0: float | None = None
        self.started_wall = self.now_s()
        self.finished = False

        self.create_subscription(Odometry, "/model/mana_chair/ground_truth", self.on_truth, 10)
        self.create_subscription(DriveRequest, "drive_request", self.on_request, 10)
        self.create_subscription(UserMessage, "user_message", self.on_message, 10)
        self.create_subscription(Image, "camera/image", self.on_image, 1)
        self.ground = self.create_publisher(GroundReport, "ground", 10)
        self.command = self.create_publisher(UserCommand, "user_command", 10)
        self.set_fault = self.create_client(SetFault, "set_fault")
        self.create_timer(TICK_S, self.tick)

    def now_s(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_truth(self, m: Odometry) -> None:
        self.x = m.pose.pose.position.x - self.run_.start_x
        self.z = m.pose.pose.position.z
        self.settling.append((self.now_s(), self.x, self.z))
        self.settling = [s for s in self.settling if s[0] > self.now_s() - 1.0]

    def on_request(self, m: DriveRequest) -> None:
        self.request = m

    def on_image(self, m: Image) -> None:
        data = bytes(m.data[:: max(1, len(m.data) // 3000)])
        if data:
            self.mean_pixel = sum(data) / len(data)

    def on_message(self, m: UserMessage) -> None:
        if self.t0 is None:
            return
        t = self.now_s() - self.t0
        self.rec.trace.messages.append(Message(t, m.text, m.speak))
        self.rec.trace.heard.append((t, Heard(m.text, m.priority, m.speak, t)))

    def settled(self) -> bool:
        """The simulation has run a while and the chair has stopped moving about."""
        if self.x is None or self.now_s() < SETTLE_AFTER_S or len(self.settling) < 5:
            return False
        xs = [s[1] for s in self.settling]
        zs = [s[2] for s in self.settling]
        return max(xs) - min(xs) < SETTLED_M and max(zs) - min(zs) < SETTLED_M

    def tick(self) -> None:
        if self.finished:
            return
        now = self.now_s()
        if self.x is not None:
            self.report_ground(self.x)  # the core finds the camera's reports waiting
        if self.ready_at is None:
            if self.settled():
                self.ready_at = now
                open(self.ready_file, "w").write("ready\n")  # now the core can be started
            elif now - self.started_wall > READY_TIMEOUT_S:
                self.finish("the chair did not settle")
            return
        if self.t0 is None:
            if self.request is not None:
                self.t0 = now  # the run begins with the core's first drive request
            elif now - self.ready_at > READY_TIMEOUT_S:
                self.finish("the core did not answer")
            return
        t = now - self.t0
        x, r = self.x, self.request
        while self.says and self.says[0].at_s <= t:
            say = self.says.pop(0)
            self.command.publish(
                UserCommand(source=UserCommand.BUTTON, command=int(say.command), trust=say.trust)
            )
            self.rec.trace.said.append((t, say.command))
        self.switch_faults(t, x)
        mode = Mode(int(r.mode))
        speed = round(float(r.speed_limit), 4)  # a float32 0.3 is not 0.3
        self.rec.trace.samples.append(Sample(t, x, mode, r.stop, speed, r.reason))
        self.rec.add_mishaps(gs.mishaps_at(x, self.z, mode, self.run_.start_x, self.run_.boxes))
        self.rec.trace.end_m = x
        if self.rec.trace.mishaps:
            self.finish("mishap: " + self.rec.trace.mishaps[0])
        elif x >= self.run_.length_m:
            self.finish("reached the end of the route")
        elif t >= self.run_.duration_s:
            self.finish("time limit")

    def report_ground(self, x: float) -> None:
        g = GroundReport(
            trust=gs.camera_trust(self.mean_pixel),
            source=GroundReport.CAMERA,
            ground=int(gs.camera_ground(self.run_.camera, x)),
            distance_m=float("nan"),
            step_height_m=float("nan"),
        )
        g.header.stamp = self.get_clock().now().to_msg()
        self.ground.publish(g)

    def switch_faults(self, t: float, x: float) -> None:
        due = [
            f
            for f in self.faults
            if (f.at_s is not None and t >= f.at_s) or (f.at_x is not None and x >= f.at_x)
        ]
        for f in due:
            if not self.set_fault.service_is_ready():
                return  # try again at the next tick
            self.faults.remove(f)
            self.set_fault.call_async(
                SetFault.Request(sensor=f.sensor, kind=f.kind, active=f.active, value=f.value)
            )
            self.get_logger().info(f"at {t:.1f} s, {x:.1f} m: {f.sensor} {f.kind} {f.active}")

    def finish(self, why: str) -> None:
        self.finished = True
        self.get_logger().info(f"run over: {why}")
        with open(self.out, "w") as f:
            f.write(gs.trace_to_json(self.rec.trace))
        raise SystemExit


def main() -> None:
    parser = argparse.ArgumentParser(prog="scenario_runner")
    parser.add_argument("scenario", choices=sorted(gs.RUNS))
    parser.add_argument("--out", required=True, help="where to write the trace (JSON)")
    parser.add_argument("--ready", required=True, help="a file to write when the chair has settled")
    ours, ros_args = parser.parse_known_args(sys.argv[1:])
    rclpy.init(args=ros_args)
    node = ScenarioRunner(gs.RUNS[ours.scenario], ours.out, ours.ready)
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
