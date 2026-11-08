"""How long MANA takes to stop for a drop, stage by stage, on the real ROS 2 nodes (#84).

    python3 tools/measure_latency.py [--trials N]   # in a sourced workspace, ROS 2 Jazzy

Starts depth_node, core_node and host_node, and sends them 30 depth frames a second, as a 3D
camera would: level ground, then, at a random moment, a drop in the blind zone (read as DROP at
0 m, which stops the chair from one sensor). Each trial times, by the clock of this probe:

    frame   the first frame with the drop is published
    ground  depth_node's first DROP report arrives
    drive   core_node's first drive request with stop arrives
    wheels  host_node's first wheel command of 0 m/s arrives

Then level ground again until the chair drives on, and the next trial. Gazebo is not needed: the
host's wheel command is where the simulated or real chair starts to brake.
"""

import os
import random
import signal
import statistics
import subprocess
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node
from sensor_msgs.msg import CameraInfo, Image

from mana_msgs.msg import DriveRequest, GroundReport, MotionReport
from mana_perception import depth as dp
from mana_perception import synthetic as syn

FRAME_HZ = 30.0
CAM = syn.CAMERA  # depth_node's default mount: 1.0 m up, 0.6 rad down
NODES = [
    ["mana_perception", "depth_node"],
    ["mana_core", "core_node"],
    ["mana_sim", "host_node"],
]


def frame(metres) -> Image:
    m = metres.astype("<f4")
    image = Image(height=m.shape[0], width=m.shape[1], encoding="32FC1", is_bigendian=0)
    image.step = m.shape[1] * 4
    image.data = m.tobytes()
    return image


class Probe(Node):
    def __init__(self, trials: int):
        super().__init__("latency_probe")
        self.trials, self.rng, self.results = trials, random.Random(84), []
        self.info = CameraInfo(width=syn.COLS, height=syn.ROWS)
        self.info.k = [CAM.fx, 0.0, CAM.cx, 0.0, CAM.fy, CAM.cy, 0.0, 0.0, 1.0]
        self.flat = frame(syn.render(syn.flat, CAM))
        self.drop = frame(syn.render(lambda x: (x > 0.4) * -1.0, CAM))  # an edge in the blind zone
        assert dp.read_ground(syn.render(lambda x: (x > 0.4) * -1.0, CAM), CAM).ground == dp.DROP
        self.info_pub = self.create_publisher(CameraInfo, "depth/camera_info", 10)
        self.image_pub = self.create_publisher(Image, "depth/image", 10)
        self.motion_pub = self.create_publisher(MotionReport, "motion", 10)
        self.create_subscription(GroundReport, "ground", self.on_ground, 10)
        self.create_subscription(DriveRequest, "drive_request", self.on_drive, 10)
        self.create_subscription(Twist, "/model/mana_chair/wheels_cmd_vel", self.on_wheels, 10)
        self.state, self.t = "settle", {}
        self.settle_until = time.monotonic() + 6.0  # the nodes start, the host's slides settle
        self.drop_at = None
        self.create_timer(1.0 / FRAME_HZ, self.tick)

    def tick(self) -> None:
        now = time.monotonic()
        self.info_pub.publish(self.info)
        self.motion_pub.publish(MotionReport(trust=0.9))
        if self.state == "settle" and now >= self.settle_until and self.t.get("driving"):
            self.state, self.t = "wait", {}
            self.drop_at = now + self.rng.uniform(0.5, 1.5)  # a random phase against the timers
        dropping = self.state == "wait" and now >= self.drop_at
        if dropping and "frame" not in self.t:
            self.t["frame"] = now
        image = self.drop if dropping else self.flat
        image.header.stamp = self.get_clock().now().to_msg()
        self.image_pub.publish(image)
        if dropping and "wheels" in self.t:
            self.results.append(dict(self.t))
            self.state, self.settle_until, self.t = "settle", now + 1.5, {}

    def on_ground(self, m: GroundReport) -> None:
        if "frame" in self.t and "ground" not in self.t and m.ground == GroundReport.DROP:
            self.t["ground"] = time.monotonic()

    def on_drive(self, m: DriveRequest) -> None:
        if "ground" in self.t and "drive" not in self.t and m.stop:
            self.t["drive"] = time.monotonic()

    def on_wheels(self, m: Twist) -> None:
        if self.state == "settle":
            self.t["driving"] = m.linear.x > 0.0
        elif "drive" in self.t and "wheels" not in self.t and m.linear.x == 0.0:
            self.t["wheels"] = time.monotonic()


def summary(values: list[float]) -> str:
    ms = sorted(v * 1000.0 for v in values)
    p95 = ms[min(len(ms) - 1, round(0.95 * (len(ms) - 1)))]
    return f"{statistics.median(ms):6.0f} {p95:6.0f} {ms[-1]:6.0f}"


def signal_group(p: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass  # the whole group has exited already


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    trials = int(args[args.index("--trials") + 1]) if "--trials" in args else 50
    # each in its own process group: `ros2 run` starts the node as a child, and stopping only
    # `ros2 run` leaves the node running
    procs = [subprocess.Popen(["ros2", "run", *n], start_new_session=True) for n in NODES]
    rclpy.init()
    probe = None
    try:
        probe = Probe(trials)
        deadline = time.monotonic() + 30.0 + trials * 4.0
        while len(probe.results) < trials and time.monotonic() < deadline:
            rclpy.spin_once(probe, timeout_sec=0.01)
    finally:
        r = probe.results if probe else []
        if probe:
            probe.destroy_node()
        rclpy.shutdown()
        for p in procs:  # always, so that no node is left running
            signal_group(p, signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            signal_group(p, signal.SIGKILL)  # what is left: `ros2 run` can exit before its node
    if not r:
        print("no trial finished: is the workspace sourced and built?")
        return 1
    print(f"{len(r)} trials, {FRAME_HZ:.0f} frames per second; ms: median, 95th percentile, most")
    for name, a, b in (
        ("frame to DROP report (depth_node)", "frame", "ground"),
        ("DROP report to stop request (core_node)", "ground", "drive"),
        ("stop request to wheels at 0 (host_node)", "drive", "wheels"),
        ("frame to wheels at 0", "frame", "wheels"),
    ):
        print(f"  {name:42} {summary([t[b] - t[a] for t in r])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
