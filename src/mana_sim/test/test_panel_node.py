"""The supervisor's panel node (#80) through ROS and HTTP: it shows the core's drive request,
sends a button press as a UserCommand, and refuses what is not one of its switches."""

import json
import time
import urllib.error
import urllib.request

import pytest
import rclpy
from mana_sim.panel_node import PanelNode
from rclpy.executors import SingleThreadedExecutor

from mana_msgs.msg import DriveRequest, UserCommand


@pytest.fixture
def panel():
    rclpy.init(args=["--ros-args", "-p", "port:=0"])  # any free port
    node, probe = PanelNode(page="<p>panel</p>"), rclpy.create_node("probe")
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    executor.add_node(probe)

    def spin_until(done, seconds=5.0):
        end = time.monotonic() + seconds
        while not done() and time.monotonic() < end:
            executor.spin_once(timeout_sec=0.05)
        return done()

    yield node, probe, spin_until
    node.server.shutdown()
    for n in (node, probe):
        n.destroy_node()
    rclpy.shutdown()


def http(node, path, body=None, **headers):
    url = f"http://127.0.0.1:{node.port}{path}"
    data = None if body is None else json.dumps(body).encode()
    headers = {"Content-Type": "application/json", **headers}
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def test_the_page_and_the_drive_request_are_served(panel):
    node, probe, spin_until = panel
    assert http(node, "/") == (200, "<p>panel</p>")
    drive = probe.create_publisher(DriveRequest, "drive_request", 10)
    assert spin_until(lambda: drive.get_subscription_count() > 0)
    drive.publish(DriveRequest(mode=1, stop=True, speed_limit=0.0, reason="Drop ahead, stopping"))
    assert spin_until(lambda: node.panel.decision is not None)
    code, body = http(node, "/api/state")
    decision = json.loads(body)["decision"]
    assert code == 200 and (decision["mode"], decision["stop"]) == ("tracks", True)
    assert decision["reason"] == "Drop ahead, stopping"


def test_a_button_press_is_sent_as_a_user_command(panel):
    node, probe, spin_until = panel
    heard = []
    probe.create_subscription(UserCommand, "user_command", heard.append, 10)
    assert spin_until(lambda: node.say.get_subscription_count() > 0)
    assert http(node, "/api/command", {"name": "STOP"}) == (200, '{"done": "STOP sent"}')
    assert spin_until(lambda: heard)
    assert (heard[0].command, heard[0].source) == (UserCommand.STOP, UserCommand.BUTTON)


def test_what_is_not_a_switch_is_refused(panel):
    node, _, _ = panel
    assert http(node, "/api/command", {"name": "DRIVE OFF"})[0] == 400
    assert http(node, "/api/fault", {"label": "Open the door", "active": True})[0] == 400
    assert http(node, "/api/command", ["STOP"])[0] == 400  # not an object
    assert http(node, "/api/elsewhere", {"name": "STOP"})[0] == 404
    assert http(node, "/elsewhere")[0] == 404


def test_another_site_cannot_press_the_buttons(panel):
    # a page elsewhere can send text/plain with no preflight: refused, so it needs JSON, and then
    # the browser asks first (OPTIONS), which this server does not answer
    node, _, _ = panel
    press = {"name": "GO"}
    assert http(node, "/api/command", press, **{"Content-Type": "text/plain"})[0] == 415
    assert http(node, "/api/command", press, Origin="http://example.com")[0] == 403
    assert http(node, "/api/state", Host="mana.example.com")[0] == 403  # DNS rebinding
    assert http(node, "/api/command", press, Origin=f"http://localhost:{node.port}")[0] == 200
    assert http(node, "/api/fault", {"label": "Lose GPS", "active": "false"})[0] == 400
    preflight = urllib.request.Request(
        f"http://127.0.0.1:{node.port}/api/command", method="OPTIONS"
    )
    with pytest.raises(urllib.error.HTTPError) as no:
        urllib.request.urlopen(preflight, timeout=5)
    assert no.value.code == 501  # not answered, so the browser does not send the press


def test_a_fault_with_no_simulation_running_says_so(panel):
    node, _, _ = panel
    code, body = http(node, "/api/fault", {"label": "Lose GPS", "active": True})
    assert code == 200 and "no set_fault service" in body
