"""Send the autopilot (#78) its goals one after the other and write down how each went.

    python3 tools/autopilot_goal.py --out runs/t78/goals.csv          # the indoor goals below
    python3 tools/autopilot_goal.py --goal 13 0 0                  # one goal: x y yaw (map)

For each goal: whether Nav2 reached it, how long it took, how far the chair ended from it by
Gazebo's ground truth, and what MANA did on the way: its lowest speed limit, how long it asked
for a stop, and its modes (from drive_request, the core's output). The map frame begins where the
chair starts (tools/indoor_localisation.sh), at (1.0, 1.375) in the indoor world, facing +x.
"""

import argparse
import csv
import math
import sys
import time

import rclpy
from geometry_msgs.msg import PoseStamped
from lifecycle_msgs.srv import GetState
from nav2_msgs.action import NavigateToPose
from nav_msgs.msg import Odometry
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter import Parameter

from mana_msgs.msg import DriveRequest

START = (1.0, 1.375)  # the map's origin in the indoor world (mana_sim.localisation.INDOOR_START)
# x, y, yaw in the map frame: along the south corridor, round the east corner, along the north
# corridor (around the central block), and back to the start the shorter way round
GOALS = [(13.0, 0.0, 0.0), (13.9, 6.6, 1.57), (7.0, 7.2, 3.14), (0.5, 0.0, 3.14)]
STATUS = {4: "reached", 5: "cancelled", 6: "aborted"}


class Autopilot(Node):
    def __init__(self):
        super().__init__(
            "autopilot_goal", parameter_overrides=[Parameter("use_sim_time", value=True)]
        )
        self.client = ActionClient(self, NavigateToPose, "navigate_to_pose")
        self.gt = None
        self.requests: list[tuple[float, bool, float, int]] = []
        self.create_subscription(Odometry, "/model/mana_chair/ground_truth", self.on_gt, 10)
        self.create_subscription(DriveRequest, "drive_request", self.on_request, 10)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def wait_active(self, timeout_s: float) -> bool:
        """Until bt_navigator is active: before that it rejects every goal ("Action server is
        inactive"), and the lifecycle manager activates the servers one after the other."""
        state = self.create_client(GetState, "bt_navigator/get_state")
        wall = time.time()
        while time.time() - wall < timeout_s:
            if state.wait_for_service(timeout_sec=2.0):
                reply = state.call_async(GetState.Request())
                rclpy.spin_until_future_complete(self, reply, timeout_sec=5.0)
                if reply.result() is not None and reply.result().current_state.id == 3:
                    return True
            time.sleep(1.0)
        return False

    def on_gt(self, m: Odometry) -> None:
        self.gt = (m.pose.pose.position.x, m.pose.pose.position.y)

    def on_request(self, m: DriveRequest) -> None:
        self.requests.append((self.now(), bool(m.stop), float(m.speed_limit), int(m.mode)))

    def go(self, x: float, y: float, yaw: float, timeout_s: float) -> dict:
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = "map"
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = x, y
        goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = (
            math.sin(yaw / 2),
            math.cos(yaw / 2),
        )
        start, self.requests = self.now(), []
        sent = self.client.send_goal_async(goal)
        rclpy.spin_until_future_complete(self, sent, timeout_sec=30)
        handle = sent.result()
        if handle is None or not handle.accepted:
            return {"goal": f"{x} {y}", "result": "refused", "seconds": 0}
        result = handle.get_result_async()
        wall = time.time()
        while not result.done() and time.time() - wall < timeout_s:
            rclpy.spin_once(self, timeout_sec=0.1)
        if not result.done():
            handle.cancel_goal_async()
            status = "timed out"
        else:
            status = STATUS.get(result.result().status, str(result.result().status))
        req = self.requests
        stop_s = sum(b[0] - a[0] for a, b in zip(req, req[1:], strict=False) if a[1])
        gx, gy = (self.gt[0] - START[0], self.gt[1] - START[1]) if self.gt else (math.nan,) * 2
        return {
            "goal": f"{x:g} {y:g}",
            "result": status,
            "seconds": round(self.now() - start, 1),
            "end_error_m": round(math.hypot(gx - x, gy - y), 2),
            "lowest_speed_limit": round(min((r[2] for r in req if not r[1]), default=math.nan), 2),
            "stop_s": round(stop_s, 1),
            "modes": "".join(sorted({"WT"[r[3]] for r in req if r[3] in (0, 1)})),
        }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--goal", type=float, nargs=3, metavar=("X", "Y", "YAW"))
    p.add_argument("--timeout", type=float, default=300.0, help="wall seconds per goal")
    p.add_argument("--out", help="a CSV of the results")
    a = p.parse_args(argv)
    rclpy.init()
    node = Autopilot()
    if not node.client.wait_for_server(timeout_sec=60) or not node.wait_active(180):
        print("Nav2 is not up and active: start autopilot.launch.py", file=sys.stderr)
        return 2
    rows = []
    for x, y, yaw in [tuple(a.goal)] if a.goal else GOALS:
        row = node.go(x, y, yaw, a.timeout)
        print(row, flush=True)
        rows.append(row)
    if a.out:
        with open(a.out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    node.destroy_node()
    rclpy.shutdown()
    return 0 if all(r["result"] == "reached" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
