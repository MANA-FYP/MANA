"""MANA decision core (M6): how to move, as a behaviour tree. Pure Python, no ROS.

The node feeds in the latest module reports and asks for a decision at a fixed rate. The tree
checks, highest priority first: the user's stop, safety (a drop or an obstacle close ahead), the
user's control in manual mode, then MANA's own choice of wheels or tracks and speed.

Two rules from the design: a change of motion mode needs two independent sources to agree, and a
stop needs none. Reports that are too old or not trusted enough are ignored. The thresholds are
defaults, to be tuned in simulation.
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
class Limits:
    min_trust: float = 0.5  # reports below this trust are ignored
    max_age_s: float = 0.5  # reports older than this are ignored
    edge_m: float = 0.5  # stop this far before a drop
    obstacle_stop_m: float = 0.8
    obstacle_slow_m: float = 2.0
    step_m: float = 2.0  # react to a step, ramp or drop this close
    level_rad: float = 0.05  # about 3 degrees of tilt counts as level
    level_hold_s: float = 2.0  # level this long before going back to wheels
    slow: float = 0.3  # speed limit when slowing down, as a share of top speed


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
        self.automatic = True
        self.stopped_by_user = False
        self.user_mode = Mode.WHEELS
        self.user_speed = 1.0
        self.mode = Mode.WHEELS
        self.level_since: float | None = None
        self.now = 0.0
        self.decision = Decision(Mode.WHEELS, True, 0.0, "Starting", Priority.INFO)
        self.tree = self._build()

    # ---- inputs: the latest report per source wins

    def on_ground(self, report: GroundReport) -> None:
        self.ground[report.source] = report

    def on_obstacle(self, report: ObstacleReport) -> None:
        self.obstacles[report.source] = report

    def on_motion(self, report: MotionReport) -> None:
        self.motion = report

    def on_command(self, command: Command, trust: float) -> None:
        """A stop is obeyed at any trust; other commands need min_trust. A driving command hands
        control to the user (manual) until they choose automatic again. LEFT, RIGHT, YES and NO
        are not part of this decision."""
        if command == Command.STOP:
            self.stopped_by_user = True
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
        self.tree.tick_once()
        self.mode = self.decision.mode
        return self.decision

    # ---- what the reports say

    def _ok(self, report) -> bool:
        return (
            report is not None
            and self.now - report.stamp <= self.limits.max_age_s
            and report.trust >= self.limits.min_trust
        )

    def _grounds(self) -> list[GroundReport]:
        return [r for r in self.ground.values() if self._ok(r)]

    def _nearest_obstacle(self) -> float:
        return min((r.distance_m for r in self.obstacles.values() if self._ok(r)), default=math.inf)

    def _drop_at_edge(self) -> bool:  # a stop needs a measured distance
        return any(
            r.ground == Ground.DROP and r.distance_m <= self.limits.edge_m for r in self._grounds()
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
        step = self._confirmed_step()
        what = "Curb" if step.ground == Ground.CURB else "Stairs"
        self._set(
            Mode.TRACKS,
            False,
            self.limits.slow,
            f"{what} ahead, changing to tracks",
            Priority.WARNING,
        )

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
                _Act(
                    "wheels",
                    lambda: self._set(Mode.WHEELS, False, 1.0, "Level ground", Priority.INFO),
                ),
            ],
        )
