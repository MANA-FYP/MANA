"""Baseline controller (FR-26): the simple if-else rules MANA is compared against. Pure Python.

It reads the same reports and commands as DecisionCore and uses the same thresholds and messages,
so the comparison shows what MANA's design adds, not different tuning. What it leaves out:

- trust and age: every report counts, however doubtful or old;
- agreement: one source is enough to change to tracks;
- the level timer: it goes back to wheels the moment the tilt is small;
- lost sensors: old reports are never cleared, so when a sensor stops it acts on that sensor's
  last values for ever.

Otherwise it is a fair if-else controller: it stops for the user, a drop or an obstacle, slows for
an obstacle, a drop or a ramp ahead, and changes to tracks for a step that is seen or a tilt
steeper than any accessible ramp.
"""

import math

from mana_core.decision import (
    STEPS,
    Command,
    Decision,
    Ground,
    GroundReport,
    Limits,
    LinkReport,
    Mode,
    MotionReport,
    ObstacleReport,
    PositionReport,
    Priority,
    Source,
)

STAIRS_TILT_RAD = 0.2  # about 11 degrees: steeper than any accessible ramp (1:12, about 0.08 rad)


class BaselineController:
    def __init__(self, limits: Limits | None = None):
        self.limits = limits or Limits()
        self.ground: dict[Source, GroundReport] = {}
        self.obstacles: dict[Source, ObstacleReport] = {}
        self.motion: MotionReport | None = None
        self.stopped_by_user = False
        self.automatic = True
        self.mode = Mode.WHEELS  # a stop keeps the mode it stopped in

    def on_ground(self, report: GroundReport) -> None:
        self.ground[report.source] = report

    def on_obstacle(self, report: ObstacleReport) -> None:
        self.obstacles[report.source] = report

    def on_motion(self, report: MotionReport) -> None:
        self.motion = report

    def on_position(self, report: PositionReport) -> None:
        """Position is not part of the baseline's rules."""

    def on_link(self, report: LinkReport) -> None:
        """The alert link is not part of the baseline's rules."""

    def on_command(self, command: Command, trust: float) -> None:
        if command == Command.STOP:
            self.stopped_by_user = True
        elif command == Command.GO:
            self.stopped_by_user = False
        elif command in (Command.AUTOMATIC, Command.MANUAL):
            self.automatic = command == Command.AUTOMATIC

    def tick(self, now: float) -> Decision:
        decision = self._decide()
        self.mode = decision.mode
        return decision

    def _decide(self) -> Decision:
        lim = self.limits
        grounds = list(self.ground.values())
        nearest = min((r.distance_m for r in self.obstacles.values()), default=math.inf)
        tilt = max(abs(self.motion.pitch_rad), abs(self.motion.roll_rad)) if self.motion else 0.0

        if self.stopped_by_user:
            return Decision(self.mode, True, 0.0, "Stopped by you", Priority.URGENT)
        if any(r.ground == Ground.DROP and r.distance_m <= lim.edge_m for r in grounds):
            return Decision(self.mode, True, 0.0, "Drop ahead, stopping", Priority.URGENT)
        if nearest <= lim.obstacle_stop_m:
            return Decision(self.mode, True, 0.0, "Obstacle ahead, stopping", Priority.URGENT)
        if not self.automatic:
            return Decision(Mode.WHEELS, False, 1.0, "Manual mode", Priority.INFO)
        if any(r.ground in STEPS and not r.distance_m > lim.step_m for r in grounds):
            return Decision(
                Mode.TRACKS, False, lim.slow, "Stairs ahead, changing to tracks", Priority.WARNING
            )
        if tilt >= STAIRS_TILT_RAD:
            return Decision(
                Mode.TRACKS, False, lim.slow, "On tracks until the ground is level", Priority.INFO
            )
        if nearest <= lim.obstacle_slow_m:
            return Decision(
                Mode.WHEELS, False, lim.slow, "Obstacle ahead, slowing down", Priority.WARNING
            )
        near = [r.ground for r in grounds if not r.distance_m > lim.step_m]  # unknown counts
        for kind, reason in ((Ground.DROP, "Drop ahead"), (Ground.RAMP, "Ramp ahead")):
            if kind in near:
                return Decision(
                    Mode.WHEELS, False, lim.slow, f"{reason}, slowing down", Priority.WARNING
                )
        return Decision(Mode.WHEELS, False, 1.0, "Level ground", Priority.INFO)
