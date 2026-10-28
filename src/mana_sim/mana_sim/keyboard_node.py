"""ROS 2 node: the supervisor's keyboard, in a terminal of its own.

    ros2 run mana_sim keyboard --ros-args -p use_sim_time:=true

Arrow keys drive and take the controls from the autopilot; the other keys send the user's commands
to the core (driver.HELP lists them). Publishes cmd_vel/supervisor (geometry_msgs/Twist) at 10 Hz,
supervisor/control (std_msgs/Bool) on a take-over or a hand-back, and user_command
(mana_msgs/UserCommand, source BUTTON, trust 1.0). If this node stops, its commands stop, and the
chair stands still after the watchdog: it does not go back to the autopilot by itself.
"""

import os
import select
import sys
import termios
import tty

import rclpy
from geometry_msgs.msg import Twist, Vector3
from rclpy.node import Node
from rclpy.qos import DurabilityPolicy, QoSProfile
from std_msgs.msg import Bool

from mana_msgs.msg import UserCommand
from mana_sim.driver import HELP, Keys


class KeyboardNode(Node):
    def __init__(self):
        super().__init__("mana_keyboard")
        self.keys = Keys()
        self.twist = self.create_publisher(Twist, "cmd_vel/supervisor", 10)
        self.control = self.create_publisher(
            Bool,
            "supervisor/control",
            QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL),
        )
        self.command = self.create_publisher(UserCommand, "user_command", 10)
        self.driving = False  # whether these keys hold the controls

    def press(self, key: str) -> bool:
        """Apply one key; False when it is time to quit."""
        for action, code in self.keys.press(key):
            if action in ("take", "hand back"):
                self.driving = action == "take"
                self.control.publish(Bool(data=self.driving))
            elif action == "command":
                stamp = self.get_clock().now().to_msg()
                m = UserCommand(trust=1.0, source=UserCommand.BUTTON, command=int(code))
                m.header.stamp = stamp
                self.command.publish(m)
                self.get_logger().info(f"sent {code.name}")
            elif action == "quit":
                self.send()
                return False
        return True

    def send(self) -> None:
        k = self.keys
        self.twist.publish(Twist(linear=Vector3(x=k.forward), angular=Vector3(z=k.turn)))


def read_key(fd: int, timeout_s: float) -> str | None:
    """One key press, with an arrow's three bytes as one key, or None after timeout_s."""
    if not select.select([fd], [], [], timeout_s)[0]:
        return None
    key = os.read(fd, 1).decode(errors="ignore")
    if key == "\x1b" and select.select([fd], [], [], 0.01)[0]:
        key += os.read(fd, 2).decode(errors="ignore")
    return key


def main(args=None):
    if not sys.stdin.isatty():
        sys.exit("the keyboard node needs a terminal of its own")
    rclpy.init(args=args)
    node = KeyboardNode()
    fd, saved = sys.stdin.fileno(), termios.tcgetattr(sys.stdin.fileno())
    print(HELP)
    try:
        tty.setcbreak(fd)
        running = True
        while running and rclpy.ok():
            key = read_key(fd, 0.1)
            if key is not None:
                running = node.press(key)
                k = node.keys
                who = "supervisor" if node.driving else "autopilot"
                print(
                    f"\r{who:10}  forward {k.forward:+.1f} m/s  turn {k.turn:+.1f} rad/s  ", end=""
                )
            node.send()  # every 0.1 s, so the watchdog only stops the chair if this node stops
            rclpy.spin_once(node, timeout_sec=0)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        print()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
