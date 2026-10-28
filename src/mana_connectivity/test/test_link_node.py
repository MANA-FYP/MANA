"""End to end: an urgent alert sent while Wi-Fi fades still arrives, over Bluetooth."""

import time

import pytest
import rclpy
from mana_connectivity.link_node import LinkNode
from rclpy.executors import SingleThreadedExecutor
from std_msgs.msg import Header, String

from mana_msgs.msg import LinkReport, UserMessage


@pytest.fixture
def ros():
    # a namespace of its own: colcon tests the packages in parallel, and the core's stop test
    # publishes an urgent message on user_message at the same time
    rclpy.init(args=["--ros-args", "-r", "__ns:=/link_node_test"])
    yield
    rclpy.shutdown()


def test_no_alert_is_lost_when_wifi_fades(ros):
    node, probe = LinkNode(), rclpy.create_node("probe")
    got = {"wifi": [], "bluetooth": []}
    reports = []
    for name in got:
        probe.create_subscription(String, f"alert/{name}", got[name].append, 10)
    probe.create_subscription(LinkReport, "link", reports.append, 10)
    beat = {n: probe.create_publisher(Header, f"link/{n}/heartbeat", 10) for n in got}
    say = probe.create_publisher(UserMessage, "user_message", 10)
    ack = probe.create_publisher(String, "alert/ack", 10)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    def spin(seconds, wifi=True):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            for name in ("wifi", "bluetooth") if wifi else ("bluetooth",):
                beat[name].publish(Header())
            executor.spin_once(timeout_sec=0.05)

    spin(1.0)
    say.publish(UserMessage(priority=UserMessage.URGENT, text="Drop ahead, stopping"))
    spin(0.3)
    sent_on_wifi = [m.data for m in got["wifi"]]
    spin(3.5, wifi=False)  # Wi-Fi fades before the other end acknowledges
    resent = [m.data for m in got["bluetooth"]]
    alert_id = resent[0].split("|")[0]
    ack.publish(String(data=alert_id))
    spin(0.5, wifi=False)
    count = len(got["bluetooth"])
    spin(1.5, wifi=False)
    for n in (node, probe):
        n.destroy_node()

    assert len(sent_on_wifi) == 1 and sent_on_wifi[0].endswith("-1|Drop ahead, stopping")
    assert resent[0] == sent_on_wifi[0]  # the same alert, with the same id
    assert len(got["bluetooth"]) == count  # acknowledged, so not sent again
    wifi = [r for r in reports if r.link == LinkReport.WIFI]
    assert wifi[0].connected and not wifi[-1].connected and wifi[-1].missed_heartbeats >= 2
