"""Where the person is, and the path they have travelled, from the phone's own sensors (#129).

    acceleration (about 30 a second) --> steps and their length --+
    compass heading -------------------------------------------------+--> position filter --> track
    GPS fix (when the phone has one) --------------------------------+

Pedestrian dead reckoning: each step moves the position by its length along the compass heading.
GPS corrects that when its fix is good, so outdoors the track follows GPS, and indoors, or with
GPS lost or switched off, it carries on from the steps. Nothing needs the internet or Wi-Fi: the
phone reaches the laptop over the USB cable, and positions are kept in metres from the start
point (east, north), with latitude and longitude once a GPS fix has been seen.

The filter is a Kalman filter with one variance for both axes: a step adds its own uncertainty
(length and heading), a GPS fix of accuracy a metres counts with variance a squared. A fix far
outside what the two allow is refused as a jump, unless several in a row agree (the person moved
without steps, as on a device with wheels).

Pure Python, no network: the demo server feeds it, and the tests feed it recorded or made-up walks.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

EARTH_R = 6_371_000.0  # m


# ---- steps


@dataclass
class Step:
    t: float  # s, the phone's clock
    length_m: float


class StepCounter:
    """Steps from the size of the acceleration, gravity included, so it works however the phone
    is held. Gravity is followed slowly and taken off; what is left is smoothed, and a step is a
    rise above `high` then a fall below `low`, at least `min_gap_s` after the last one. A step is
    given out at the next rise, or 1.5 s later when the walk stops: rises closer together than
    `min_gap_s` are shaking (a rough floor, a vehicle), and then no step is counted.

    The length of each step follows Weinberg: k * (peak - valley) ** 0.25, with the peak and the
    valley of that step's swing, between `shortest_m` and `longest_m`. `k` can be set from a walk
    of known length (calibrate)."""

    def __init__(
        self,
        k: float = 0.5,
        high: float = 0.6,
        low: float = 0.0,
        min_gap_s: float = 0.3,
        shortest_m: float = 0.3,
        longest_m: float = 1.0,
    ):
        self.k, self.high, self.low, self.min_gap_s = k, high, low, min_gap_s
        self.shortest_m, self.longest_m = shortest_m, longest_m
        self.gravity = 9.81  # m/s^2; then followed, for the sensor's own offset
        self.smooth = 0.0
        self.t: float | None = None
        self.above = False
        self.peak = -math.inf
        self.valley = math.inf
        self.last_step_t = -math.inf
        self.last_rise_t = -math.inf
        self.pending: Step | None = None
        self.count = 0

    def add(self, t: float, ax: float, ay: float, az: float) -> Step | None:
        m = math.sqrt(ax * ax + ay * ay + az * az)
        if self.t is None or t <= self.t or t - self.t > 1.0:  # first reading, or a gap
            self.t, self.smooth = t, 0.0
            self.pending = None
            return None
        dt, self.t = t - self.t, t
        self.gravity += (m - self.gravity) * min(1.0, dt / 2.0)  # follows over about 2 s
        self.smooth += (m - self.gravity - self.smooth) * dt / (0.05 + dt)  # about 3 Hz
        s = self.smooth
        if not self.above:
            self.valley = min(self.valley, s)
            if s <= self.high:
                # the last step of a walk: no next rise came, so it was not shaking
                return self._confirm() if self.pending and t - self.pending.t > 1.5 else None
            self.above, self.peak = True, s
            calm = t - self.last_rise_t >= self.min_gap_s
            self.last_rise_t = t
            if not calm:
                self.pending = None  # rises faster than anyone steps: shaking, not a step
            return self._confirm() if self.pending else None
        self.peak = max(self.peak, s)
        if s >= self.low:
            return None
        self.above = False
        swing, self.valley = self.peak - min(self.valley, 0.0), s
        if t - self.last_step_t >= self.min_gap_s:
            length = min(self.longest_m, max(self.shortest_m, self.k * swing**0.25))
            self.pending = Step(t, length)  # a step once the next rise shows it was not shaking
        return None

    def _confirm(self) -> Step:
        step, self.pending = self.pending, None
        self.last_step_t = step.t
        self.count += 1
        return step

    def calibrate(self, steps: list[Step], walked_m: float) -> float:
        """Set k so that these steps add up to the distance walked; returns the new k."""
        counted = sum(s.length_m for s in steps)
        if counted > 0 and walked_m > 0:
            self.k *= walked_m / counted
        return self.k


# ---- heading


def compass_heading(alpha: float, beta: float, gamma: float) -> float:
    """Degrees clockwise from north that the phone points to, from an absolute
    deviceorientation (W3C: rotation Z by alpha, then X by beta, then Y by gamma, from the
    earth's east-north-up frame). Held flat it is where the top edge points; held upright, as
    for the camera, it is where the back camera looks. Whichever of the two lies flatter is used."""
    a, b, g = (math.radians(v) for v in (alpha, beta, gamma))
    ca, sa = math.cos(a), math.sin(a)
    cb, sb = math.cos(b), math.sin(b)
    cg, sg = math.cos(g), math.sin(g)
    top = (-sa * cb, ca * cb)  # the phone's y axis, east and north parts
    back = (-(ca * sg + sa * sb * cg), -(sa * sg - ca * sb * cg))  # its -z axis
    e, n = top if math.hypot(*top) >= math.hypot(*back) else back
    return math.degrees(math.atan2(e, n)) % 360.0


# ---- the position filter


@dataclass
class Fix:
    t: float  # s, the laptop's clock
    lat: float
    lon: float
    accuracy_m: float
    speed: float | None = None  # m/s, when the phone gives it
    heading: float | None = None  # degrees, when the phone gives it


class Track:
    """The person's position in metres from the start (x east, y north), with its uncertainty,
    the path, the distance travelled and where the position comes from."""

    GPS, STEPS, NONE = "gps", "steps", "none"

    def __init__(
        self,
        poor_gps_m: float = 25.0,
        gate_sigmas: float = 3.0,
        agree_to_jump: int = 3,
        heading_sd_deg: float = 10.0,
        step_sd: float = 0.1,
        drift_m2_per_s: float = 0.02,
        path_every_m: float = 0.5,
        path_points: int = 5000,
    ):
        self.poor_gps_m, self.gate_sigmas = poor_gps_m, gate_sigmas
        self.agree_to_jump = agree_to_jump
        self.heading_sd = math.radians(heading_sd_deg)
        self.step_sd, self.drift = step_sd, drift_m2_per_s
        self.path_every_m = path_every_m
        self.x = self.y = 0.0
        self.var = 0.0  # m^2: the start point is where the person is, by definition
        self.origin: tuple[float, float] | None = None  # lat, lon of x = y = 0
        self.heading: float | None = None  # degrees, from the compass
        self.steps = 0
        self.step_m = 0.0  # the distance of the steps alone
        self.distance_m = 0.0  # the length of the path
        self.path: deque = deque([(0.0, 0.0, self.NONE)], maxlen=path_points)
        self.source = self.NONE
        self.last_gps_t: float | None = None
        self.last_step_t: float | None = None
        self.last_fix: Fix | None = None
        self.refused = 0  # fixes refused as jumps, in a row
        self.t: float | None = None
        self._recent: deque = deque(maxlen=50)  # (t, distance) for the speed

    # -- inputs

    def on_heading(self, degrees: float) -> None:
        self.heading = degrees % 360.0

    def on_step(self, step: Step, now: float) -> None:
        """One step along the compass heading. Without a heading yet the step is counted but does
        not move the position, since its direction is unknown."""
        self._grow(now)
        self.steps += 1
        self.step_m += step.length_m
        self.last_step_t = now
        if self.heading is None:
            return
        h = math.radians(self.heading)
        self.x += step.length_m * math.sin(h)
        self.y += step.length_m * math.cos(h)
        self.var += (self.step_sd * step.length_m) ** 2 + (step.length_m * self.heading_sd) ** 2
        if self.last_gps_t is None or now - self.last_gps_t > 3.0:
            self.source = self.STEPS
        self._extend(now)

    def on_gps(self, fix: Fix) -> bool:
        """Take a fix into the position. Returns whether it was used."""
        self._grow(fix.t)
        self.last_fix = fix
        if fix.accuracy_m is None or fix.accuracy_m > self.poor_gps_m:
            return False
        if self.origin is None:
            # the first good fix ties the track to the earth: where the track is now is this fix
            self.origin = self._shift(fix.lat, fix.lon, -self.x, -self.y)
            self.var = fix.accuracy_m**2
            self._used(fix)
            return True
        e, n = self.to_metres(fix.lat, fix.lon)
        r = fix.accuracy_m**2
        gap = math.hypot(e - self.x, n - self.y)
        if gap > self.gate_sigmas * math.sqrt(self.var + r):
            self.refused += 1
            if self.refused < self.agree_to_jump:
                return False
            self.x, self.y, self.var = e, n, r  # several fixes agree: the person did move
        else:
            gain = self.var / (self.var + r)
            self.x += gain * (e - self.x)
            self.y += gain * (n - self.y)
            self.var *= 1.0 - gain
        self._used(fix)
        return True

    # -- what it shows

    def to_metres(self, lat: float, lon: float) -> tuple[float, float]:
        lat0, lon0 = self.origin
        e = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0))
        n = math.radians(lat - lat0) * EARTH_R
        return e, n

    def latlon(self) -> tuple[float, float] | None:
        return None if self.origin is None else self._shift(*self.origin, self.x, self.y)

    def accuracy_m(self) -> float:
        return math.sqrt(self.var)

    def speed(self, now: float, window_s: float = 5.0) -> float:
        old = [(t, d) for t, d in self._recent if now - t <= window_s]
        if len(old) < 2 or old[-1][0] - old[0][0] < 1.0:
            return 0.0
        return (old[-1][1] - old[0][1]) / (now - old[0][0])

    def view(self, now: float, points: int = 600) -> dict:
        source = self.source
        if source == self.GPS and (self.last_gps_t is None or now - self.last_gps_t > 3.0):
            source = self.STEPS if self.steps else self.NONE
        where = self.latlon()
        path = list(self.path)[-points:]
        return {
            "x": round(self.x, 2),
            "y": round(self.y, 2),
            "accuracy_m": round(self.accuracy_m(), 1),
            "lat": None if where is None else round(where[0], 7),
            "lon": None if where is None else round(where[1], 7),
            "heading": None if self.heading is None else round(self.heading),
            "steps": self.steps,
            "distance_m": round(self.distance_m, 1),
            "speed": round(self.speed(now), 2),
            "source": source,
            "path": [[round(x, 2), round(y, 2), s] for x, y, s in path],
        }

    # -- inside

    def _grow(self, now: float) -> None:
        """Uncertainty grows a little with time, so a still person's GPS still counts."""
        if self.t is not None and now > self.t:
            self.var += self.drift * (now - self.t)
        self.t = now if self.t is None else max(self.t, now)

    def _used(self, fix: Fix) -> None:
        self.refused = 0
        self.last_gps_t = fix.t
        self.source = self.GPS
        self._extend(fix.t)

    def _extend(self, now: float) -> None:
        """Add the position to the path when it has moved far enough from the last point."""
        lx, ly, _ = self.path[-1]
        moved = math.hypot(self.x - lx, self.y - ly)
        if moved >= self.path_every_m:
            self.distance_m += moved
            self.path.append((self.x, self.y, self.source))
            self._recent.append((now, self.distance_m))

    def _shift(self, lat: float, lon: float, e: float, n: float) -> tuple[float, float]:
        lat2 = lat + math.degrees(n / EARTH_R)
        lon2 = lon + math.degrees(e / (EARTH_R * math.cos(math.radians(lat))))
        return lat2, lon2


class Navigator:
    """The step counter and the track together, as the demo server feeds them."""

    def __init__(self, counter: StepCounter | None = None, track: Track | None = None):
        self.counter = counter or StepCounter()
        self.track = track or Track()

    def on_accel(self, phone_t: float, ax: float, ay: float, az: float, now: float) -> Step | None:
        step = self.counter.add(phone_t, ax, ay, az)
        if step is not None:
            self.track.on_step(step, now)
        return step

    def on_orientation(
        self, alpha: float | None, beta: float | None, gamma: float | None,
        compass: float | None = None,
    ) -> None:  # fmt: skip
        if compass is not None:  # an iPhone gives the heading itself
            self.track.on_heading(compass)
        elif None not in (alpha, beta, gamma):
            self.track.on_heading(compass_heading(alpha, beta, gamma))

    def on_gps(self, fix: Fix) -> bool:
        return self.track.on_gps(fix)

    def view(self, now: float) -> dict:
        return self.track.view(now)
