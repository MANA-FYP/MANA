"""Drive the indoor world's corridor loop and log the position sources against ground truth (#43).

    ros2 run mana_sim loop_driver --laps 2 --csv drive.csv

A stand-in for a path planner: it steers by the chair's ground-truth pose, as the autopilot (through
driver_select, so MANA's drive request still caps it), and asks the host for normal driving. Once a
second it writes the ground truth, the map > base_link pose and the odom > base_link pose (NaN while
a frame is missing), the poses looked up at the ground truth's own time; mana_sim.localisation
compares them from the first row, which it writes before the chair moves, where map and odom
begin. Ends when the route is done or after --timeout seconds of wall time, and prints the errors.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
import time
from collections import deque

import rclpy
from geometry_msgs.msg import Twist, Vector3
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.time import Time
from tf2_ros import Buffer, TransformListener

from mana_msgs.msg import DriveRequest
from mana_sim.localisation import COLUMNS, INDOOR_LOOP, driven_m, errors, steer


def yaw_of(q) -> float:
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class LoopDriver(Node):
    def __init__(self, laps: int, out: str):
        super().__init__("loop_driver", parameter_overrides=[Parameter("use_sim_time", value=True)])
        self.route = INDOOR_LOOP * laps
        self.wp, self.gt, self.done = 0, None, False
        self.history: deque = deque(maxlen=200)  # (time, ground-truth pose), newest last
        self.rows: list[dict] = []
        self.file = open(out, "w", newline="")
        self.log = csv.DictWriter(self.file, COLUMNS)
        self.log.writeheader()
        self.req = self.create_publisher(DriveRequest, "drive_request", 10)
        self.cmd = self.create_publisher(Twist, "cmd_vel/autopilot", 10)
        self.create_subscription(Odometry, "/model/mana_chair/ground_truth", self.on_gt, 10)
        self.tf = Buffer()
        self.listener = TransformListener(self.tf, self)
        self.create_timer(0.05, self.step)
        self.create_timer(1.0, self.record)

    def on_gt(self, m: Odometry) -> None:
        p = m.pose.pose.position
        self.gt = (p.x, p.y, yaw_of(m.pose.pose.orientation))
        self.history.append((Time.from_msg(m.header.stamp), self.gt))
        if not self.rows:
            self.record()  # the first row before the chair moves

    def pose(self, parent: str, at: Time) -> tuple[float, float, float]:
        try:
            t = self.tf.lookup_transform(parent, "base_link", at)
        except Exception:  # the frame is not there (yet)
            return (math.nan,) * 3
        tr = t.transform
        return tr.translation.x, tr.translation.y, yaw_of(tr.rotation)

    def record(self, lag_s: float = 0.25) -> None:
        """One row: a ground-truth pose about lag_s old, so that the transforms have reached that
        time, and map and odom looked up at that time (at 0.4 m/s each 0.1 s apart is 4 cm)."""
        if not self.history:
            return
        newest = self.history[-1][0].nanoseconds
        at, gt = next(
            ((s, g) for s, g in reversed(self.history) if newest - s.nanoseconds >= lag_s * 1e9),
            self.history[0],
        )
        mp, op = self.pose("map", at), self.pose("odom", at)
        if not self.rows and math.isnan(mp[0] + op[0]):
            return  # the first row needs map and odom, or the start is not compared
        row = dict(zip(COLUMNS, (at.nanoseconds / 1e9, *gt, *mp, *op), strict=True))
        self.rows.append(row)
        self.log.writerow({k: round(v, 3) for k, v in row.items()})
        self.file.flush()

    def step(self) -> None:
        r = DriveRequest()
        r.header.stamp = self.get_clock().now().to_msg()
        r.mode, r.stop, r.speed_limit, r.reason = 0, False, 1.0, "loop driver"
        self.req.publish(r)
        if not self.rows or self.done:  # not before the first row is written
            self.cmd.publish(Twist())
            return
        target = self.route[self.wp]
        if math.dist(self.gt[:2], target) < 0.3:
            self.wp += 1
            self.done = self.wp == len(self.route)
            return
        fwd, turn = steer(self.gt, target)
        self.cmd.publish(Twist(linear=Vector3(x=fwd), angular=Vector3(z=turn)))


def main(argv=None) -> int:
    rclpy.init(args=argv)
    p = argparse.ArgumentParser(description="Drive the indoor loop and log the position sources")
    p.add_argument("--laps", type=int, default=1)
    p.add_argument("--csv", default="loop_drive.csv")
    p.add_argument("--timeout", type=float, default=900.0, help="wall-clock seconds")
    a = p.parse_args(rclpy.utilities.remove_ros_args(sys.argv if argv is None else argv)[1:])
    node = LoopDriver(a.laps, a.csv)
    end = time.time() + a.timeout
    while not node.done and time.time() < end:
        rclpy.spin_once(node, timeout_sec=0.05)
    node.cmd.publish(Twist())
    done = f"waypoint {node.wp} of {len(node.route)}; driven {driven_m(node.rows):.1f} m"
    print(done, flush=True)
    for name, x, y in (("map (localiser)", "mx", "my"), ("odom (wheels)", "ox", "oy")):
        e = errors(node.rows, x, y)  # from row 0, written at rest
        print(
            f"{name}: no pose"
            if e is None
            else f"{name}: {e.poses} poses, mean {e.mean_m:.2f} m, 95th {e.p95_m:.2f} m, "
            f"max {e.max_m:.2f} m, last {e.last_m:.2f} m",
            flush=True,
        )
    node.file.close()
    node.destroy_node()
    rclpy.shutdown()
    return 0 if node.done else 1


if __name__ == "__main__":
    sys.exit(main())
