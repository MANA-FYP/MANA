"""Core demo: the decision core and one scripted scenario, live on ROS 2 topics.

    ros2 launch mana_core demo.launch.py scenario:=stairs

Scenarios: level, ramp, stairs, curb, stop, drop, manual_drop, picture, dark, imu_lost.
Everything stops when the scenario has printed its pass marks.
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, EmitEvent, RegisterEventHandler
from launch.event_handlers import OnProcessExit
from launch.events import Shutdown
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    scenario = Node(
        package="mana_core",
        executable="scenario_node",
        parameters=[{"scenario": LaunchConfiguration("scenario")}],
        output="screen",
        emulate_tty=True,
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument("scenario", default_value="stairs"),
            Node(package="mana_core", executable="core_node", output="screen"),
            scenario,
            RegisterEventHandler(
                OnProcessExit(target_action=scenario, on_exit=[EmitEvent(event=Shutdown())])
            ),
        ]
    )
