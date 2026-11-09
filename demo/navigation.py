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
outside what the two allow is refused as a jump, unless several in a row agree with each other
(the person moved without steps, as on a device with wheels).

Pure Python, no network: the demo server feeds it, and the tests feed it recorded or made-up walks.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

EARTH_R = 6_371_000.0  # m
COMPASS = ("north", "north-east", "east", "south-east", "south", "south-west", "west", "north-west")


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
            # start over: a swing half before the gap is not a step
            if self.t is not None and t < self.t:
                # the phone's clock started again (the page was reloaded): the last rise and
                # step are on the old clock, and would hold back every step until it caught up
                self.last_rise_t = self.last_step_t = -math.inf
            self.t, self.smooth, self.pending = t, 0.0, None
            self.above, self.peak, self.valley = False, -math.inf, math.inf
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
        self.path: deque = deque([(0.0, 0.0, self.NONE, None)], maxlen=path_points)  # x, y, src, t
        self.marks: dict[str, tuple[float, float]] = {}  # places the person marked, by name
        self.added = 0  # path points added since the start, the first one not counted
        self.marked_at: dict[str, int] = {}  # each mark's place in the path, as self.added then
        self.source = self.NONE
        self.last_gps_t: float | None = None
        self.last_step_t: float | None = None
        self.last_fix: Fix | None = None
        self.refused: deque = deque(maxlen=agree_to_jump)  # fixes refused as jumps, in a row
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
            self.refused.append((e, n, fix.accuracy_m))
            agreed = self._agreed()
            if agreed is None:
                return False
            # several fixes agree: the person is there. After a stretch on steps alone (out of a
            # building) the jump puts right where the steps had drifted to: not walked, so no
            # distance. Otherwise it is how the track follows a device rolling with no steps,
            # whose fixes leave the gate as they come: that is travel (Waqas, #150)
            corrects_steps = self.source == self.STEPS
            self.x, self.y, self.var = agreed
            self._used(fix, walked=not corrects_steps)
            return True
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
            # where x = y = 0 is on the earth, once a GPS fix has said: the street map's anchor
            "origin": None if self.origin is None else [round(v, 7) for v in self.origin],
            "heading": None if self.heading is None else round(self.heading),
            "steps": self.steps,
            "distance_m": round(self.distance_m, 1),
            "speed": round(self.speed(now), 2),
            "source": source,
            "path": [[round(x, 2), round(y, 2), s] for x, y, s, _ in path],
            "marks": {k: [round(x, 2), round(y, 2)] for k, (x, y) in self.marks.items()},
        }

    # -- marked places, and the way back to them

    def mark(self, name: str = "mark") -> None:
        """Remember where the person is now, and where that is in the path."""
        self.marks[name] = (self.x, self.y)
        self.marked_at[name] = self.added

    def route_back(self, name: str = "mark", near_m: float = 1.5, tolerance_m: float = 1.0):
        """The way back to a marked place along the path walked, as legs (metres, bearing in
        degrees), from where the person is now. Indoors the straight line goes through walls; the
        path walked is known to be passable. Loops are cut where the path came back within near_m
        of itself (into a room and out again), then the path is simplified into straight legs.
        None when not marked, or when the path from the mark is no longer kept."""
        if name not in self.marks:
            return None
        first = self.added + 1 - len(self.path)  # the place in the path of self.path[0]
        since = self.marked_at[name] - first + 1
        if since < 0:
            return None
        walked = [self.marks[name]] + [(x, y) for x, y, _, _ in list(self.path)[since:]]
        back = _cut_loops([(self.x, self.y)] + walked[::-1], near_m)
        points = _simplify(back, tolerance_m)
        moves = []  # (east, north) of each leg; a bend of under 25 degrees is not a turn
        for (x1, y1), (x2, y2) in zip(points, points[1:], strict=False):
            east, north = x2 - x1, y2 - y1
            if moves and _bend(moves[-1], (east, north)) < 25.0:
                moves[-1] = (moves[-1][0] + east, moves[-1][1] + north)
            elif east or north:
                moves.append((east, north))
        return [(math.hypot(e, n), math.degrees(math.atan2(e, n)) % 360.0) for e, n in moves]

    def way_to(self, name: str = "mark") -> tuple[float, float | None, str] | None:
        """How far the marked place is, its direction relative to where the person faces (degrees,
        clockwise; None without a compass), and its compass direction. None when not marked."""
        if name not in self.marks:
            return None
        mx, my = self.marks[name]
        dx, dy = mx - self.x, my - self.y
        bearing = math.degrees(math.atan2(dx, dy)) % 360.0
        turn = None if self.heading is None else (bearing - self.heading) % 360.0
        return math.hypot(dx, dy), turn, COMPASS[round(bearing / 45.0) % 8]

    # -- the walk, to keep

    def points(self) -> list[tuple[float, float, str, float | None]]:
        """The path with the current position: x, y, source, time."""
        return list(self.path) + [(self.x, self.y, self.source, self.t)]

    # -- inside

    def _grow(self, now: float) -> None:
        """Uncertainty grows a little with time, so a still person's GPS still counts."""
        if self.t is not None and now > self.t:
            self.var += self.drift * (now - self.t)
        self.t = now if self.t is None else max(self.t, now)

    def _agreed(self) -> tuple[float, float, float] | None:
        """Where the refused fixes put the person, when there are agree_to_jump of them in a row
        and each lies within twice its own accuracy of their mean: then the person moved without
        steps. Fixes scattered in different directions (multipath) never agree."""
        if len(self.refused) < self.agree_to_jump:
            return None
        me = sum(e for e, _, _ in self.refused) / len(self.refused)
        mn = sum(n for _, n, _ in self.refused) / len(self.refused)
        if any(math.hypot(e - me, n - mn) > 2.0 * a for e, n, a in self.refused):
            return None
        var = max(a for _, _, a in self.refused) ** 2
        return me, mn, var

    def _used(self, fix: Fix, walked: bool = True) -> None:
        self.refused.clear()
        self.last_gps_t = fix.t
        self.source = self.GPS
        self._extend(fix.t, walked)

    def _extend(self, now: float, walked: bool = True) -> None:
        """Add the position to the path when it has moved far enough from the last point, and
        what it moved to the distance walked unless the track was put right by GPS."""
        lx, ly, _, _ = self.path[-1]
        moved = math.hypot(self.x - lx, self.y - ly)
        if moved >= self.path_every_m:
            self.distance_m += moved if walked else 0.0
            self.path.append((self.x, self.y, self.source, now))
            self.added += 1
            self._recent.append((now, self.distance_m))

    def _shift(self, lat: float, lon: float, e: float, n: float) -> tuple[float, float]:
        lat2 = lat + math.degrees(n / EARTH_R)
        lon2 = lon + math.degrees(e / (EARTH_R * math.cos(math.radians(lat))))
        return lat2, lon2


def _cut_loops(points: list, near_m: float) -> list:
    """A stretch that left a point and came back within near_m of it (a room, a dead end) is
    skipped. It counts as a loop only when the way round was more than three times near_m, so
    the next points along, or the two sides of a corner, are not cut."""
    along = [0.0]
    for a, b in zip(points, points[1:], strict=False):
        along.append(along[-1] + math.dist(a, b))
    # a grid of near_m cells: a point within near_m is in the same cell or one of the 8 around,
    # so each point looks at a few others, not all (the server waits for this; Waqas, #158)
    cells: dict[tuple[int, int], list[int]] = {}
    for k, (x, y) in enumerate(points):
        cells.setdefault((math.floor(x / near_m), math.floor(y / near_m)), []).append(k)
    out, i = [], 0
    while i < len(points):
        out.append(points[i])
        cx, cy = math.floor(points[i][0] / near_m), math.floor(points[i][1] / near_m)
        back = [
            k
            for dx in (-1, 0, 1)
            for dy in (-1, 0, 1)
            for k in cells.get((cx + dx, cy + dy), ())
            if k > i
            and math.dist(points[k], points[i]) <= near_m
            and along[k] - along[i] > 3 * near_m
        ]
        i = max(back) if back else i + 1  # always forward: later points come after i
    return out


def _simplify(points: list, tolerance_m: float) -> list:
    """Douglas-Peucker: the fewest points that keep the line within tolerance_m of all of them."""
    if len(points) < 3:
        return list(points)
    (x1, y1), (x2, y2) = points[0], points[-1]
    length = math.hypot(x2 - x1, y2 - y1)

    def off(p):
        if length == 0.0:
            return math.dist(p, points[0])
        return abs((x2 - x1) * (y1 - p[1]) - (x1 - p[0]) * (y2 - y1)) / length

    far = max(range(1, len(points) - 1), key=lambda k: off(points[k]))
    if off(points[far]) <= tolerance_m:
        return [points[0], points[-1]]
    return _simplify(points[: far + 1], tolerance_m)[:-1] + _simplify(points[far:], tolerance_m)


def _bend(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The angle in degrees between two moves (east, north)."""
    turn = math.degrees(math.atan2(b[0], b[1]) - math.atan2(a[0], a[1])) % 360.0
    return min(turn, 360.0 - turn)


def _hour(degrees: float) -> int:
    return round(degrees / 30.0) % 12 or 12


def route_text(legs: list | None, heading: float | None, name: str = "mark") -> str | None:
    """The way back along the path, as the user hears it: the first leg from where the person
    faces, then the next turn from the way the first leg goes, as clock faces; compass directions
    without a compass. None when there is no route or it is one straight leg: the straight line
    says the same, more shortly."""
    total = sum(d for d, _ in legs or ())
    if not legs or len(legs) < 2 or total < 2.0:  # the straight line, or "You are at the mark"
        return None
    (d0, b0), rest = legs[0], legs[1:]
    if heading is None:
        first = f"{d0:.0f} metres to the {COMPASS[round(b0 / 45.0) % 8]}"
    else:
        first = f"{d0:.0f} metres at {_hour((b0 - heading) % 360.0)} o'clock"
    text = f"Back along your path, the {name} is {total:.0f} metres: {first}"
    if rest:
        d1, b1 = rest[0]
        if heading is None:
            text += f", then {d1:.0f} metres to the {COMPASS[round(b1 / 45.0) % 8]}"
        else:
            text += f", then turn to {_hour((b1 - b0) % 360.0)} o'clock for {d1:.0f} metres"
    if len(rest) > 1:
        text += f", then {len(rest) - 1} more turn{'s' if len(rest) > 2 else ''}"
    return text + "."


def way_text(way: tuple[float, float | None, str] | None, name: str = "mark") -> str:
    """The way to a marked place, as the user hears it. The direction is a clock face, 12 o'clock
    straight ahead, the way orientation and mobility teaching gives directions; without a compass
    it is the compass direction."""
    if way is None:
        return f"No {name} yet. Say mark this place first."
    distance, turn, compass = way
    if distance < 2.0:
        return f"You are at the {name}."
    metres = f"{distance:.0f} metres"
    if turn is None:
        return f"The {name} is {metres} to the {compass}."
    hour = round(turn / 30.0) % 12 or 12
    return f"The {name} is {metres} away, at {hour} o'clock."


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


# ---- a walk to keep: GPX for map apps, GeoJSON for anything else


def _iso(wall: float | None) -> str | None:
    import datetime as dt

    if wall is None:
        return None
    return dt.datetime.fromtimestamp(wall, dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_gpx(track: Track, wall_of=lambda t: None) -> str | None:
    """The walk as GPX 1.1, with the marks as waypoints. None until a GPS fix has tied the track
    to the earth: GPX needs latitude and longitude. wall_of turns the track's clock into Unix
    time."""
    from xml.sax.saxutils import escape

    if track.origin is None:
        return None
    pts = []
    for x, y, src, t in track.points():
        lat, lon = track._shift(*track.origin, x, y)
        when = _iso(wall_of(t)) if t is not None else None
        time = f"<time>{when}</time>" if when else ""
        pts.append(f'<trkpt lat="{lat:.7f}" lon="{lon:.7f}">{time}<type>{src}</type></trkpt>')
    wpts = []
    for name, (x, y) in track.marks.items():
        lat, lon = track._shift(*track.origin, x, y)
        wpts.append(f'<wpt lat="{lat:.7f}" lon="{lon:.7f}"><name>{escape(name)}</name></wpt>')
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<gpx version="1.1" creator="MANA" xmlns="http://www.topografix.com/GPX/1/1">\n'
        + "".join(w + "\n" for w in wpts)
        + "<trk><name>MANA walk</name><trkseg>\n"
        + "".join(p + "\n" for p in pts)
        + "</trkseg></trk>\n</gpx>\n"
    )


def to_geojson(track: Track, wall_of=lambda t: None) -> dict:
    """The walk as GeoJSON: a line and a point per mark. With a GPS fix the coordinates are
    longitude and latitude, as GeoJSON wants; without one they are metres east and north of the
    start, and the collection says so ("crs_note"), since a walk indoors never had a fix."""
    geo = track.origin is not None

    def where(x, y):
        if not geo:
            return [round(x, 2), round(y, 2)]
        lat, lon = track._shift(*track.origin, x, y)
        return [round(lon, 7), round(lat, 7)]

    pts = track.points()
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [where(x, y) for x, y, _, _ in pts]},
            "properties": {
                "name": "MANA walk",
                "distance_m": round(track.distance_m, 1),
                "steps": track.steps,
                "sources": [s for _, _, s, _ in pts],
                "times": [_iso(wall_of(t)) if t is not None else None for _, _, _, t in pts],
            },
        }
    ]
    for name, (x, y) in track.marks.items():
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": where(x, y)},
                "properties": {"name": name},
            }
        )
    out = {"type": "FeatureCollection", "features": features}
    if not geo:
        out["crs_note"] = "metres east and north of the start: no GPS fix during this walk"
    return out
