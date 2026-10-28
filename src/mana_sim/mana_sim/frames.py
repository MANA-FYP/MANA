"""Where each sensor sits on the chair (the frame ids of the ROS 2 messages) and the maths to turn
that into a transform. The poses are the same as in models/mana_chair/model.sdf; a test compares
them.

Frames follow REP 103 (x forward, y left, z up; a positive pitch looks down) and REP 105:
map > odom > base_link > one frame per sensor. base_link is the chassis of the chair.
"""

import math

MAP = "map"
ODOM = "odom"
BASE = "base_link"

# sensor frame -> (x, y, z, roll, pitch, yaw) from base_link, in metres and radians
SENSORS = {
    "camera_link": (0.35, 0.0, 0.65, 0.0, 0.3, 0.0),
    "depth_link": (0.35, 0.0, 0.6, 0.0, 0.6, 0.0),
    "lidar_link": (0.4, 0.0, 0.25, 0.0, 0.0, 0.0),
    "imu_link": (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "gps_link": (-0.3, 0.0, 0.75, 0.0, 0.0, 0.0),
}


def quaternion(roll: float, pitch: float, yaw: float) -> tuple[float, float, float, float]:
    """(x, y, z, w) for a rotation of yaw about z, then pitch about y, then roll about x."""
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (
        sr * cp * cy - cr * sp * sy,
        cr * sp * cy + sr * cp * sy,
        cr * cp * sy - sr * sp * cy,
        cr * cp * cy + sr * sp * sy,
    )
