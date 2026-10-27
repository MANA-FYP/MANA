"""The robot_localization setup: the config is complete and the launch file wires it together."""

import importlib.util
from pathlib import Path

import pytest
import yaml

PKG = Path(__file__).parents[1]
CONFIG = yaml.safe_load((PKG / "config" / "localisation.yaml").read_text())
FIELDS = ["x", "y", "z", "roll", "pitch", "yaw", "vx", "vy", "vz"]
FIELDS += ["vroll", "vpitch", "vyaw", "ax", "ay", "az"]


def fused(node, sensor):
    values = CONFIG[node]["ros__parameters"][f"{sensor}_config"]
    assert len(values) == 15
    return {f for f, on in zip(FIELDS, values, strict=True) if on}


@pytest.mark.parametrize("node", ["ekf_local", "ekf_global"])
def test_both_filters_track_the_tilt_in_3d_and_share_their_frames(node):
    p = CONFIG[node]["ros__parameters"]
    assert p["two_d_mode"] is False  # ramps and stairs tilt the device
    assert (p["map_frame"], p["odom_frame"], p["base_link_frame"]) == ("map", "odom", "base_link")
    assert {"roll", "pitch"} <= fused(node, "imu0")
    assert fused(node, "odom0") == {"vx", "vy", "vyaw"}  # the wheels give speed, not position


def test_the_local_filter_stays_in_odom_and_the_global_one_adds_gps_in_map():
    assert CONFIG["ekf_local"]["ros__parameters"]["world_frame"] == "odom"
    assert "odom1" not in CONFIG["ekf_local"]["ros__parameters"]
    glob = CONFIG["ekf_global"]["ros__parameters"]
    assert glob["world_frame"] == "map"
    assert (glob["odom1"], fused("ekf_global", "odom1")) == ("odometry/gps", {"x", "y"})


def test_navsat_takes_its_heading_from_the_filter():
    assert CONFIG["navsat_transform"]["ros__parameters"]["use_odometry_yaw"] is True


def launch_nodes():
    pytest.importorskip("launch_ros")
    spec = importlib.util.spec_from_file_location(
        "launch_file", PKG / "launch" / "localisation.launch.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.get_package_share_directory = lambda _: str(PKG)  # works before install too
    from launch_ros.actions import Node

    return [a for a in module.generate_launch_description().entities if isinstance(a, Node)]


def test_the_launch_file_starts_both_filters_navsat_and_the_position_node():
    names = [(n.node_package, n.node_executable) for n in launch_nodes()]
    assert names == [
        ("robot_localization", "ekf_node"),
        ("robot_localization", "ekf_node"),
        ("robot_localization", "navsat_transform_node"),
        ("mana_position", "position_node"),
    ]
