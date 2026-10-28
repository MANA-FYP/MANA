"""Start the MANA test route in Gazebo with the test host, and the host node that follows the core.

    ros2 launch mana_sim world.launch.py              # with the Gazebo window
    ros2 launch mana_sim world.launch.py headless:=true   # no window (server only), for CI
    ros2 launch mana_sim world.launch.py stub:=true       # also the scripted perception stub
    ros2 launch mana_sim world.launch.py detector:=true   # also the object detector on the camera

The launch file sets the model path, starts Gazebo, bridges the chair's topics to ROS 2
(config/bridge.yaml) and starts the host node, the sensors node and the depth node (the ground
reports from the 3D camera, told where the camera is mounted). With detector:=true it also starts
the object detector on the RGB camera, which needs `pip install ultralytics`. The core node is
started separately, on the same clock:
ros2 run mana_core core_node --ros-args -p use_sim_time:=true
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (
    DeclareLaunchArgument,
    ExecuteProcess,
    SetEnvironmentVariable,
    TimerAction,
)
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from mana_sim.frames import depth_mount


def generate_launch_description():
    share = get_package_share_directory("mana_sim")
    world = os.path.join(share, "worlds", "mana_route.sdf")
    models = os.path.join(share, "models")
    depth_height_m, depth_pitch_rad = depth_mount()
    return LaunchDescription(
        [
            DeclareLaunchArgument("headless", default_value="false", description="no window"),
            DeclareLaunchArgument(
                "stub", default_value="false", description="also start the scripted perception stub"
            ),
            DeclareLaunchArgument(
                "detector", default_value="false", description="also start the object detector"
            ),
            DeclareLaunchArgument(
                "weights", default_value="yolo26n.pt", description="the object detector's weights"
            ),
            SetEnvironmentVariable(
                "GZ_SIM_RESOURCE_PATH",
                models + os.pathsep + os.environ.get("GZ_SIM_RESOURCE_PATH", ""),
            ),
            ExecuteProcess(
                cmd=["gz", "sim", "-r", world],
                output="screen",
                condition=UnlessCondition(LaunchConfiguration("headless")),
            ),
            ExecuteProcess(
                cmd=["gz", "sim", "-r", "-s", "--headless-rendering", world],
                output="screen",
                condition=IfCondition(LaunchConfiguration("headless")),
            ),
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
            Node(
                package="mana_sim",
                executable="sensors_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
            # ground reports from the simulated 3D camera (depth/image, depth/camera_info), told
            # where the camera is: the reading compares the nearest ground with the mounting height
            Node(
                package="mana_perception",
                executable="depth_node",
                output="screen",
                parameters=[
                    {
                        "use_sim_time": True,
                        "camera_height_m": depth_height_m,
                        "camera_pitch_rad": depth_pitch_rad,
                    }
                ],
            ),
            # obstacles from the simulated RGB camera (camera/image, camera/camera_info)
            Node(
                package="mana_perception",
                executable="detector_node",
                output="screen",
                parameters=[{"use_sim_time": True, "weights": LaunchConfiguration("weights")}],
                condition=IfCondition(LaunchConfiguration("detector")),
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
