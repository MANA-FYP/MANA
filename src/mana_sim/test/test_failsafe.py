"""Fail-safe (#85): one of MANA's own nodes dies or hangs mid-drive.

The real nodes run as processes (`ros2 run`), each in its own process group, on a ROS domain of
their own. A probe stands in for the sensors and the Wi-Fi heartbeat, and watches the drive
requests and the wheels' command. Each test kills or freezes one node and checks that what
follows is the safe behaviour written down in docs/results.md (#85), within the time it gives.
"""

import os
import signal
import subprocess
import time

import pytest
import rclpy
from geometry_msgs.msg import Twist
from mana_sim.host import WATCHDOG_S
from rclpy.executors import SingleThreadedExecutor
from rclpy.node import Node
from sensor_msgs.msg import NavSatFix, NavSatStatus
from std_msgs.msg import Header

from mana_msgs.msg import DriveRequest, GroundReport, LinkReport, MotionReport, PositionReport

DOMAIN = 85
NODES = {
    "core": ("mana_core", "core_node"),
    "host": ("mana_sim", "host_node"),
    "position": ("mana_position", "position_node"),
    "link": ("mana_connectivity", "link_node"),
}
MAX_AGE_S = 0.5  # Limits.max_age_s: a ground report counts this long
LOST_S = 2.5  # Limits.position_max_age_s and link_max_age_s
LATE_S = 0.3  # what the timers (0.05 s host, 0.1 s core) and a loaded machine may add
NO_GROUND = "Ground sensors not available, slowing down"


class Probe(Node):
    """Flat ground from both cameras, a level chair, a good GPS fix and a Wi-Fi heartbeat, 20
    times a second; and every drive request and wheels' command, with when it came (monotonic),
    and how many position and link reports were sent."""

    def __init__(self, context):
        super().__init__("failsafe_probe", context=context)
        self.ground_on = True
        self.drives, self.wheels = [], []
        self.reports = {"position": 0, "link": 0}
        for kind, topic in ((PositionReport, "position"), (LinkReport, "link")):
            self.create_subscription(
                kind, topic, lambda _, t=topic: self.reports.__setitem__(t, self.reports[t] + 1), 10
            )
        self.create_subscription(
            DriveRequest, "drive_request", lambda m: self.drives.append((time.monotonic(), m)), 10
        )
        self.create_subscription(
            Twist,
            "/model/mana_chair/wheels_cmd_vel",
            lambda m: self.wheels.append((time.monotonic(), m)),
            10,
        )
        self.ground = self.create_publisher(GroundReport, "ground", 10)
        self.motion = self.create_publisher(MotionReport, "motion", 10)
        self.fix = self.create_publisher(NavSatFix, "gps/fix", 10)
        self.beat = self.create_publisher(Header, "link/wifi/heartbeat", 10)
        self.create_timer(0.05, self.feed)

    def feed(self):
        stamp = self.get_clock().now().to_msg()
        if self.ground_on:
            for source in (GroundReport.CAMERA, GroundReport.DEPTH_CAMERA):
                flat = GroundReport(source=source, ground=GroundReport.FLAT, trust=0.9)
                flat.header.stamp = stamp
                self.ground.publish(flat)
        level = MotionReport(trust=0.9)
        level.header.stamp = stamp
        self.motion.publish(level)
        fix = NavSatFix(latitude=33.64, longitude=72.99)
        fix.status.status = NavSatStatus.STATUS_FIX
        fix.position_covariance_type = NavSatFix.COVARIANCE_TYPE_DIAGONAL_KNOWN
        fix.position_covariance[0] = fix.position_covariance[4] = 1.0  # 1 m
        fix.header.stamp = stamp
        self.fix.publish(fix)
        self.beat.publish(Header(stamp=stamp))


def signal_group(p: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(p.pid, sig)
    except ProcessLookupError:
        pass  # the whole group has exited already


@pytest.fixture
def mana():
    env = {**os.environ, "ROS_DOMAIN_ID": str(DOMAIN)}
    procs = {}
    context = rclpy.Context()
    rclpy.init(context=context, domain_id=DOMAIN)
    probe = Probe(context)
    executor = SingleThreadedExecutor(context=context)
    executor.add_node(probe)

    def start(*names):
        for name in names:
            procs[name] = subprocess.Popen(
                ["ros2", "run", *NODES[name]], env=env, start_new_session=True
            )

    def spin_until(done, seconds):
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            executor.spin_once(timeout_sec=0.02)
        return done()

    yield start, procs, probe, spin_until
    probe.destroy_node()
    rclpy.shutdown(context=context)
    for p in procs.values():  # a frozen node gets SIGCONT first, or it never sees the SIGINT
        signal_group(p, signal.SIGCONT)
        signal_group(p, signal.SIGINT)
    for p in procs.values():
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        signal_group(p, signal.SIGKILL)


def last_reason(probe):
    return probe.drives[-1][1].reason if probe.drives else None


def after(t, messages, test):
    """How long after t the first message that passes the test came, or None."""
    return next((at - t for at, m in messages if at > t and test(m)), None)


@pytest.mark.parametrize("how", [signal.SIGKILL, signal.SIGSTOP], ids=["killed", "frozen"])
def test_when_the_core_dies_or_hangs_the_hosts_watchdog_stops_the_chair(mana, how):
    start, procs, probe, spin_until = mana
    start("core", "host")
    assert spin_until(lambda: probe.wheels and probe.wheels[-1][1].linear.x > 0.0, 60.0)
    t = time.monotonic()
    signal_group(procs["core"], how)
    assert spin_until(lambda: after(t, probe.wheels, lambda m: m.linear.x == 0.0), 5.0)
    still = after(t, probe.wheels, lambda m: m.linear.x == 0.0)
    print(f"core {how.name}: the wheels' command was 0 after {still:.2f} s")
    assert still <= WATCHDOG_S + LATE_S
    spin_until(lambda: False, 1.0)  # and it stays 0
    assert all(m.linear.x == 0.0 for at, m in probe.wheels if at > t + still)


def test_when_the_ground_reports_stop_the_core_slows_down(mana):
    start, _, probe, spin_until = mana
    start("core")
    assert spin_until(lambda: last_reason(probe) == "Level ground", 60.0)
    t = time.monotonic()
    probe.ground_on = False  # a perception node died or a camera topic stalled
    assert spin_until(lambda: last_reason(probe) == NO_GROUND, 5.0)
    slowed = after(t, probe.drives, lambda m: m.reason == NO_GROUND)
    print(f"ground reports stopped: slowed after {slowed:.2f} s")
    assert slowed <= MAX_AGE_S + LATE_S
    assert probe.drives[-1][1].speed_limit == pytest.approx(0.3)


@pytest.mark.parametrize(
    "node, reason",
    [("position", "Position unknown, slowing down"), ("link", "No alert link, slowing down")],
)
def test_when_the_position_or_link_node_dies_the_core_slows_down(mana, node, reason):
    start, procs, probe, spin_until = mana
    start("core", "position", "link")
    assert spin_until(lambda: min(probe.reports.values()) >= 10, 60.0), probe.reports
    assert spin_until(lambda: last_reason(probe) == "Level ground", 5.0)
    t = time.monotonic()
    signal_group(procs[node], signal.SIGKILL)
    assert spin_until(lambda: last_reason(probe) == reason, LOST_S + 3.0)
    slowed = after(t, probe.drives, lambda m: m.reason == reason)
    print(f"{node} node killed: slowed after {slowed:.2f} s")
    assert slowed <= LOST_S + 0.1 + LATE_S  # a report every 0.1 s, then it ages out
    assert probe.drives[-1][1].speed_limit == pytest.approx(0.3)
