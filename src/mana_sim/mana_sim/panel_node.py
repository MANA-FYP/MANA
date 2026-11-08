"""ROS 2 node: the supervisor's panel (#80) on http://localhost:8090 during a test drive.

    ros2 launch mana_sim world.launch.py panel:=true     # then open http://localhost:8090

It listens to drive_request, user_message, position, link, ground, obstacle, driver and
fault_state, and keeps what panel.Panel shows. The page asks for /api/state a few times a second.
Its buttons post to /api/fault ({"label": ..., "active": true}), which calls fault_node's
set_fault, and to /api/command ({"name": "STOP"}), which publishes a UserCommand as a button press.
The server listens on 127.0.0.1 only: the panel is for the computer that runs the simulation.

Parameters: port (default 8090).
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from rclpy.time import Time
from std_msgs.msg import String

from mana_msgs.msg import (
    DriveRequest,
    GroundReport,
    LinkReport,
    ObstacleReport,
    PositionReport,
    UserCommand,
    UserMessage,
)
from mana_msgs.srv import SetFault
from mana_sim import panel as pn
from mana_sim.faults import DEFAULT_VALUE

MAX_BODY = 1024  # bytes: a button press is a few dozen


class PanelNode(Node):
    def __init__(self, page: str | None = None):
        super().__init__("mana_panel")
        port = self.declare_parameter("port", 8090).value
        self.panel, self.lock = pn.Panel(), threading.Lock()
        self.page = page if page is not None else load_page()
        for kind, topic, handler in (
            (DriveRequest, "drive_request", self.on_drive),
            (UserMessage, "user_message", self.on_message),
            (PositionReport, "position", self.on_position),
            (LinkReport, "link", self.on_link),
            (GroundReport, "ground", self.on_ground),
            (ObstacleReport, "obstacle", self.on_obstacle),
            (String, "driver", lambda m: self.update(self.panel.on_driver, m.data)),
        ):
            self.create_subscription(kind, topic, handler, 10)
        latched = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(
            String, "fault_state", lambda m: self.update(self.panel.on_faults, m.data), latched
        )
        self.say = self.create_publisher(UserCommand, "user_command", 10)
        self.fault_client = self.create_client(SetFault, "set_fault")
        self.server = ThreadingHTTPServer(("127.0.0.1", port), handler_for(self))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.get_logger().info(f"supervisor panel on http://localhost:{self.port}")

    def now(self) -> float:
        return self.get_clock().now().nanoseconds / 1e9

    @staticmethod
    def stamp(msg) -> float:
        return Time.from_msg(msg.header.stamp).nanoseconds / 1e9

    def update(self, fn, *args):
        with self.lock:
            fn(*args)

    def on_drive(self, m):
        self.update(self.panel.on_decision, self.now(), m.mode, m.stop, m.speed_limit, m.reason)

    def on_message(self, m):
        self.update(self.panel.on_message, self.now(), m.text, m.priority == UserMessage.URGENT)

    def on_position(self, m):
        self.update(self.panel.on_position, self.stamp(m), m.source, m.trust, m.error_m)

    def on_link(self, m):
        self.update(self.panel.on_link, self.stamp(m), m.link, m.connected, m.missed_heartbeats)

    def on_ground(self, m):
        text = pn.ground_text(m.source, m.ground, m.distance_m)
        self.update(self.panel.on_report, f"ground {m.source}", self.stamp(m), m.trust, text)

    def on_obstacle(self, m):
        text = pn.obstacle_text(m.source, m.label, m.distance_m)
        self.update(self.panel.on_report, f"obstacle {m.source}", self.stamp(m), m.trust, text)

    def state(self) -> dict:
        with self.lock:
            return self.panel.view(self.now())

    def switch_fault(self, label: str, active: bool) -> str:
        which = pn.fault(label)
        if which is None:
            raise ValueError(f"no fault switch {label!r}")
        if not self.fault_client.service_is_ready():
            return "no set_fault service: is the simulation running?"
        sensor, kind = which
        req = SetFault.Request(
            sensor=sensor, kind=kind, active=active, value=DEFAULT_VALUE.get(kind, 1.0)
        )
        self.fault_client.call_async(req)  # fault_state shows the result
        return f"{label} {'on' if active else 'off'}"

    def command(self, name: str) -> str:
        if name not in pn.COMMANDS:
            raise ValueError(f"no command {name!r}")
        self.say.publish(
            UserCommand(command=int(pn.COMMANDS[name]), source=UserCommand.BUTTON, trust=1.0)
        )
        return f"{name} sent"


def load_page() -> str:
    share = Path(get_package_share_directory("mana_sim"))
    return (share / "web" / "panel.html").read_text(encoding="utf-8")


def handler_for(node: PanelNode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # the node logs what matters
            pass

        def reply(self, code: int, body: str | dict, kind: str = "application/json"):
            data = (json.dumps(body) if isinstance(body, dict) else body).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def from_this_panel(self, post: bool) -> bool:
            """Refuse what does not come from this panel's own page. Host must be this server
            (against DNS rebinding), and an Origin, when a browser sends one, must be too. A
            press must be JSON: a page on another site can then only send it after a preflight,
            which this server does not answer, so another site cannot press the buttons."""
            mine = {f"localhost:{node.port}", f"127.0.0.1:{node.port}"}
            origin = self.headers.get("Origin")
            if self.headers.get("Host") not in mine or (
                origin is not None and origin not in {f"http://{h}" for h in mine}
            ):
                self.reply(403, {"error": "only this panel's own page may use it"})
                return False
            kind = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if post and kind != "application/json":
                self.reply(415, {"error": "a press is sent as application/json"})
                return False
            return True

        def do_GET(self):
            if not self.from_this_panel(post=False):
                return
            if self.path == "/":
                self.reply(200, node.page, "text/html")
            elif self.path == "/api/state":
                self.reply(200, node.state())
            else:
                self.reply(404, {"error": "not found"})

        def do_POST(self):
            if not self.from_this_panel(post=True):
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= MAX_BODY:
                    raise ValueError("a body of 1 to 1024 bytes is expected")
                body = json.loads(self.rfile.read(length))
                if self.path == "/api/fault":
                    if not isinstance(body["active"], bool):
                        raise ValueError("active must be true or false")
                    done = node.switch_fault(str(body["label"]), body["active"])
                elif self.path == "/api/command":
                    done = node.command(str(body["name"]))
                else:
                    return self.reply(404, {"error": "not found"})
            except (ValueError, KeyError, TypeError) as e:
                return self.reply(400, {"error": str(e)})
            node.get_logger().info(f"panel: {done}")
            self.reply(200, {"done": done})

    return Handler


def main(args=None):
    rclpy.init(args=args)
    node = PanelNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.server.shutdown()
        node.destroy_node()
        rclpy.try_shutdown()
