"""Faults switched in the running simulation, and what MANA does about each (#42, #44, #85).

    tools/sim_smoke.sh --faults   # starts the world and the core, then runs this

One after the other, on the simulation's own fault switch (`set_fault`):
1. GPS loss: the position reports stop (there is no lidar map in this world yet, #43), and the
   core slows down with "Position unknown".
2. GPS back: the core stops saying so.
3. Wi-Fi fade: the Wi-Fi heartbeats get lost until none arrives. The link node gives Wi-Fi up,
   Bluetooth still carries the alerts, so the core keeps saying "Level ground". The probe is
   the helper's phone: it sends an urgent message every ALERT_EVERY_S during the fade, answers
   the alerts that come on Bluetooth, and takes Wi-Fi as delivering nothing once the fade began.
   Every alert must still reach the phone, and the alert link must change at most once.
4. The clock stops (Gazebo paused, #85): its time stands still, and the core decides nothing.
   When it runs again, the clock goes on from where it stopped, and the core decides again.
5. The bridge to Gazebo is killed: the launch file starts it again, and the clock comes back.
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
from std_msgs.msg import String

from mana_msgs.msg import DriveRequest, GroundReport, LinkReport, PositionReport, UserMessage
from mana_msgs.srv import SetFault

LEVEL = "Level ground"
NO_POSITION = "Position unknown, slowing down"
WAIT_S = 60.0  # wall seconds to wait for each step: the simulation may run slower than real time
GPS_LOST_S = 1.5 + 2.5 + 1.0  # the position node's max_age_s, the core's position_max_age_s, ticks
FADE_S = 10.0
ALERT_EVERY_S = 0.5
PAUSE_S = 5.0
ALERT_TEXT = "fault check alert"  # the probe's own urgent messages, told apart from the core's


def world_control(req: str) -> None:
    """Pause or run the world, as the launch file does."""
    subprocess.run(
        [
            "gz",
            "service",
            "-s",
            "/world/mana_route/control",
            "--reqtype",
            "gz.msgs.WorldControl",
            "--reptype",
            "gz.msgs.Boolean",
            "--timeout",
            "3000",
            "--req",
            req,
        ],
        check=True,
        capture_output=True,
    )


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
        self.fade_from = None  # sim time the Wi-Fi fade began: from then on Wi-Fi delivers nothing
        self.alerts_sent = []  # texts of the urgent messages sent during the fade
        self.received = []  # (sim time, link, text) of every alert that reached the phone
        self.sent_on = []  # (sim time, link, text) of every alert the node sent, reached or not
        self.say = self.create_publisher(UserMessage, "user_message", 10)
        self.core_said = []  # (sim time, text) of the core's user messages, not the probe's alerts
        self.create_subscription(UserMessage, "user_message", self.on_message, 10)
        self.ack = self.create_publisher(String, "alert/ack", 10)
        for link, name in ((LinkReport.WIFI, "wifi"), (LinkReport.BLUETOOTH, "bluetooth")):
            self.create_subscription(
                String, f"alert/{name}", lambda m, link=link: self.on_alert(link, m.data), 10
            )

    def on_alert(self, link, data):
        alert_id, _, text = data.partition("|")
        self.sent_on.append((self.now(), link, text))
        if link == LinkReport.WIFI and self.fade_from is not None:
            return  # lost on the fading Wi-Fi: not answered, so the link node sends it again
        self.received.append((self.now(), link, text))
        self.ack.publish(String(data=alert_id))

    def delivered(self) -> set[str]:
        return {text for _, _, text in self.received}

    def on_message(self, m):
        if not m.text.startswith(ALERT_TEXT):
            self.core_said.append((self.now(), m.text))

    def send_alert(self):
        text = f"{ALERT_TEXT} {len(self.alerts_sent) + 1}"
        self.alerts_sent.append(text)
        self.say.publish(UserMessage(priority=UserMessage.URGENT, text=text, speak=True))

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
        results.append(bool(ok))  # some checks pass a set or a list
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
        p.spin_until(lambda: any(at > t and x == NO_POSITION for at, x in p.core_said), 5.0)
        check(
            any(at > t and x == NO_POSITION for at, x in p.core_said),
            f"GPS lost: the core tells the user '{NO_POSITION}' (user_message)",
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
        p.fade_from = t
        end = time.monotonic() + WAIT_S * 2
        while p.now() < t + FADE_S + 5.0 and time.monotonic() < end:
            if p.now() < t + FADE_S and p.now() >= t + len(p.alerts_sent) * ALERT_EVERY_S:
                p.send_alert()
            rclpy.spin_once(p, timeout_sec=0.05)
        p.spin_until(lambda: set(p.alerts_sent) <= p.delivered())  # resends may still be on the way
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
        missing = [a for a in p.alerts_sent if a not in p.delivered()]
        wifi_sends = [x for at, link, x in p.sent_on if at > t and link == LinkReport.WIFI]
        on_wifi = set(wifi_sends) & set(p.alerts_sent)
        check(
            p.alerts_sent and not missing,
            f"Wi-Fi fade: all {len(p.alerts_sent)} alerts reached the phone"
            f" ({len(on_wifi)} of them went out on the fading Wi-Fi first, {len(wifi_sends)} sends,"
            " all lost, and were sent again)"
            + (f"; missing: {', '.join(missing)}" if missing else ""),
        )
        during = [link for at, link, _ in p.sent_on if at > t]  # in the order the node sent them
        switches = sum(a != b for a, b in zip(during, during[1:], strict=False))
        check(switches <= 1, f"Wi-Fi fade: the alert link changed {switches} time(s), at most once")
        p.set_fault("wifi", "fade", False)
        p.fade_from = None

        world_control("pause: true")
        # the pause takes a moment, and Gazebo still sends the clock: wait until its time stands
        # still (two readings 0.2 s of wall time apart), within PAUSE_S
        end, last = time.monotonic() + PAUSE_S, p.now()
        while time.monotonic() < end:
            p.spin_until(lambda: False, 0.2)
            if p.now() == last:
                break
            last = p.now()
        paused_at, decided = p.now(), len(p.reasons)
        p.spin_until(lambda: False, PAUSE_S)  # wall seconds
        moved = p.now() - paused_at  # simulation time that went by while paused
        check(
            moved == 0.0 and len(p.reasons) == decided,
            f"clock stopped for {PAUSE_S:.0f} s of wall time: {moved:.2f} s of simulation time and"
            f" {len(p.reasons) - decided} new decisions",
        )
        world_control("pause: false")
        resumed = p.spin_until(lambda: len(p.reasons) > decided + 10 and p.reasons[-1][1] == LEVEL)
        check(resumed, f"clock running again: the core decides again and says '{LEVEL}'")

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
