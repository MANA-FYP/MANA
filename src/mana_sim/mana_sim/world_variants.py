"""Variants of the world file made when the launch file starts: no ROS.

A machine with no GPU (the CI runner) cannot render the cameras and the lidar. Without the
Sensors system of Gazebo they never start, and the rest of the simulation (physics, IMU, GPS, the
host) runs.
"""

import xml.etree.ElementTree as ET

SENSORS_SYSTEM = "gz-sim-sensors-system"


def without_rendering_sensors(sdf: str) -> str:
    """The world with the Sensors system (cameras and lidar) taken out. Comments are kept."""
    root = ET.fromstring(sdf, ET.XMLParser(target=ET.TreeBuilder(insert_comments=True)))
    world = root.find("world")
    found = [] if world is None else world.findall("plugin")
    found = [p for p in found if p.get("filename") == SENSORS_SYSTEM]
    if len(found) != 1:
        raise ValueError(f"expected one Sensors system in the world, found {len(found)}")
    world.remove(found[0])
    return '<?xml version="1.0"?>\n' + ET.tostring(root, encoding="unicode") + "\n"
