"""Start the MANA test route in Gazebo with the test host, and the host node that follows the core.

    ros2 launch mana_sim world.launch.py              # with the Gazebo window
    ros2 launch mana_sim world.launch.py headless:=true   # no window (server only), for CI
    ros2 launch mana_sim world.launch.py stub:=true       # also the scripted perception stub
    ros2 launch mana_sim world.launch.py sensors:=false   # no cameras and lidar (no GPU, as in CI)

The launch file sets the model path, starts Gazebo, bridges the chair's topics to ROS 2
(config/bridge.yaml) and starts the host node, the sensors node and the depth node (the ground
reports from the 3D camera). The core node is started separately, on the same clock:
ros2 run mana_core core_node --ros-args -p use_sim_time:=true
"""

import os
import tempfile

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    OpaqueFunction,
    SetEnvironmentVariable,
    TimerAction,
)
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from mana_sim.world_variants import without_rendering_sensors


def bare_world(world: str) -> str:
    """A copy of the world with no cameras and no lidar, in a folder of this user. It is written to
    a file of its own and moved into place, so two launches at once never read a half-written one."""
    with open(world) as f:
        text = without_rendering_sensors(f.read())
    folder = os.path.join(tempfile.gettempdir(), f"mana_sim_{os.getuid()}")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    fd, part = tempfile.mkstemp(dir=folder, suffix=".sdf")
    with os.fdopen(fd, "w") as out:
        out.write(text)
    path = os.path.join(folder, "mana_route_no_rendering.sdf")
    os.replace(part, path)
    return path


def gazebo(context):
    """Gazebo, with the window or headless. sensors:=false starts it on the world with no cameras
    and no lidar (a machine with no GPU), which is made only then."""
    world = os.path.join(get_package_share_directory("mana_sim"), "worlds", "mana_route.sdf")
    if LaunchConfiguration("sensors").perform(context) == "false":
        world = bare_world(world)
    headless = LaunchConfiguration("headless").perform(context) == "true"
    server = ["-s", "--headless-rendering"] if headless else []
    return [ExecuteProcess(cmd=["gz", "sim", "-r", *server, world], output="screen")]


def generate_launch_description():
    share = get_package_share_directory("mana_sim")
    models = os.path.join(share, "models")
    return LaunchDescription(
        [
            DeclareLaunchArgument("headless", default_value="false", description="no window"),
            DeclareLaunchArgument(
                "sensors",
                default_value="true",
                description="false: no cameras and no lidar (a machine with no GPU)",
            ),
            DeclareLaunchArgument(
                "stub", default_value="false", description="also start the scripted perception stub"
            ),
            SetEnvironmentVariable(
                "GZ_SIM_RESOURCE_PATH",
                models + os.pathsep + os.environ.get("GZ_SIM_RESOURCE_PATH", ""),
            ),
            OpaqueFunction(function=gazebo),
            # the window sometimes starts paused: make sure the simulation runs
            TimerAction(
                period=8.0,
                actions=[
                    ExecuteProcess(
                        cmd=[
                            "gz",
                            "service",
                            "-s",
                            "/world/mana_route/control",
                            "--reqtype",
                            "gz.msgs.WorldControl",
                            "--reptype",
                            "gz.msgs.Boolean",
                            "--timeout",
                            "3000",
                            "--req",
                            "pause: false",
                        ],
                    )
                ],
            ),
            Node(
                package="ros_gz_bridge",
                executable="parameter_bridge",
                parameters=[{"config_file": os.path.join(share, "config", "bridge.yaml")}],
                output="screen",
            ),
            # on the simulation clock: the 2.5 s slide switch and the watchdog count its seconds
            Node(
                package="mana_sim",
                executable="host_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
            # the sensor faults sit between the bridge and the others (see docs/faults.md)
            Node(
                package="mana_sim",
                executable="fault_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
            Node(
                package="mana_sim",
                executable="sensors_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
            # ground reports from the simulated 3D camera (depth/image, depth/camera_info)
            Node(
                package="mana_perception",
                executable="depth_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
            Node(
                package="mana_perception",
                executable="stub_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
                condition=IfCondition(LaunchConfiguration("stub")),
            ),
        ]
    )
