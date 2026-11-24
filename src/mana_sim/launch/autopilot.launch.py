"""The autopilot (#78): Nav2 drives the chair to a goal, through MANA.

Start the world with a driver and wheel odometry, the core, slam_toolbox localising on a saved map,
the lidar gate and ekf_global (tools/autopilot.sh does all of it), then:

    ros2 launch mana_sim autopilot.launch.py

Nav2's controller publishes on cmd_vel/autopilot, so driver_select passes it on below the
supervisor's keyboard, and the host caps it with MANA's decision like any driver's. The
behaviour tree has no recoveries (config/navigate_no_recovery.xml): a goal that makes no progress
for 60 s is aborted. The behaviour server still runs (remapped to cmd_vel/autopilot too) for a
tree that asks for one. Pick a goal with "2D Goal Pose" in RViz, or send one with
tools/autopilot_goal.py.
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    params = LaunchConfiguration("params")
    config = os.path.join(get_package_share_directory("mana_sim"), "config")
    default = os.path.join(config, "nav2.yaml")
    # no recoveries: a goal stalled by a long MANA stop is aborted, not retried (#187)
    tree = {"default_nav_to_pose_bt_xml": os.path.join(config, "navigate_no_recovery.xml")}
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
                parameters=[params, tree],
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
