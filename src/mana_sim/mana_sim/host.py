"""What the test host does with the core's drive request. Pure Python, no ROS and no Gazebo.

The host chair has two undercarriages on vertical slides (models/mana_chair/model.sdf): wheels and
tracks. Only one set is down. A drive request says which one to use (mode), whether to stop, and
how fast to go as a share of top speed. This class turns each request into the four commands the
simulated chair understands:

- the position of the wheel slide and of the track slides (down = on the ground, up = out of
  the way),
- a forward speed for the wheels and one for the tracks (the set that is up gets 0).

A change of mode first stops the chair and moves the slides; the new set drives once they are done.
A request that changes back before the slides have finished is followed at once.
"""

from dataclasses import dataclass

WHEELS, TRACKS = 0, 1  # the Mode codes of DriveRequest

TOP_SPEED = 1.2  # m/s at speed limit 1.0, the walking speed the scripted world uses
SWITCH_S = 2.5  # how long the slides need to change the undercarriage

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


class Host:
    def __init__(self, mode: int = WHEELS):
        self.mode = mode  # the undercarriage that is down (or going down)
        self._switching_until: float | None = None

    def lifts(self, mode: int) -> tuple[float, float]:
        return (WHEELS_DOWN, TRACKS_UP) if mode == WHEELS else (WHEELS_UP, TRACKS_DOWN)

    def update(self, mode: int, stop: bool, speed_limit: float, now: float) -> HostCommand:
        if mode != self.mode:
            self.mode = mode
            self._switching_until = now + SWITCH_S
        switching = self._switching_until is not None and now < self._switching_until
        if not switching:
            self._switching_until = None
        wheel_lift, track_lift = self.lifts(self.mode)
        speed = 0.0 if stop or switching else max(0.0, min(speed_limit, 1.0)) * TOP_SPEED
        return HostCommand(
            wheel_lift,
            track_lift,
            speed if self.mode == WHEELS else 0.0,
            speed if self.mode == TRACKS else 0.0,
        )

    def halt(self) -> HostCommand:
        """Nothing is asking for anything: stand still on the current undercarriage."""
        wheel_lift, track_lift = self.lifts(self.mode)
        return HostCommand(wheel_lift, track_lift, 0.0, 0.0)
