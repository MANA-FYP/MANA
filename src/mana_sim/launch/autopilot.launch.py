"""The autopilot (#78): Nav2 drives the chair to a goal, through MANA.

Start the world with a driver and wheel odometry, the core, slam_toolbox localising on a saved map,
the lidar gate and ekf_global (tools/autopilot.sh does all of it), then:

    ros2 launch mana_sim autopilot.launch.py

Nav2's controller and its recovery behaviours publish on cmd_vel/autopilot, so driver_select passes
them on below the supervisor's keyboard, and the host caps them with MANA's decision like any
driver's. Pick a goal with "2D Goal Pose" in RViz, or send one with tools/autopilot_goal.py.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    params = LaunchConfiguration("params")
    default = os.path.join(get_package_share_directory("mana_sim"), "config", "nav2.yaml")
    to_mana = [("cmd_vel", "cmd_vel/autopilot")]
    return LaunchDescription(
        [
            DeclareLaunchArgument("params", default_value=default, description="Nav2 parameters"),
            Node(
                package="nav2_controller",
                executable="controller_server",
                output="screen",
                parameters=[params],
                remappings=to_mana,
            ),
            Node(
                package="nav2_planner",
                executable="planner_server",
                name="planner_server",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="nav2_behaviors",
                executable="behavior_server",
                name="behavior_server",
                output="screen",
                parameters=[params],
                remappings=to_mana,
            ),
            Node(
                package="nav2_bt_navigator",
                executable="bt_navigator",
                name="bt_navigator",
                output="screen",
                parameters=[params],
            ),
            Node(
                package="nav2_lifecycle_manager",
                executable="lifecycle_manager",
                name="lifecycle_manager_navigation",
                output="screen",
                parameters=[params],
            ),
        ]
    )
