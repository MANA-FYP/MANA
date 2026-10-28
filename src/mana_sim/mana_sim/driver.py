"""Who drives the test host: the supervisor or the autopilot, and what the supervisor's keys do.
Pure Python, no ROS.

Both are drivers; MANA's drive request caps whichever one drives (host.py). Taking the controls is
a latch: once the supervisor drives, the autopilot is not followed until the supervisor hands
back, so the two never fight over the chair. A driver whose commands stop for watchdog_s gives no
motion: the chair stands still, it never falls back to the other driver by itself.

Taking the steering does not put MANA's core in manual mode. In manual mode the core leaves the
mode and speed to the user and skips its own changes to tracks and its slow-downs, which are what
a test drive is meant to show. The supervisor can still send every user command (M for manual,
A for automatic, and the rest), as the user would by voice or touch.
"""

from dataclasses import dataclass

from mana_core.decision import Command  # the same codes as UserCommand

SUPERVISOR, AUTOPILOT = "supervisor", "autopilot"
WATCHDOG_S = 0.5  # as the host's: a driver silent this long gives no motion
# supervisor/control is latched (transient local), so a node that starts late still gets it

FORWARD_STEP, TURN_STEP = 0.1, 0.2  # m/s and rad/s per key press
FORWARD_RANGE, TURN_MAX = (-0.3, 1.2), 1.0  # the host caps these again, by MANA's drive request

# keys that send a user command to the core
COMMAND_KEYS = {
    " ": Command.STOP,
    "g": Command.GO,
    "y": Command.YES,
    "n": Command.NO,
    "m": Command.MANUAL,
    "a": Command.AUTOMATIC,
    "w": Command.WHEELS,
    "t": Command.TRACKS,
    "+": Command.SPEED_UP,
    "-": Command.SLOW_DOWN,
}
UP, DOWN, RIGHT, LEFT = "\x1b[A", "\x1b[B", "\x1b[C", "\x1b[D"  # what a terminal sends for arrows

HELP = """MANA test drive: the supervisor's keys
  arrows  drive: up and down change speed, left and right change the turn (takes the controls)
  x       stand still (keeps the controls)
  h       hand the controls back to the autopilot
  space   STOP          g  GO          y / n  YES / NO to MANA's question
  m / a   MANUAL / AUTOMATIC           w / t  WHEELS / TRACKS      + / -  speed up / down
  q       quit"""


class DriverSelect:
    """Which driver the host follows, and that driver's latest command."""

    def __init__(self, watchdog_s: float = WATCHDOG_S):
        self.watchdog_s = watchdog_s
        self.driver = AUTOPILOT
        self._last: dict[str, tuple[tuple[float, float], float]] = {}

    def take_over(self) -> None:
        self.driver = SUPERVISOR

    def hand_back(self) -> None:
        self.driver = AUTOPILOT

    def command(self, who: str, forward: float, turn: float, now: float) -> None:
        self._last[who] = ((forward, turn), now)

    def output(self, now: float) -> tuple[float, float]:
        """(forward, turn) for the host: the driving driver's, or no motion if it went quiet."""
        last = self._last.get(self.driver)
        if last is None or now - last[1] > self.watchdog_s:
            return (0.0, 0.0)
        return last[0]


@dataclass
class Keys:
    """The supervisor's motion, and what each key press changes."""

    forward: float = 0.0
    turn: float = 0.0

    def press(self, key: str) -> list[tuple[str, Command | None]]:
        """Apply one key. Returns what to send, in order: ("take", None), ("hand back", None),
        ("command", Command) or ("quit", None)."""
        key = key if key.startswith("") else key.lower()
        out: list[tuple[str, Command | None]] = []
        if key in (UP, DOWN, LEFT, RIGHT):
            if key in (UP, DOWN):
                step = FORWARD_STEP if key == UP else -FORWARD_STEP
                low, high = FORWARD_RANGE
                self.forward = round(max(low, min(self.forward + step, high)), 2)
            else:
                step = TURN_STEP if key == LEFT else -TURN_STEP
                self.turn = round(max(-TURN_MAX, min(self.turn + step, TURN_MAX)), 2)
            out.append(("take", None))
        elif key == "x":
            self.forward = self.turn = 0.0
        elif key == "h":
            self.forward = self.turn = 0.0
            out.append(("hand back", None))
        elif key == "q":
            self.forward = self.turn = 0.0
            out.append(("quit", None))
        elif key in COMMAND_KEYS:
            if COMMAND_KEYS[key] == Command.STOP:
                self.forward = self.turn = 0.0
            out.append(("command", COMMAND_KEYS[key]))
        return out
