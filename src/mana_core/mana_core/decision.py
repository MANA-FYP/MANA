"""MANA decision core (M6): how to move, as a behaviour tree. Pure Python, no ROS.

The node feeds in the latest module reports and asks for a decision at a fixed rate. The tree
checks, highest priority first: the user's stop, safety (a drop or an obstacle close ahead), the
user's control in manual mode, then MANA's own choice of wheels or tracks and speed. Losing the
position or the alert link also slows MANA down: without them a call for help could not say where
the user is, or could not be sent.

Two rules from the design: a change of motion mode needs two independent sources to agree, and a
stop needs none. Stairs going down are the one change MANA does not make on its own: it stops at
the top and goes down on tracks only after the user says yes. Reports that are too old or not
trusted enough are ignored. The thresholds are defaults, to be tuned in simulation.
"""

import math
from dataclasses import dataclass
from enum import IntEnum

import py_trees

# Codes match the constants in mana_msgs; tests/test_decision.py checks that they stay in step.


class Source(IntEnum):
    CAMERA = 0
    DEPTH_CAMERA = 1
    LIDAR = 2
    CLASSIFIER = 3  # the ground classifier that runs on the camera image
    DETECTOR = 4  # the object detector that runs on the camera image


# The physical sensor behind each source. The classifier and the detector look at the camera's
# image, so for agreement they are the camera, not two more sensors.
SENSOR_OF = {Source.CLASSIFIER: Source.CAMERA, Source.DETECTOR: Source.CAMERA}


class PositionSource(IntEnum):
    GPS = 0
    LIDAR_MAP = 1
    ODOMETRY = 2


class Link(IntEnum):
    WIFI = 0
    BLUETOOTH = 1


class Ground(IntEnum):
    FLAT = 0
    RAMP = 1
    STAIRS_UP = 2
    STAIRS_DOWN = 3
    CURB = 4
    DROP = 5
    UNKNOWN = 255


class Command(IntEnum):
    STOP = 0
    GO = 1
    LEFT = 2
    RIGHT = 3
    SPEED_UP = 4
    SLOW_DOWN = 5
    YES = 6
    NO = 7
    AUTOMATIC = 8
    MANUAL = 9
    WHEELS = 10
    TRACKS = 11


class Mode(IntEnum):
    WHEELS = 0
    TRACKS = 1


class Priority(IntEnum):
    INFO = 0
    WARNING = 1
    URGENT = 2


STEPS = {Ground.STAIRS_UP, Ground.STAIRS_DOWN, Ground.CURB}
SPEED_STEP = 0.25  # how much one "up" or "down" changes the speed in manual mode
ASK_DOWN = "Stairs down ahead. Say yes to go down on tracks"
STEP_STOP = "Step ahead, stopping"


@dataclass(frozen=True)
class GroundReport:
    stamp: float  # seconds, when it was measured
    trust: float
    source: Source
    ground: Ground
    distance_m: float = math.nan  # to where the ground changes; NaN when unknown
    step_height_m: float = math.nan


@dataclass(frozen=True)
class ObstacleReport:
    stamp: float
    trust: float
    source: Source
    distance_m: float
    label: str = ""


@dataclass(frozen=True)
class MotionReport:
    stamp: float
    trust: float
    pitch_rad: float = 0.0
    roll_rad: float = 0.0
    wheel_slip: bool = False


@dataclass(frozen=True)
class PositionReport:
    stamp: float
    trust: float
    source: PositionSource
    error_m: float = math.nan  # estimated horizontal error


@dataclass(frozen=True)
class LinkReport:
    stamp: float
    trust: float
    link: Link
    connected: bool
    missed_heartbeats: int = 0  # in a row


@dataclass(frozen=True)
class Limits:
    min_trust: float = 0.5  # reports below this trust are ignored
    max_age_s: float = 0.5  # reports older than this are ignored
    edge_m: float = 0.5  # stop this far before a drop
    obstacle_stop_m: float = 0.8
    obstacle_slow_m: float = 2.0
    step_m: float = 2.0  # react to a step, ramp or drop this close
    level_rad: float = 0.05  # about 3 degrees of tilt counts as level
    level_hold_s: float = 2.0  # level this long before going back to wheels
    down_forget_s: float = 1.5  # a yes to stairs down lapses once they are out of sight this long
    slow: float = 0.3  # speed limit when slowing down, as a share of top speed
    missed_heartbeats: int = 2  # a link that missed this many heartbeats in a row cannot alert
    link_max_age_s: float = 2.5  # link and position reports come about once a second
    position_max_age_s: float = 2.5


@dataclass(frozen=True)
class Decision:
    mode: Mode
    stop: bool
    speed_limit: float
    reason: str
    priority: Priority


class _Check(py_trees.behaviour.Behaviour):
    """Condition: succeeds when its test holds."""

    def __init__(self, name, test):
        super().__init__(name)
        self.test = test

    def update(self):
        return py_trees.common.Status.SUCCESS if self.test() else py_trees.common.Status.FAILURE


class _Act(py_trees.behaviour.Behaviour):
    """Action: records the decision and succeeds."""

    def __init__(self, name, act):
        super().__init__(name)
        self.act = act

    def update(self):
        self.act()
        return py_trees.common.Status.SUCCESS


class DecisionCore:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.ground: dict[Source, GroundReport] = {}
        self.obstacles: dict[Source, ObstacleReport] = {}
        self.motion: MotionReport | None = None
        self.positions: dict[PositionSource, PositionReport] = {}
        self.links: dict[Link, LinkReport] = {}
        self.automatic = True
        self.stopped_by_user = False
        self.user_mode = Mode.WHEELS
        self.user_speed = 1.0
        self.mode = Mode.WHEELS
        self.level_since: float | None = None
        self.asking_down = False  # stopped at the top of stairs down, waiting for a yes
        self.down_ok = False  # the user said yes to the stairs down in sight
        self.down_seen_at = -math.inf
        self.now = 0.0
        self.decision = Decision(Mode.WHEELS, True, 0.0, "Starting", Priority.INFO)
        self.tree = self._build()

    # ---- inputs: the latest report per source wins; one older than it, arriving late, does not

    @staticmethod
    def _keep(reports: dict, key, report) -> None:
        old = reports.get(key)
        if old is None or report.stamp >= old.stamp:
            reports[key] = report

    def on_ground(self, report: GroundReport) -> None:
        self._keep(self.ground, report.source, report)

    def on_obstacle(self, report: ObstacleReport) -> None:
        self._keep(self.obstacles, report.source, report)

    def on_motion(self, report: MotionReport) -> None:
        if self.motion is None or report.stamp >= self.motion.stamp:
            self.motion = report

    def on_position(self, report: PositionReport) -> None:
        self._keep(self.positions, report.source, report)

    def on_link(self, report: LinkReport) -> None:
        self._keep(self.links, report.link, report)

    def on_command(self, command: Command, trust: float) -> None:
        """A stop is obeyed at any trust; other commands need min_trust. A driving command hands
        control to the user (manual) until they choose automatic again. YES answers the question
        at the top of stairs down; at any other time it does nothing, so an early yes cannot
        approve stairs not yet seen. A NO or a STOP takes the yes back at any trust, since taking
        it back is always safe, so MANA asks again. Once the user says yes, the stairs are gone
        down on tracks whatever the mode. LEFT and RIGHT are not part of this decision."""
        if command == Command.STOP:
            self.stopped_by_user = True
            self.down_ok = False
            return
        if command == Command.NO:
            self.down_ok = False
            return
        if trust < self.limits.min_trust:
            return
        if command == Command.GO:
            self.stopped_by_user = False
        elif command in (Command.AUTOMATIC, Command.MANUAL):
            self.automatic = command == Command.AUTOMATIC
        elif command in (Command.WHEELS, Command.TRACKS):
            self.automatic = False
            self.user_mode = Mode.WHEELS if command == Command.WHEELS else Mode.TRACKS
        elif command == Command.YES and self.asking_down:
            self.down_ok = True
        elif command in (Command.SPEED_UP, Command.SLOW_DOWN):
            self.automatic = False
            step = SPEED_STEP if command == Command.SPEED_UP else -SPEED_STEP
            self.user_speed = min(1.0, max(SPEED_STEP, self.user_speed + step))

    # ---- one decision

    def tick(self, now: float) -> Decision:
        self.now = now
        motion = self.motion if self._ok(self.motion) else None
        lim = self.limits
        level = (
            motion is not None and max(abs(motion.pitch_rad), abs(motion.roll_rad)) < lim.level_rad
        )
        self.level_since = (
            (now if self.level_since is None else self.level_since) if level else None
        )
        if self._stairs_down():
            self.down_seen_at = now
        elif self.down_ok and now - self.down_seen_at > lim.down_forget_s:
            self.down_ok = False  # those stairs are behind or gone: ask at the next ones
        self.tree.tick_once()
        self.mode = self.decision.mode
        self.asking_down = self.decision.reason == ASK_DOWN
        return self.decision

    # ---- what the reports say

    def _ok(self, report, max_age_s: float | None = None) -> bool:
        return (
            report is not None
            and self.now - report.stamp
            <= (self.limits.max_age_s if max_age_s is None else max_age_s)
            and report.trust >= self.limits.min_trust
        )

    def _grounds(self) -> list[GroundReport]:
        return [r for r in self.ground.values() if self._ok(r)]

    def _nearest_obstacle(self) -> float:
        return min((r.distance_m for r in self.obstacles.values() if self._ok(r)), default=math.inf)

    def _drop_at_edge(self) -> bool:
        """A drop at the edge, or stairs going down at the edge that only one sensor sees: a fall
        needs no second opinion. Stairs down that two sensors confirm are left to the "stairs
        down" and "going down" branches, which stop and ask, so a yes can still take the chair
        down them. A stop needs a measured distance."""
        confirmed = self._stairs_down()
        return any(
            r.distance_m <= self.limits.edge_m
            and (r.ground == Ground.DROP or (r.ground == Ground.STAIRS_DOWN and not confirmed))
            for r in self._grounds()
        )

    def _lone_step_at_edge(self) -> bool:
        """A step up or a curb measured within edge_m that no second sensor confirms, as when the
        camera cannot be trusted in the dark. A change of mode needs two sensors, but a stop needs
        none, so the chair stops instead of rolling onto it on wheels. A report without a
        distance (the camera alone, a picture of stairs) only slows down. No stop on tracks, which
        can climb it, or when the user has chosen tracks in manual mode to cross on purpose."""
        if self.decision.mode == Mode.TRACKS or (
            not self.automatic and self.user_mode == Mode.TRACKS
        ):
            return False
        return self._confirmed_step() is None and any(
            r.ground in (Ground.STAIRS_UP, Ground.CURB) and r.distance_m <= self.limits.edge_m
            for r in self._grounds()
        )

    def _confirmed_step(self) -> GroundReport | None:
        """The nearest report of a step that at least two different sensors both see close ahead.

        Ground reports come from the camera and the 3D camera. The ground classifier and the
        object detector both use the camera image, so together they are one sensor: on a phone
        without depth a step is never confirmed, and the chair slows down instead. A report with
        an unknown distance can agree, but at least one of the agreeing reports must give a
        distance within step_m.
        """
        agreeing = [r for r in self._grounds() if r.ground in STEPS]
        if len({SENSOR_OF.get(r.source, r.source) for r in agreeing}) < 2:
            return None
        known = [r for r in agreeing if r.distance_m <= self.limits.step_m]  # NaN is never <=
        return min(known, key=lambda r: r.distance_m, default=None)

    def _stairs_down(self) -> bool:
        """A step two sensors agree on, where any of them sees stairs going down. One sensor
        saying stairs down and the other a curb still asks: it never goes down unasked."""
        return self._confirmed_step() is not None and any(
            r.ground == Ground.STAIRS_DOWN for r in self._grounds()
        )

    def _position_lost(self) -> bool:
        """A position module reports, but neither GPS nor the lidar map has a recent, trusted
        position. Odometry does not count: it drifts, so it cannot tell a helper where the user
        is. A host without a position module is not slowed down for it."""
        return bool(self.positions) and not any(
            self._ok(r, self.limits.position_max_age_s)
            for r in self.positions.values()
            if r.source != PositionSource.ODOMETRY
        )

    def _link_lost(self) -> bool:
        """A link module reports, but neither Wi-Fi nor Bluetooth could carry an alert now."""
        lim = self.limits
        return bool(self.links) and not any(
            self._ok(r, lim.link_max_age_s)
            and r.connected
            and r.missed_heartbeats < lim.missed_heartbeats
            for r in self.links.values()
        )

    def _slipping(self) -> bool:
        return self._ok(self.motion) and self.motion.wheel_slip

    def _level_held(self) -> bool:
        return (
            self.level_since is not None and self.now - self.level_since >= self.limits.level_hold_s
        )

    def _slow_reason(self) -> str | None:
        lim = self.limits
        if self._nearest_obstacle() <= lim.obstacle_slow_m:
            return "Obstacle ahead, slowing down"
        near = [
            r for r in self._grounds() if not r.distance_m > lim.step_m
        ]  # unknown distance counts
        for kind, reason in ((Ground.DROP, "Drop ahead"), (Ground.RAMP, "Ramp ahead")):
            if any(r.ground == kind for r in near):
                return f"{reason}, slowing down"
        if any(r.ground in STEPS for r in near):
            return "Possible step ahead, slowing down"
        return None

    # ---- the tree

    def _set(self, mode, stop, speed, reason, priority):
        self.decision = Decision(mode, stop, speed, reason, priority)

    def _stop(self, reason):
        self._set(self.mode, True, 0.0, reason, Priority.URGENT)

    def _tracks(self):
        if self._stairs_down():
            reason = "Going down the stairs on tracks"
        elif self._confirmed_step().ground == Ground.CURB:
            reason = "Curb ahead, changing to tracks"
        else:
            reason = "Stairs ahead, changing to tracks"
        self._set(Mode.TRACKS, False, self.limits.slow, reason, Priority.WARNING)

    def _build(self):
        lim = self.limits

        def branch(name, test, act):
            return py_trees.composites.Sequence(
                name, memory=False, children=[_Check(name + "?", test), _Act(name, act)]
            )

        return py_trees.composites.Selector(
            "decide",
            memory=False,
            children=[
                branch(
                    "user stop", lambda: self.stopped_by_user, lambda: self._stop("Stopped by you")
                ),
                branch("drop", self._drop_at_edge, lambda: self._stop("Drop ahead, stopping")),
                branch(
                    "obstacle",
                    lambda: self._nearest_obstacle() <= lim.obstacle_stop_m,
                    lambda: self._stop("Obstacle ahead, stopping"),
                ),
                branch(  # above manual too: a driving command does not roll onto it
                    "lone step", self._lone_step_at_edge, lambda: self._stop(STEP_STOP)
                ),
                branch(  # above manual: a driving command at the question does not drive on
                    "stairs down",
                    lambda: self._stairs_down() and not self.down_ok,
                    lambda: self._stop(ASK_DOWN),
                ),
                branch(  # and after the yes, down on tracks whatever the mode
                    "going down",
                    lambda: self._stairs_down() and self.down_ok,
                    self._tracks,
                ),
                branch(
                    "manual",
                    lambda: not self.automatic,
                    lambda: self._set(
                        self.user_mode, False, self.user_speed, "Manual mode", Priority.INFO
                    ),
                ),
                branch("step", lambda: self._confirmed_step() is not None, self._tracks),
                branch(
                    "slip",
                    self._slipping,
                    lambda: self._set(
                        Mode.TRACKS,
                        False,
                        lim.slow,
                        "Wheel slip, changing to tracks",
                        Priority.WARNING,
                    ),
                ),
                branch(
                    "hold tracks",
                    lambda: self.mode == Mode.TRACKS and not self._level_held(),
                    lambda: self._set(
                        Mode.TRACKS,
                        False,
                        lim.slow,
                        "On tracks until the ground is level",
                        Priority.INFO,
                    ),
                ),
                branch(
                    "no ground",
                    lambda: not self._grounds(),
                    lambda: self._set(
                        Mode.WHEELS,
                        False,
                        lim.slow,
                        "Ground sensors not available, slowing down",
                        Priority.WARNING,
                    ),
                ),
                branch(
                    "slow",
                    lambda: self._slow_reason() is not None,
                    lambda: self._set(
                        Mode.WHEELS, False, lim.slow, self._slow_reason(), Priority.WARNING
                    ),
                ),
                branch(
                    "no alert link",
                    self._link_lost,
                    lambda: self._set(
                        Mode.WHEELS,
                        False,
                        lim.slow,
                        "No alert link, slowing down",
                        Priority.WARNING,
                    ),
                ),
                branch(
                    "no position",
                    self._position_lost,
                    lambda: self._set(
                        Mode.WHEELS,
                        False,
                        lim.slow,
                        "Position unknown, slowing down",
                        Priority.WARNING,
                    ),
                ),
                _Act(
                    "wheels",
                    lambda: self._set(Mode.WHEELS, False, 1.0, "Level ground", Priority.INFO),
                ),
            ],
        )
