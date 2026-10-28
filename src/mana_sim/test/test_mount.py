"""The 3D camera's mount that depth_node is told, against the model. No ROS, no Gazebo."""

import re
from pathlib import Path

import pytest
from mana_sim.frames import CHASSIS_HEIGHT_M, SENSORS, depth_mount

MODEL = (Path(__file__).parents[1] / "models" / "mana_chair" / "model.sdf").read_text()


def link_pose(name: str) -> list[float]:
    block = re.search(rf'<link name="{name}">(.*?)</link>', MODEL, re.S).group(1)
    return [float(v) for v in re.search(r"<pose>([^<]+)</pose>", block).group(1).split()]


def test_the_chassis_height_is_where_the_wheels_and_the_tracks_hold_it():
    wheel = link_pose("wheel_fl")
    radius = float(re.search(r"<radius>([\d.]+)</radius>", MODEL).group(1))
    assert -wheel[2] + radius == pytest.approx(CHASSIS_HEIGHT_M)
    track = link_pose("left_track")
    block = re.search(r'<link name="left_track">(.*?)</link>', MODEL, re.S).group(1)
    tall = float(re.search(r"<size>[\d.]+ [\d.]+ ([\d.]+)</size>", block).group(1))
    assert -track[2] + tall / 2 == pytest.approx(CHASSIS_HEIGHT_M)


def test_depth_node_is_told_the_height_and_tilt_of_the_3d_camera():
    height, pitch = depth_mount()
    assert height == pytest.approx(CHASSIS_HEIGHT_M + SENSORS["depth_link"][2])
    assert height == pytest.approx(0.97)
    assert pitch == pytest.approx(SENSORS["depth_link"][4]) and pitch == pytest.approx(0.6)
