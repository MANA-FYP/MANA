"""ROS 2 node for the alert link (M3).

Each radio's heartbeat answers arrive on `link/wifi/heartbeat` and `link/bluetooth/heartbeat`.
Ten times a second the node publishes a LinkReport for each radio on `link`, chooses the radio for
alerts with LinkSelector, and logs every switch.

Urgent messages from the core (`user_message`) become alerts for the caregiver. They go out on
`alert/wifi` or `alert/bluetooth` as "<id>|<text>" and stay in the outbox until their id comes
back on `alert/ack`, so an alert sent just before Wi-Fi fades is sent again over Bluetooth.
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_msgs.msg import Header, String, UInt32

from mana_connectivity.link import Heartbeat, Link, LinkSelector, Outbox
from mana_msgs.msg import LinkReport, UserMessage

TOPIC = {Link.WIFI: "wifi", Link.BLUETOOTH: "bluetooth"}
NAME = {**TOPIC, None: "no link"}
TRUST = 0.9  # a missed heartbeat is certain; the trust is in the heartbeat itself


class LinkNode(Node):
    def __init__(self):
        super().__init__("mana_connectivity")
        p = {
            name: self.declare_parameter(name, default).value
            for name, default in (
                ("period_s", 1.0),
                ("missed_to_leave", 2),
                ("back_s", 5.0),
                ("resend_s", 1.0),
            )
        }
        self.beats = {link: Heartbeat(p["period_s"]) for link in Link}
        self.selector = LinkSelector(p["missed_to_leave"], p["back_s"])
        self.outbox = Outbox(p["resend_s"])
        for link, name in TOPIC.items():
            self.create_subscription(
                Header, f"link/{name}/heartbeat", lambda _, link=link: self.on_beat(link), 10
            )
        self.create_subscription(UserMessage, "user_message", self.on_message, 10)
        self.create_subscription(UInt32, "alert/ack", lambda m: self.outbox.ack(m.data), 10)
        self.report = self.create_publisher(LinkReport, "link", 10)
        self.alerts = {
            link: self.create_publisher(String, f"alert/{name}", 10) for link, name in TOPIC.items()
        }
        self.create_timer(0.1, self.tick)

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    def on_beat(self, link: Link) -> None:
        self.beats[link].beat(self.now())

    def on_message(self, m: UserMessage) -> None:
        if m.priority == UserMessage.URGENT:
            self.outbox.add(m.text)

    def tick(self) -> None:
        stamp = self.get_clock().now()
        now = stamp.nanoseconds / 1e9
        missed = {link: beat.missed(now) for link, beat in self.beats.items()}
        for link in Link:
            out = LinkReport(
                link=int(link),
                trust=TRUST,
                connected=missed[link] < self.selector.missed_to_leave,
                rssi_dbm=float("nan"),  # heartbeats are the evidence; the signal is not read
                missed_heartbeats=missed[link],
            )
            out.header.stamp = stamp.to_msg()
            self.report.publish(out)
        before = self.selector.active
        active = self.selector.update(missed[Link.WIFI], missed[Link.BLUETOOTH], now)
        if active != before:
            self.get_logger().info(f"alert link: {NAME[before]} -> {NAME[active]}")
        for alert_id, text in self.outbox.due(active, now):
            self.alerts[active].publish(String(data=f"{alert_id}|{text}"))


def main(args=None):
    rclpy.init(args=args)
    node = LinkNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()
