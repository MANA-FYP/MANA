"""The position stack (M2): robot_localization's two EKFs and navsat_transform, and MANA's
position-source node. See docs/decisions.md (D1, D2).

    ros2 launch mana_position localisation.launch.py
    ros2 launch mana_position localisation.launch.py wheel_odometry:=/chair/odom imu:=/chair/imu
    ros2 launch mana_position localisation.launch.py use_sim_time:=true    # in Gazebo

The lidar map source is slam_toolbox's localisation mode (D1); its pose goes to lidar/pose. It is
started separately once a map of the building exists.
"""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

TOPICS = {  # launch argument: default topic
    "wheel_odometry": "wheel/odometry",
    "imu": "imu/data",
    "gps": "gps/fix",
    "lidar_pose": "lidar/pose",
}


def generate_launch_description():
    config = str(
        Path(get_package_share_directory("mana_position")) / "config" / "localisation.yaml"
    )
    arg = {name: LaunchConfiguration(name) for name in TOPICS}
    sim = {"use_sim_time": LaunchConfiguration("use_sim_time")}  # Gazebo's clock, for every node
    inputs = [("wheel/odometry", arg["wheel_odometry"]), ("imu/data", arg["imu"])]
    return LaunchDescription(
        [
            *(DeclareLaunchArgument(name, default_value=topic) for name, topic in TOPICS.items()),
            DeclareLaunchArgument("use_sim_time", default_value="false"),
            Node(
                package="robot_localization",
                executable="ekf_node",
                name="ekf_local",
                parameters=[config, sim],
                remappings=[*inputs, ("odometry/filtered", "odometry/local")],
            ),
            Node(
                package="robot_localization",
                executable="ekf_node",
                name="ekf_global",
                parameters=[config, sim],
                remappings=[*inputs, ("odometry/filtered", "odometry/global")],
            ),
            Node(
                package="robot_localization",
                executable="navsat_transform_node",
                name="navsat_transform",
                parameters=[config, sim],
                remappings=[
                    ("imu", arg["imu"]),
                    ("gps/fix", arg["gps"]),
                    ("odometry/filtered", "odometry/global"),
                ],
            ),
            Node(
                package="mana_position",
                executable="position_node",
                parameters=[sim],
                remappings=[
                    ("gps/fix", arg["gps"]),
                    ("lidar/pose", arg["lidar_pose"]),
                    ("odometry/filtered", "odometry/local"),
                ],
            ),
        ]
    )
