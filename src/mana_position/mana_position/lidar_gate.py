"""Take slam_toolbox's jumps out of the lidar map pose, with the wheels as the check (#43). No ROS.

slam_toolbox's localisation can jump a metre along a bare corridor, where one stretch of wall
looks like the next, and stay there until a corner or a cabinet sets it right (docs/results.md).
Localisers have no failure check of their own, and ROS users who meet such jumps ignore an update
that implies a very large one; a jump shows as an abrupt change of pose that the motion did not
make. So each lidar pose is compared with the one before it (half a second apart, slam_toolbox
gives about two a second while moving): the wheels' move over that time, turned into the map's
heading, is where it should be. Over half a second the wheels hardly drift, so a difference over
`jump_m` is a jump. It is added to an offset that is taken off every pose after it; when the lidar
jumps back, the offset returns. The offset fades with a time constant of `fade_s`, so a lasting
correction (the lidar was right after all) is taken in the end.

The settings come from four runs on the indoor loop (two laps each): with 0.5 m and 60 s the worst
error went from 1.26 to 0.59 m and from 1.13 to 0.33 m in the two with a jump, and stayed the same
(0.35 and 0.40 m) in the two without. Any jump_m from 0.35 to 0.8 m left those two unchanged.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

Pose = tuple[float, float, float]  # x, y (m), heading (rad)


@dataclass
class JumpFilter:
    jump_m: float = 0.5
    fade_s: float = 60.0
    offset: list[float] = field(default_factory=lambda: [0.0, 0.0])  # taken off the lidar pose
    last: tuple[float, Pose, Pose] | None = None  # time, lidar pose and wheels' pose at it
    odom: Pose | None = None  # the wheels' latest pose
    jumps: int = 0

    def on_odom(self, odom: Pose) -> None:
        self.odom = odom

    def correct(self, t: float, pose: Pose) -> Pose:
        """The lidar pose with the jumps taken out."""
        if self.last is not None and self.odom is not None:
            t0, (x0, y0, a0), o0 = self.last
            turn = a0 - o0[2]  # the map's heading minus the wheels' at the last pose
            dx, dy = self.odom[0] - o0[0], self.odom[1] - o0[1]
            c, s = math.cos(turn), math.sin(turn)
            ex = (pose[0] - x0) - (dx * c - dy * s)
            ey = (pose[1] - y0) - (dx * s + dy * c)
            if math.hypot(ex, ey) > self.jump_m:
                self.offset[0] += ex
                self.offset[1] += ey
                self.jumps += 1
            fade = math.exp(-max(0.0, t - t0) / self.fade_s)
            self.offset[0] *= fade
            self.offset[1] *= fade
        if self.odom is not None:
            self.last = (t, pose, self.odom)
        return pose[0] - self.offset[0], pose[1] - self.offset[1], pose[2]
