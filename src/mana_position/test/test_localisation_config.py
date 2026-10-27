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
    assert {"roll", "pitch", "yaw"} <= fused(node, "imu0")
    # the wheels give speed, not position; vz = 0 in the device's frame keeps z measured in 3D
    assert fused(node, "odom0") == {"vx", "vy", "vz", "vyaw"}


@pytest.mark.parametrize("node", ["ekf_local", "ekf_global"])
def test_each_filter_has_exactly_one_absolute_heading(node):
    sensors = [
        k[: -len("_config")] for k in CONFIG[node]["ros__parameters"] if k.endswith("_config")
    ]
    assert [s for s in sensors if "yaw" in fused(node, s)] == ["imu0"]


def test_the_local_filter_stays_in_odom_and_the_global_one_adds_gps_in_map():
    assert CONFIG["ekf_local"]["ros__parameters"]["world_frame"] == "odom"
    assert "odom1" not in CONFIG["ekf_local"]["ros__parameters"]
    glob = CONFIG["ekf_global"]["ros__parameters"]
    assert glob["world_frame"] == "map"
    assert (glob["odom1"], fused("ekf_global", "odom1")) == ("odometry/gps", {"x", "y"})


def test_navsat_takes_its_heading_from_the_imu_not_from_the_filter():
    # use_odometry_yaw needs an earth-referenced heading in the odometry, which the wheels lack
    assert CONFIG["navsat_transform"]["ros__parameters"]["use_odometry_yaw"] is False


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


def resolved(node):
    """A launch Node's remappings and parameters with the launch arguments at their defaults."""
    from launch import LaunchContext
    from launch.utilities import normalize_to_list_of_substitutions, perform_substitutions

    context = LaunchContext()
    for name, default in DEFAULTS.items():
        context.launch_configurations[name] = default

    def text(value):
        return perform_substitutions(context, normalize_to_list_of_substitutions(value))

    remaps = {text(a): text(b) for a, b in node._Node__remappings or []}
    params = [p for p in node._Node__parameters or [] if isinstance(p, dict)]
    sim = {text(k): text(v) for d in params for k, v in d.items()}
    return remaps, sim


DEFAULTS = {
    "wheel_odometry": "wheel/odometry",
    "imu": "imu/data",
    "gps": "gps/fix",
    "lidar_pose": "lidar/pose",
    "use_sim_time": "false",
}


def test_the_launch_file_wires_the_topics_together_and_passes_sim_time():
    nodes = launch_nodes()
    wiring = [resolved(n) for n in nodes]
    local, glob, navsat, position = (remaps for remaps, _ in wiring)
    assert local["odometry/filtered"] == "odometry/local"
    assert glob["odometry/filtered"] == "odometry/global"
    assert (navsat["odometry/filtered"], navsat["gps/fix"], navsat["imu"]) == (
        "odometry/global",
        "gps/fix",
        "imu/data",
    )
    assert position["odometry/filtered"] == "odometry/local"  # the ODOMETRY source drifts
    assert position["lidar/pose"] == "lidar/pose"
    assert CONFIG["ekf_global"]["ros__parameters"]["odom1"] == "odometry/gps"  # from navsat
    assert all(sim == {"use_sim_time": "false"} for _, sim in wiring)
