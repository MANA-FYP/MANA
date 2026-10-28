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


# The chassis would ride this high on either set with rigid slides (models/mana_chair/model.sdf):
# the wheel centres are 0.22 m under it with a 0.15 m radius, and the track boxes are 0.28 m under
# it, 0.18 m tall.
CHASSIS_HEIGHT_M = 0.37
# Where it settles in Gazebo. The lift controllers are P only, so the slides give under load: about
# 3 cm on wheels (65 kg on wheel_lift) and 1 cm on tracks. Measured on #92.
SETTLED_CHASSIS_M = {"wheels": 0.33, "tracks": 0.36}


def depth_mount() -> tuple[float, float]:
    """(height above the ground, downward tilt) of the 3D camera: depth_node's camera_height_m and
    camera_pitch_rad, which its ground reading depends on. The height is the middle of the two
    settled heights, so it is within 2 cm of where the camera really is on either set."""
    _x, _y, z, _roll, pitch, _yaw = SENSORS["depth_link"]
    settled = sum(SETTLED_CHASSIS_M.values()) / len(SETTLED_CHASSIS_M)
    return round(settled + z, 3), pitch


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
