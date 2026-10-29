"""Faults switched in the running simulation, and what MANA does about each (#42, #44, #85).

    tools/sim_smoke.sh --faults   # starts the world and the core, then runs this

One after the other, on the simulation's own fault switch (`set_fault`):
1. GPS loss: the position reports stop (there is no lidar map in this world yet, #43), and the
   core slows down with "Position unknown".
2. GPS back: the core stops saying so.
3. Wi-Fi fade: the Wi-Fi heartbeats get lost until none arrives. The link node gives Wi-Fi up,
   Bluetooth still carries the alerts, so the core keeps saying "Level ground".
4. The bridge to Gazebo is killed: the launch file starts it again, and the clock comes back.
The world has no cameras (no GPU in CI), and with no ground report the core slows down for that
before it looks at the position or the link. So this probe sends flat ground from both cameras,
and sim_smoke.sh starts the world with driver:=true: the chair moves only when a driver asks,
and none does, so it stands still on the route while the core decides as if it drove.
Times are in simulation seconds. Exit 0 when every check passes, 1 otherwise.
"""

import subprocess
import sys
import time

import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rosgraph_msgs.msg import Clock

from mana_msgs.msg import DriveRequest, GroundReport, LinkReport, PositionReport
from mana_msgs.srv import SetFault

LEVEL = "Level ground"
NO_POSITION = "Position unknown, slowing down"
WAIT_S = 60.0  # wall seconds to wait for each step: the simulation may run slower than real time
GPS_LOST_S = 1.5 + 2.5 + 1.0  # the position node's max_age_s, the core's position_max_age_s, ticks
FADE_S = 10.0


class Probe(Node):
    def __init__(self):
        super().__init__("sim_faults", parameter_overrides=[Parameter("use_sim_time", value=True)])
        self.reasons = []  # (sim time, reason) of every drive request
        self.positions = []  # sim time of every GPS position report
        self.links = []  # (sim time, link, usable)
        self.clocks = 0
        self.create_subscription(
            DriveRequest, "drive_request", lambda m: self.reasons.append((self.now(), m.reason)), 10
        )
        self.create_subscription(PositionReport, "position", self.on_position, 10)
        self.create_subscription(LinkReport, "link", self.on_link, 10)
        self.create_subscription(Clock, "/clock", self.on_clock, 10)
        self.fault = self.create_client(SetFault, "set_fault")
        self.ground = self.create_publisher(GroundReport, "ground", 10)
        self.create_timer(0.1, self.flat_ground)

    def flat_ground(self):
        stamp = self.get_clock().now().to_msg()
        for source in (GroundReport.CAMERA, GroundReport.DEPTH_CAMERA):
            flat = GroundReport(source=source, ground=GroundReport.FLAT, trust=0.9)
            flat.header.stamp = stamp
            self.ground.publish(flat)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_position(self, m):
        if m.source == PositionReport.GPS:
            self.positions.append(self.now())

    def on_link(self, m):
        usable = m.connected and m.missed_heartbeats < 2  # Limits.missed_heartbeats
        self.links.append((self.now(), m.link, usable))

    def on_clock(self, _):
        self.clocks += 1

    def spin_until(self, done, seconds=WAIT_S) -> bool:
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            rclpy.spin_once(self, timeout_sec=0.05)
        return bool(done())

    def set_fault(self, sensor, kind, active, value=0.0) -> None:
        if not self.fault.wait_for_service(timeout_sec=WAIT_S):
            raise RuntimeError("no set_fault service")
        req = SetFault.Request(sensor=sensor, kind=kind, active=active, value=value)
        future = self.fault.call_async(req)
        self.spin_until(future.done)
        if not future.done() or not future.result().ok:
            raise RuntimeError(f"set_fault {sensor} {kind} {active} refused: {future.result()}")

    def first(self, since, test):
        """The sim time of the first drive request after since whose reason passes the test."""
        return next((t for t, r in self.reasons if t > since and test(r)), None)


def main() -> int:
    rclpy.init()
    p = Probe()
    results = []

    def check(ok, text):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'} {text}", flush=True)

    try:
        ready = p.spin_until(
            lambda: (
                p.positions
                and any(link == LinkReport.WIFI and ok for _, link, ok in p.links)
                and p.reasons
                and p.reasons[-1][1] == LEVEL
            )
        )
        check(ready, f"before any fault: GPS position, Wi-Fi link, and the core says '{LEVEL}'")
        if not ready:
            return 1

        t = p.now()
        p.set_fault("gps", "dropout", True)
        p.spin_until(lambda: p.first(t, lambda r: r == NO_POSITION))
        slowed = p.first(t, lambda r: r == NO_POSITION)
        check(
            slowed is not None and slowed - t <= GPS_LOST_S,
            f"GPS lost: '{NO_POSITION}' after "
            + (f"{slowed - t:.1f} s" if slowed else "never")
            + f" (at most {GPS_LOST_S:.1f} s)",
        )

        t = p.now()
        p.set_fault("gps", "dropout", False)
        p.spin_until(lambda: p.first(t, lambda r: r == LEVEL))
        back = p.first(t, lambda r: r == LEVEL)
        check(
            back is not None,
            f"GPS back: the core says '{LEVEL}' again after "
            + (f"{back - t:.1f} s" if back else "never"),
        )

        t = p.now()
        p.set_fault("wifi", "fade", True, FADE_S)
        p.spin_until(lambda: p.now() > t + FADE_S + 5.0, WAIT_S * 2)
        lost = next(
            (s for s, link, ok in p.links if s > t and link == LinkReport.WIFI and not ok), None
        )
        bluetooth = all(ok for s, link, ok in p.links if s > t and link == LinkReport.BLUETOOTH)
        check(
            lost is not None,
            "Wi-Fi fade: the link node gives Wi-Fi up after "
            + (f"{lost - t:.1f} s" if lost else "never"),
        )
        check(
            bluetooth and p.first(t, lambda r: r != LEVEL) is None,
            f"Wi-Fi fade: Bluetooth stays usable and the core keeps saying '{LEVEL}'",
        )
        p.set_fault("wifi", "fade", False)

        before = subprocess.run(["pgrep", "-f", "parameter_bridge"], capture_output=True, text=True)
        subprocess.run(["pkill", "-KILL", "-f", "ros_gz_bridge/parameter_bridge"], check=False)
        killed_at = time.monotonic()
        clocks = p.clocks
        respawned = p.spin_until(
            lambda: p.clocks > clocks + 10 and time.monotonic() > killed_at + 2.0
        )
        after = subprocess.run(["pgrep", "-f", "parameter_bridge"], capture_output=True, text=True)
        check(
            respawned and set(after.stdout.split()) - set(before.stdout.split()),
            "bridge killed: a new bridge started and the clock came back after "
            f"{time.monotonic() - killed_at:.1f} s of wall time",
        )
    finally:
        p.destroy_node()
        rclpy.shutdown()
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
