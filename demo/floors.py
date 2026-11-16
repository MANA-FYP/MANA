"""Which floor the person is on, from the phone a web page can read (no barometer: Chrome gives a
web page no pressure sensor). Pure Python, no models.

Motion only measures changes ("up one floor"), never where it started. So the floor is unknown
("?") until there is a reason for it, and meanwhile the page shows the floors moved since the
start. The start comes from:
- walking in from outside: GPS was good outdoors and is lost on going in, so the person is
  assumed to be on the ground floor (labelled as an assumption: some entrances are higher);
- the supervisor setting it (one tap), which also corrects it at any time.

Three ways a floor changes, each written down with where it came from:
- a lift: the phone, held still (no steps), feels the lift speed up and then slow down. The
  vertical acceleration, integrated twice from the start of the ride to its end, is the height
  travelled, and a floor is `floor_h` metres.
- stairs: the camera sees stairs up or down ahead, then the person climbs on. Each step on the
  stairs is one riser (`riser_m`); a flight that rises most of a floor's height is a floor.
- by hand: the supervisor sets the floor (the start, or a correction).

GPS altitude is kept beside it, but only as a reference: a phone's GPS height is often 10 m or more
off, more than a floor. Everything here is an estimate, and the page says so.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field

G = 9.81


@dataclass
class Ride:
    """A lift ride being measured: velocity and height from its start, and the pulses felt."""

    start: float
    v: float = 0.0
    d: float = 0.0
    up_pulse: bool = False
    down_pulse: bool = False
    quiet_since: float | None = None


@dataclass
class Flight:
    """A stairs flight being counted: the direction the camera saw, and the steps since."""

    seen: float
    direction: int
    steps_at: int
    last_step: float
    steps: int = 0


@dataclass
class FloorTracker:
    floor: int | None = None  # None: not known yet (see above)
    floor_h: float = 3.2  # metres from one floor to the next
    riser_m: float = 0.17  # one stair step
    move_a: float = 0.2  # m/s^2 of vertical acceleration felt as the lift moving
    quiet_a: float = 0.1  # below this the lift runs evenly, or has stopped
    events: deque = field(default_factory=lambda: deque(maxlen=12))
    trace: deque = field(default_factory=lambda: deque(maxlen=400))  # (t, vertical acc, height)
    gps_alt: tuple[float, float | None, float] | None = None  # (altitude, its accuracy, t)
    gps_alt0: float | None = None  # the altitude where the walk began, for "metres above start"

    def __post_init__(self):
        self._g: list[float] | None = None  # gravity in the phone's frame, from a slow average
        self._offset: float | None = None  # server clock minus phone clock
        self._t: float | None = None
        self._last_step_t = -1e9
        self._steps = 0
        self._ride: Ride | None = None
        self._flight: Flight | None = None
        self._loud_since: float | None = None
        self._trace_t = -1e9
        self._recent: deque = deque()  # (t, vertical acceleration) over the last second
        self.moved = 0  # floors moved since the start, known or not
        self.outdoors = False  # GPS is good: outside, no floor
        self._outdoor_t: float | None = None

    # -- what the server feeds in

    def on_motion(
        self, t: float, ax: float, ay: float, az: float, now: float | None = None
    ) -> None:
        """One reading of acceleration with gravity (m/s^2). t: seconds on the phone's clock,
        which spaces the readings evenly; now: the server's clock when it came, which the steps
        and the camera use. The phone's time is moved onto the server's by the smallest offset
        seen (the reading that waited least on the network)."""
        if now is not None:
            off = now - t
            self._offset = off if self._offset is None else min(self._offset, off)
            t += self._offset
        dt = 0.0 if self._t is None else min(max(t - self._t, 0.0), 0.2)
        self._t = t
        if self._g is None:
            self._g = [ax, ay, az]
            return
        gx, gy, gz = self._g
        gn = math.sqrt(gx * gx + gy * gy + gz * gz) or G
        av = (ax * gx + ay * gy + az * gz) / gn - gn  # up is +: along gravity's opposite
        # walking shakes the phone; a lift pushes it smoothly. Judged on the phone's own readings:
        # the track's steps arrive late, in a batch, once a walk is confirmed
        self._recent.append((t, av))
        while self._recent and t - self._recent[0][0] > 1.0:
            self._recent.popleft()
        vals = [a for _, a in self._recent]
        walking = max(vals) - min(vals) > 1.2
        # gravity follows the phone's tilt slowly (walking swings average out within seconds),
        # but not while a lift pushes on it
        if self._ride is None and (walking or abs(av) < self.move_a):
            k = dt / (3.0 + dt)
            self._g = [gx + (ax - gx) * k, gy + (ay - gy) * k, gz + (az - gz) * k]
        self._lift(t, dt, av, walking)
        if t - self._trace_t >= 0.25:
            self._trace_t = t
            ride_d = self._ride.d if self._ride else 0.0
            self.trace.append(
                (round(t, 2), round(av, 3), round(self.moved * self.floor_h + ride_d, 2))
            )

    def on_steps(self, t: float, steps: int) -> None:
        """The step count of the track (it only grows while walking)."""
        if steps > self._steps:
            self._last_step_t = t
            if self._flight is not None:
                self._flight.last_step = t
        self._steps = steps

    def on_stairs(self, t: float, direction: int) -> None:
        """The camera sees stairs ahead: +1 up, -1 down. Counting starts from the next step."""
        if self._flight is None or self._flight.direction != direction:
            self._flight = Flight(t, direction, self._steps, t)
        else:
            self._flight.seen = t

    def on_gps_altitude(self, t: float, altitude: float | None, accuracy: float | None) -> None:
        if altitude is None or not math.isfinite(altitude):
            return
        self.gps_alt = (altitude, accuracy, t)
        if self.gps_alt0 is None:
            self.gps_alt0 = altitude

    def set_floor(
        self, t: float, floor: int, why: str = "set by hand", source: str = "hand"
    ) -> None:
        self.floor = int(floor)
        self._ride = self._flight = None
        self._event(t, f"Floor {label(self.floor)}, {why}", source)

    def on_position_source(self, t: float, source: str) -> None:
        """The track's source: "gps" outdoors with a good fix, "steps" when GPS is lost. Going
        from good GPS to none, with the floor not known yet, is walking in at the ground floor."""
        if source == "gps":
            self.outdoors, self._outdoor_t = True, t
            return
        was_out = self.outdoors and self._outdoor_t is not None and t - self._outdoor_t < 30.0
        self.outdoors = False
        if was_out and self.floor is None:
            self.set_floor(t, 0, "walked in from outside (assumed ground floor)", "gps")

    def tick(self, t: float) -> None:
        """Close a stairs flight once the steps have stopped, or when it was only seen."""
        f = self._flight
        if f is None:
            return
        f.steps = self._steps - f.steps_at
        stopped = f.steps > 0 and t - f.last_step > 4.0
        never = f.steps == 0 and t - f.seen > 30.0
        if not (stopped or never or t - f.seen > 120.0):
            return
        self._flight = None
        rise = f.steps * self.riser_m
        if rise >= 0.6 * self.floor_h:
            n = max(1, round(rise / self.floor_h))
            self._change(
                t,
                f.direction * n,
                f"{f.steps} steps {'up' if f.direction > 0 else 'down'} the stairs",
                "stairs",
            )
        elif f.steps:
            self._event(t, f"{f.steps} steps on stairs: less than a floor", "stairs")

    # -- the lift

    def _lift(self, t: float, dt: float, av: float, walking: bool) -> None:
        r = self._ride
        if r is None:
            if walking or abs(av) < self.move_a:
                self._loud_since = None
                return
            self._loud_since = self._loud_since if self._loud_since is not None else t
            if t - self._loud_since >= 0.3:  # felt for a moment: not a bump
                self._ride = r = Ride(start=self._loud_since)
                r.v = av * (t - self._loud_since)  # the part of the push already felt
            return
        if walking or t - r.start > 90.0:  # walked off, or never stopped: not a lift
            self._ride = None
            return
        r.v += av * dt
        r.d += r.v * dt
        if av > self.move_a:
            r.up_pulse = True
        elif av < -self.move_a:
            r.down_pulse = True
        if abs(av) < self.quiet_a:
            r.quiet_since = r.quiet_since if r.quiet_since is not None else t
        else:
            r.quiet_since = None
        # over once it has sped up and slowed down, and stayed quiet a moment after
        if r.up_pulse and r.down_pulse and r.quiet_since is not None and t - r.quiet_since > 1.5:
            self._ride = None
            if abs(r.d) >= 0.6 * self.floor_h:
                n = max(1, round(abs(r.d) / self.floor_h))
                way = 1 if r.d > 0 else -1
                self._change(
                    t, way * n, f"lift {'up' if way > 0 else 'down'} about {abs(r.d):.1f} m", "lift"
                )
            else:
                self._event(t, f"moved {r.d:+.1f} m: less than a floor", "lift")

    def _change(self, t: float, by: int, why: str, source: str) -> None:
        self.moved += by
        if self.floor is None:
            self._event(t, f"{by:+d} floor{'s' if abs(by) > 1 else ''}: {why}", source)
            return
        self.floor += by
        self._event(t, f"Floor {label(self.floor)}: {why}", source)

    def _event(self, t: float, text: str, source: str) -> None:
        self.events.append({"t": round(t, 1), "text": text, "source": source})

    def _shaky(self) -> bool:
        vals = [a for _, a in self._recent]
        return bool(vals) and max(vals) - min(vals) > 1.2

    def view(self, t: float) -> dict:
        mode = "still"
        if self._ride is not None:
            mode = "lift"
        elif self._flight is not None:
            mode = "stairs up" if self._flight.direction > 0 else "stairs down"
        elif t - self._last_step_t < 2.0 or self._shaky():
            mode = "walking"
        alt = None
        if self.gps_alt is not None:
            a, acc, at = self.gps_alt
            alt = {
                "m": round(a, 1),
                "accuracy_m": None if acc is None else round(acc, 1),
                "above_start_m": round(a - self.gps_alt0, 1),
                "age_s": round(t - at, 1),
            }
        return {
            "floor": self.floor,
            "label": "?" if self.floor is None else label(self.floor),
            "known": self.floor is not None,
            "outdoors": self.outdoors,
            "moved": self.moved,
            "floor_h": self.floor_h,
            # metres up from the start, measured: the floors climbed, plus a lift ride under way
            "height_m": round(self.moved * self.floor_h + (self._ride.d if self._ride else 0.0), 2),
            "mode": mode,
            "flight_steps": None if self._flight is None else self._steps - self._flight.steps_at,
            "events": list(self.events),
            "trace": [list(p) for p in self.trace],
            "gps_altitude": alt,
        }


def label(floor: int) -> str:
    """G for the ground floor, B1 and below underground, then 1, 2 ..."""
    return "G" if floor == 0 else f"B{-floor}" if floor < 0 else str(floor)
