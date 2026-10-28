"""Start the MANA test route in Gazebo with the test host, and the host node that follows the core.

    ros2 launch mana_sim world.launch.py              # with the Gazebo window
    ros2 launch mana_sim world.launch.py headless:=true   # no window (server only), for CI

The launch file sets the model path, starts Gazebo, bridges the chair's topics to ROS 2 and starts
the host node. The core node is started separately, on the same clock:
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

CHAIR = "/model/mana_chair"
BRIDGE = [
    # ROS 2 -> Gazebo: what the host node asks of the chair
    f"{CHAIR}/wheels_cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist",
    f"{CHAIR}/tracks_cmd_vel@geometry_msgs/msg/Twist]gz.msgs.Twist",
    f"{CHAIR}/joint/wheel_lift/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double",
    f"{CHAIR}/joint/track_lift_left/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double",
    f"{CHAIR}/joint/track_lift_right/cmd_pos@std_msgs/msg/Float64]gz.msgs.Double",
    # Gazebo -> ROS 2: where the chair is, and the simulation clock
    f"{CHAIR}/ground_truth@nav_msgs/msg/Odometry[gz.msgs.Odometry",
    "/clock@rosgraph_msgs/msg/Clock[gz.msgs.Clock",
]


def generate_launch_description():
    share = get_package_share_directory("mana_sim")
    world = os.path.join(share, "worlds", "mana_route.sdf")
    models = os.path.join(share, "models")
    return LaunchDescription(
        [
            DeclareLaunchArgument("headless", default_value="false", description="no window"),
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
                cmd=["gz", "sim", "-r", "-s", world],
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
                arguments=BRIDGE,
                output="screen",
            ),
            # on the simulation clock: the 2.5 s slide switch and the watchdog count its seconds
            Node(
                package="mana_sim",
                executable="host_node",
                output="screen",
                parameters=[{"use_sim_time": True}],
            ),
        ]
    )
