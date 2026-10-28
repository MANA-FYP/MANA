"""Variants of the world file made when the launch file starts: pure text, no ROS.

A machine with no GPU (the CI runner) cannot render the cameras and the lidar. Without the
Sensors system of Gazebo they never start, and the rest of the simulation (physics, IMU, GPS, the
host) runs.
"""

import re

SENSORS_SYSTEM = re.compile(r'\s*<plugin filename="gz-sim-sensors-system".*?</plugin>', re.DOTALL)


def without_rendering_sensors(sdf: str) -> str:
    """The world with the Sensors system (cameras and lidar) taken out."""
    out, count = SENSORS_SYSTEM.subn("", sdf)
    if count != 1:
        raise ValueError(f"expected one Sensors system in the world, found {count}")
    return out
