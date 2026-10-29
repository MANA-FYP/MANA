"""A scripted world for the decision core: a chair on a straight route, and the reports its sensors
would send. Pure Python, no ROS. scenarios.py builds the routes; evaluate.py runs them in fast time
and the scenario node runs them live against the core node.

The sensor model follows the design: the camera names the ground but cannot measure distance, the
3D camera measures distance and step height, the lidar sees obstacles, and the IMU gives the tilt.
A full-system route also has the position and alert-link modules: the position module reports GPS,
or the lidar map when GPS is lost (it switches at once here; the real node takes up to 2 s), and the
link module reports Wi-Fi and Bluetooth.
"""

import math
from dataclasses import dataclass

from mana_core.decision import (
    Ground,
    GroundReport,
    Link,
    LinkReport,
    Mode,
    MotionReport,
    ObstacleReport,
    PositionReport,
    PositionSource,
    Source,
)

TOP_SPEED = 1.2  # m/s at speed limit 1.0, walking speed
RANGE_M = {Source.CAMERA: 5.0, Source.DEPTH_CAMERA: 4.0, Source.LIDAR: 8.0}
TRUST = 0.9
STEPS = {Ground.STAIRS_UP, Ground.STAIRS_DOWN, Ground.CURB}
BOTH_CAMERAS = frozenset({Source.CAMERA, Source.DEPTH_CAMERA})


@dataclass(frozen=True)
class Stretch:
    """Ground other than flat, from start_m to end_m along the route."""

    start_m: float
    end_m: float
    ground: Ground
    pitch_rad: float = 0.0  # tilt of a chair on it
    height_m: float = math.nan
    seen_by: frozenset = BOTH_CAMERAS
    real: bool = True  # False for something that only looks like it, such as a picture of stairs


@dataclass(frozen=True)
class Obstacle:
    at_m: float
    label: str
    gone_s: float = math.inf  # when it moves out of the way, such as a person who steps aside


@dataclass(frozen=True)
class Fault:
    """A sensor fault while the chair is between from_m and to_m. trust=None means no reports."""

    sensor: str  # "camera", "depth", "lidar", "imu", "gps", "lidar_map", "wifi" or "bluetooth"
    from_m: float = 0.0
    to_m: float = math.inf
    trust: float | None = None


@dataclass(frozen=True)
class Route:
    length_m: float
    stretches: tuple[Stretch, ...] = ()
    obstacles: tuple[Obstacle, ...] = ()
    faults: tuple[Fault, ...] = ()
    full_system: bool = False  # also the position and alert-link modules


SENSOR = {"camera": Source.CAMERA, "depth": Source.DEPTH_CAMERA, "lidar": Source.LIDAR}
POSITIONS = (("gps", PositionSource.GPS), ("lidar_map", PositionSource.LIDAR_MAP))
RADIOS = (("wifi", Link.WIFI), ("bluetooth", Link.BLUETOOTH))


class World:
    def __init__(self, route: Route):
        self.route = route
        self.x = 0.0
        self.t = 0.0  # the time of the last sense()
        self.mishaps: list[str] = []

    @property
    def finished(self) -> bool:
        return self.x >= self.route.length_m or bool(self.mishaps)

    def _trust(self, sensor: str) -> float | None:
        for f in self.route.faults:
            if f.sensor == sensor and f.from_m <= self.x < f.to_m:
                return f.trust
        return TRUST

    def _obstacles(self) -> list[Obstacle]:
        return [o for o in self.route.obstacles if o.gone_s > self.t]

    def _under(self) -> Stretch | None:
        return next(
            (s for s in self.route.stretches if s.real and s.start_m <= self.x < s.end_m), None
        )

    def _ground(self, t: float, source: Source, trust: float) -> GroundReport:
        ahead = [
            s
            for s in self.route.stretches
            if source in s.seen_by and s.end_m > self.x and s.start_m - self.x <= RANGE_M[source]
        ]
        if not ahead:
            return GroundReport(t, trust, source, Ground.FLAT)
        s = min(ahead, key=lambda s: s.start_m)
        if source == Source.CAMERA:
            return GroundReport(t, trust, source, s.ground)
        return GroundReport(t, trust, source, s.ground, max(s.start_m - self.x, 0.0), s.height_m)

    def sense(self, t: float) -> list:
        """The reports every working sensor sends at time t."""
        self.t, reports = t, []
        for name, source in SENSOR.items():
            trust = self._trust(name)
            if trust is None:
                continue
            if source != Source.LIDAR:
                reports.append(self._ground(t, source, trust))
            if source != Source.DEPTH_CAMERA:
                seen = [o for o in self._obstacles() if 0 <= o.at_m - self.x <= RANGE_M[source]]
                if seen:
                    o = min(seen, key=lambda o: o.at_m)
                    reports.append(ObstacleReport(t, trust, source, o.at_m - self.x, o.label))
        trust = self._trust("imu")
        if trust is not None:
            under = self._under()
            reports.append(MotionReport(t, trust, pitch_rad=under.pitch_rad if under else 0.0))
        if self.route.full_system:
            reports += self._modules(t)
        return reports

    def _modules(self, t: float) -> list:
        """The position report (GPS first, then the lidar map, else odometry) and one link report
        per radio; a radio with a fault reports that it is not connected."""
        working = [(self._trust(n), s) for n, s in POSITIONS if self._trust(n) is not None]
        trust, source = working[0] if working else (0.6, PositionSource.ODOMETRY)
        reports = [PositionReport(t, trust, source)]
        for name, link in RADIOS:
            trust = self._trust(name)
            reports.append(LinkReport(t, TRUST, link, connected=trust is not None))
        return reports

    def move(self, mode: Mode, stop: bool, speed_limit: float, dt: float) -> None:
        """Drive for dt seconds as the core asked, and note anything that went wrong."""
        if not stop:
            self.x += speed_limit * TOP_SPEED * dt
        under = self._under()
        if under and under.ground == Ground.DROP:
            self.mishaps.append(f"went over the drop at {under.start_m:.1f} m")
        elif under and under.ground in STEPS and mode == Mode.WHEELS:
            what = "curb" if under.ground == Ground.CURB else "stairs"
            self.mishaps.append(f"on the {what} on wheels at {self.x:.2f} m")
        for o in self._obstacles():
            if self.x >= o.at_m:
                self.mishaps.append(f"hit the {o.label} at {o.at_m:.1f} m")
