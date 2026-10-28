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
from dataclasses import dataclass, replace
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
NO_GROUND = "Ground sensors not available, slowing down"
SPEED_STEP = 0.25  # how much one "up" or "down" changes the speed in manual mode
ASK_DOWN = "Stairs down ahead. Say yes to go down on tracks"
STEP_STOP = "Step ahead, stopping"
STOPS = ("drop", "obstacle", "lone step", "stairs down")  # the stop branches that hold (#97)


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
    hold_s: float = 0.5  # a stop holds this long after its hazard was last seen (#97)
    release_m: float = 0.1  # and while stopped, a hazard this far past its limit still counts
    answer_s: float = 2.0  # a yes this soon after the question was last asked still answers it
    slow: float = 0.3  # speed limit when slowing down, as a share of top speed
    missed_heartbeats: int = 2  # a link that missed this many heartbeats in a row cannot alert
    link_max_age_s: float = 2.5  # link and position reports come about once a second
    position_max_age_s: float = 2.5
    clock_back_s: float = 5.0  # a report this much older than the one held: the clock went back


@dataclass(frozen=True)
class Mount:
    """Where the device's front and each sensor are, in metres ahead of one point of the device
    (the chair's centre in the simulation). Reports give distances from their sensor. The core
    turns them into distances from the front, for the current mode, so that edge_m and
    obstacle_stop_m hold from the front (#96). On tracks the chair's front is further ahead than
    on wheels. All 0: the reports already measure from the front, as the scripted world's do."""

    front_wheels_m: float = 0.0
    front_tracks_m: float = 0.0
    camera_m: float = 0.0  # the camera, and the classifier and detector that run on its image
    depth_camera_m: float = 0.0
    lidar_m: float = 0.0
    switch_s: float = 2.5  # the slides take this long: until then the longer front counts
    # how far the chair goes after a stop is decided: at the speed it was last allowed, for
    # reaction_s, then braking at decel_mps2. A stop distance then holds where the chair stands
    # still, not where the stop was decided. 0: no allowance, as in the scripted world.
    top_mps: float = 0.0  # the host's top speed, at speed limit 1.0
    reaction_s: float = 0.0  # from a sensor frame to the wheels braking
    decel_mps2: float = 0.0

    def travel_m(self, speed_limit: float) -> float:
        """How far the chair goes at this speed limit, from a stop decided to standing still."""
        v = speed_limit * self.top_mps
        return v * self.reaction_s + (v * v / (2.0 * self.decel_mps2) if self.decel_mps2 else 0.0)

    def ahead_of_sensor(self, source: Source, mode: Mode, switching: bool) -> float:
        """How far the front is ahead of the sensor of this source."""
        front = {Mode.WHEELS: self.front_wheels_m, Mode.TRACKS: self.front_tracks_m}
        at = max(front.values()) if switching else front[mode]
        sensor = {
            Source.CAMERA: self.camera_m,
            Source.DEPTH_CAMERA: self.depth_camera_m,
            Source.LIDAR: self.lidar_m,
        }[SENSOR_OF.get(source, source)]
        return at - sensor


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
    def __init__(self, limits: Limits | None = None, mount: Mount | None = None):
        self.limits = limits or Limits()
        self.mount = mount or Mount()
        self.mode_changed_at = -math.inf
        self.moving_speed = 1.0  # the speed limit of the last decision that was not a stop
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
        self.asked_at = -math.inf  # when the stairs-down question was last the decision
        self._seen_at: dict[str, float] = {}  # stop branch: when its hazard was last seen
        self.now = 0.0
        self.decision = Decision(Mode.WHEELS, True, 0.0, "Starting", Priority.INFO)
        self.tree = self._build()

    # ---- inputs: the latest report per source wins; one older than it, arriving late, does not

    def _newer(self, old, report) -> bool:
        """A report a little older than the one held came late and is dropped. It wins when the
        clock went back (a world reset, a bag played again): when it is much older, or when the
        one held is stamped ahead of the core's own clock."""
        return (
            old is None
            or report.stamp >= old.stamp
            or old.stamp - report.stamp > self.limits.clock_back_s
            or old.stamp - self.now > self.limits.max_age_s
        )

    def _keep(self, reports: dict, key, report) -> None:
        if self._newer(reports.get(key), report):
            reports[key] = report

    def _from_front(self, report):
        """The report with its distance from the device's front instead of from its sensor. A
        hazard the front has already reached is at 0 m; an unknown distance stays unknown."""
        if not math.isfinite(report.distance_m):
            return report
        switching = 0.0 <= self.now - self.mode_changed_at < self.mount.switch_s
        ahead = self.mount.ahead_of_sensor(report.source, self.mode, switching)
        return replace(report, distance_m=max(0.0, report.distance_m - ahead)) if ahead else report

    def on_ground(self, report: GroundReport) -> None:
        self._keep(self.ground, report.source, self._from_front(report))

    def on_obstacle(self, report: ObstacleReport) -> None:
        self._keep(self.obstacles, report.source, self._from_front(report))

    def on_motion(self, report: MotionReport) -> None:
        if self._newer(self.motion, report):
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
        elif command == Command.YES and (
            self.asking_down or self.now - self.asked_at <= self.limits.answer_s
        ):
            self.down_ok = True  # also just after the question, which one reading can end
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
        # every tick, whichever branch fires, so that each hold counts from its own hazard; while
        # a stop holds, its hazard still counts up to release_m past the limit (hysteresis)
        past = {name: lim.release_m if self._held(name) else 0.0 for name in STOPS}
        reach = self.mount.travel_m(self.moving_speed)  # the stop distances hold at standstill
        seen = {
            "drop": self._drop_at_edge(lim.edge_m + reach + past["drop"]),
            "obstacle": self._nearest_obstacle() <= lim.obstacle_stop_m + reach + past["obstacle"],
            "lone step": self._lone_step_at_edge(lim.edge_m + reach + past["lone step"]),
            "stairs down": self._stairs_down(lim.step_m + past["stairs down"]),
        }
        if any(at > now for at in (*self._seen_at.values(), self.down_seen_at, self.asked_at)):
            # the clock went back (a world reset, a bag played again): what was seen is gone
            self._seen_at.clear()
            self.down_seen_at, self.asked_at, self.down_ok = -math.inf, -math.inf, False
        self._seen_at.update({name: now for name, s in seen.items() if s})
        if seen["stairs down"]:
            self.down_seen_at = now
        elif self.down_ok and now - self.down_seen_at > lim.down_forget_s:
            self.down_ok = False  # those stairs are behind or gone: ask at the next ones
        self.tree.tick_once()
        if self.decision.mode != self.mode:
            self.mode_changed_at = now
        if not self.decision.stop:
            self.moving_speed = self.decision.speed_limit
        self.mode = self.decision.mode
        self.asking_down = self.decision.reason == ASK_DOWN
        if self.asking_down:
            self.asked_at = now
        return self.decision

    # ---- what the reports say

    def _ok(self, report, max_age_s: float | None = None) -> bool:
        """Recent and trusted. A report stamped further ahead than max_age_s is not used either:
        after the clock went back, one held from before would otherwise stay fresh."""
        max_age_s = self.limits.max_age_s if max_age_s is None else max_age_s
        return (
            report is not None
            and -self.limits.max_age_s <= self.now - report.stamp <= max_age_s
            and report.trust >= self.limits.min_trust
        )

    def _grounds(self) -> list[GroundReport]:
        return [r for r in self.ground.values() if self._ok(r)]

    def _nearest_obstacle(self) -> float:
        return min((r.distance_m for r in self.obstacles.values() if self._ok(r)), default=math.inf)

    def _drop_at_edge(self, edge_m: float | None = None) -> bool:
        """A drop at the edge, or stairs going down at the edge that only one sensor sees: a fall
        needs no second opinion. Stairs down that two sensors confirm are left to the "stairs
        down" and "going down" branches, which stop and ask, so a yes can still take the chair
        down them. A stop needs a measured distance."""
        confirmed = self._stairs_down()
        return any(
            r.distance_m <= (self.limits.edge_m if edge_m is None else edge_m)
            and (r.ground == Ground.DROP or (r.ground == Ground.STAIRS_DOWN and not confirmed))
            for r in self._grounds()
        )

    def _lone_step_at_edge(self, edge_m: float | None = None) -> bool:
        """A step up or a curb measured within edge_m that no second sensor confirms, as when the
        camera cannot be trusted in the dark. A change of mode needs two sensors, but a stop needs
        none, so the chair stops instead of rolling onto it on wheels. A report without a
        distance (the camera alone, a picture of stairs) only slows down."""
        edge_m = self.limits.edge_m if edge_m is None else edge_m
        return self._confirmed_step() is None and any(
            r.ground in (Ground.STAIRS_UP, Ground.CURB) and r.distance_m <= edge_m
            for r in self._grounds()
        )

    def _may_cross_a_step(self) -> bool:
        """No stop for a lone step on tracks, which can climb it, or when the user has chosen
        tracks in manual mode to cross on purpose. Checked every tick, so it is never held."""
        return self.decision.mode == Mode.TRACKS or (
            not self.automatic and self.user_mode == Mode.TRACKS
        )

    def _confirmed_step(self, step_m: float | None = None) -> GroundReport | None:
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
        step_m = self.limits.step_m if step_m is None else step_m
        known = [r for r in agreeing if r.distance_m <= step_m]  # NaN is never <=
        return min(known, key=lambda r: r.distance_m, default=None)

    def _stairs_down(self, step_m: float | None = None) -> bool:
        """A step two sensors agree on, where any of them sees stairs going down. One sensor
        saying stairs down and the other a curb still asks: it never goes down unasked."""
        return self._confirmed_step(step_m) is not None and any(
            r.ground == Ground.STAIRS_DOWN for r in self._grounds()
        )

    def _held(self, name: str) -> bool:
        """The hazard of a stop branch was seen within hold_s: one reading over a limit, or one
        lost report, does not release the stop, and while it holds a hazard up to release_m past
        the limit still counts as seen (#97)."""
        return self.now - self._seen_at.get(name, -math.inf) < self.limits.hold_s

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

    def _manual(self):
        """The user drives, on the undercarriage they chose. With no ground sensor at all MANA
        cannot see a drop, so it slows down as in automatic mode (#85)."""
        if self._grounds():
            self._set(self.user_mode, False, self.user_speed, "Manual mode", Priority.INFO)
        else:
            speed = min(self.user_speed, self.limits.slow)
            self._set(self.user_mode, False, speed, NO_GROUND, Priority.WARNING)

    def _tracks(self):
        if self.down_ok and self._held("stairs down"):  # as the "going down" branch decides
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
                branch(
                    "drop",
                    lambda: self._held("drop"),
                    lambda: self._stop("Drop ahead, stopping"),
                ),
                branch(
                    "obstacle",
                    lambda: self._held("obstacle"),
                    lambda: self._stop("Obstacle ahead, stopping"),
                ),
                branch(  # above manual too: a driving command does not roll onto it
                    "lone step",
                    lambda: self._held("lone step") and not self._may_cross_a_step(),
                    lambda: self._stop(STEP_STOP),
                ),
                branch(  # above manual: a driving command at the question does not drive on
                    "stairs down",  # a yes ends the question at once; a lost report does not
                    lambda: self._held("stairs down") and not self.down_ok,
                    lambda: self._stop(ASK_DOWN),
                ),
                branch(  # and after the yes, down on tracks whatever the mode
                    "going down",
                    lambda: self._held("stairs down") and self.down_ok,
                    self._tracks,
                ),
                branch(
                    "manual",
                    lambda: not self.automatic,
                    self._manual,
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
                        NO_GROUND,
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
