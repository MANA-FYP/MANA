"""What the test host does with the core's drive request. Pure Python, no ROS and no Gazebo.

The host chair has two undercarriages on vertical slides (models/mana_chair/model.sdf): wheels and
tracks. Only one set is down. A drive request says which one to use (mode), whether to stop, and
how fast to go as a share of top speed. This class turns each request into the four commands the
simulated chair understands:

- the position of the wheel slide and of the track slides (down = on the ground, up = out of
  the way),
- a forward speed and a turn rate for the wheels, and the same for the tracks (the set that is up
  gets 0).

With no driver, the chair drives straight at the speed limit, as the scripted scenarios need. With a
driver (the supervisor's keyboard, or the autopilot), the driver steers and sets the speed, and the
drive request caps it: never faster forward than the speed limit, always in MANA's mode, and after a
stop only turning on the spot or reversing slowly, so the chair can move away from what stopped it
(docs/decisions.md, D3).

A change of mode first stops the chair and moves the slides; the new set drives once they are done.
A request that changes back before the slides have finished is followed at once.
"""

import math
from dataclasses import dataclass

WHEELS, TRACKS = 0, 1  # the Mode codes of DriveRequest

TOP_SPEED = 1.2  # m/s at speed limit 1.0, the walking speed the scripted world uses
SWITCH_S = 2.5  # how long the slides need to change the undercarriage (simulation seconds)
WATCHDOG_S = 0.5  # no request for this long: the chair stops
TURN_MAX = 1.0  # rad/s, the fastest turn a driver gets (the wheels' plugin allows 1.5)
REVERSE_MAX = 0.3  # m/s: nothing looks behind the chair, so it reverses slowly

# wheel slide: 0 = on the ground; up is high enough to clear stairs
WHEELS_DOWN, WHEELS_UP = 0.0, 0.35
# track slides
TRACKS_DOWN, TRACKS_UP = 0.0, 0.15


@dataclass(frozen=True)
class HostCommand:
    wheel_lift: float
    track_lift: float
    wheels_mps: float
    tracks_mps: float
    wheels_turn: float = 0.0  # rad/s, positive turns left
    tracks_turn: float = 0.0


class Host:
    def __init__(self, mode: int = WHEELS):
        self.mode = mode  # the undercarriage that is down (or going down)
        self._switching_until: float | None = None

    def lifts(self, mode: int) -> tuple[float, float]:
        return (WHEELS_DOWN, TRACKS_UP) if mode == WHEELS else (WHEELS_UP, TRACKS_DOWN)

    def update(
        self,
        mode: int,
        stop: bool,
        speed_limit: float,
        now: float,
        driver: tuple[float, float] | None = None,
    ) -> HostCommand:
        """driver is (forward m/s, turn rad/s) from whoever drives, or None to drive straight at
        the speed limit."""
        if mode not in (WHEELS, TRACKS):
            return self.halt()  # a mode this host does not have: stand still, change nothing
        if mode != self.mode:
            self.mode = mode
            self._switching_until = now + SWITCH_S
        switching = self._switching_until is not None and now < self._switching_until
        if not switching:
            self._switching_until = None
        wheel_lift, track_lift = self.lifts(self.mode)
        # a speed that is not a number (NaN) is no speed
        limit = max(0.0, min(speed_limit, 1.0)) if math.isfinite(speed_limit) else 0.0
        if switching:
            speed = turn = 0.0  # the slides are moving
        elif driver is None:
            speed, turn = (0.0 if stop else limit * TOP_SPEED), 0.0
        else:
            forward, turn = (v if math.isfinite(v) else 0.0 for v in driver)
            top = 0.0 if stop else limit * TOP_SPEED  # after a stop: back off or turn, no forward
            speed = max(-REVERSE_MAX, min(forward, top))
            turn = max(-TURN_MAX, min(turn, TURN_MAX))
        on_wheels = self.mode == WHEELS
        return HostCommand(
            wheel_lift,
            track_lift,
            speed if on_wheels else 0.0,
            0.0 if on_wheels else speed,
            turn if on_wheels else 0.0,
            0.0 if on_wheels else turn,
        )

    def follow(
        self,
        request: tuple[int, bool, float] | None,
        received_at: float,
        now: float,
        watchdog_s: float = WATCHDOG_S,
        driver: tuple[float, float] | None = None,
        driver_at: float = -math.inf,
        follow_driver: bool = False,
    ) -> HostCommand:
        """The command for this moment. request is (mode, stop, speed_limit) of the last drive
        request, or None; received_at is when it arrived. An old request is never replayed: after
        watchdog_s without a new one the chair stands still. With follow_driver, the chair moves
        only as the driver asks; a driver silent for watchdog_s (keys let go) means stand still,
        never "drive on by itself"."""
        if request is None or not 0.0 <= now - received_at <= watchdog_s:  # also a clock gone back
            return self.halt()
        mode, stop, speed_limit = request
        if follow_driver:
            driver = (
                driver
                if driver is not None and 0.0 <= now - driver_at <= watchdog_s
                else (0.0, 0.0)
            )
        else:
            driver = None
        return self.update(int(mode), bool(stop), float(speed_limit), now, driver)

    def halt(self) -> HostCommand:
        """Nothing is asking for anything: stand still on the current undercarriage."""
        wheel_lift, track_lift = self.lifts(self.mode)
        return HostCommand(wheel_lift, track_lift, 0.0, 0.0)
