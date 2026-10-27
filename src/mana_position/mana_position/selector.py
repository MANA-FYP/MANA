"""Position source (M2): GPS outdoors, the lidar map indoors, odometry when neither can be used.
Pure Python, no ROS.

GPS is left once its error has been worse than out_m, or there has been no fix, for out_s
seconds: going into a building switches within the 2 s of FR-19, and one bad reading does not
switch at all. GPS is chosen again only after its error has been better than back_m for back_s
seconds. Between the two levels nothing changes, so one outage gives one switch away and one back.

Away from GPS the lidar map is used while the lidar localisation reports, and odometry, which
drifts, only while it does not.
"""

import math
from enum import IntEnum


class Source(IntEnum):  # the codes of mana_msgs/PositionReport
    GPS = 0
    LIDAR_MAP = 1
    ODOMETRY = 2


class PositionSelector:
    def __init__(self, out_m=15.0, out_s=1.5, back_m=8.0, back_s=5.0):
        self.out_m, self.out_s, self.back_m, self.back_s = out_m, out_s, back_m, back_s
        self.source = Source.GPS
        self._since: float | None = None  # when the condition to leave or come back began

    def update(self, gps_error_m: float | None, lidar_ok: bool, now: float) -> Source:
        """gps_error_m is None (or NaN) when there is no fix."""
        error = math.inf if gps_error_m is None or math.isnan(gps_error_m) else gps_error_m
        on_gps = self.source == Source.GPS
        changing = error > self.out_m if on_gps else error < self.back_m
        self._since = (now if self._since is None else self._since) if changing else None
        if self._since is not None and now - self._since >= (self.out_s if on_gps else self.back_s):
            self._since, on_gps = None, not on_gps
        self.source = Source.GPS if on_gps else Source.LIDAR_MAP if lidar_ok else Source.ODOMETRY
        return self.source
