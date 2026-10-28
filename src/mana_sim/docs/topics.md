# Topics of the simulation

What `ros2 launch mana_sim world.launch.py` puts on ROS 2. The bridge is `config/bridge.yaml`; the sensors are
in `models/mana_chair/model.sdf`. Rates are in simulation seconds (`ros2 topic hz --use-sim-time`);
`tools/check_topic_rates.sh` checks them, within 15 percent.

Frames follow REP 103 (x forward, y left, z up; a positive pitch looks down) and REP 105:
`map` > `odom` > `base_link` > one frame per sensor. `base_link` is the chassis of the chair.

## Sensors (Gazebo to ROS 2)

| Topic | Type | Rate | Frame | Where it comes from |
|---|---|---|---|---|
| `camera/image` | `sensor_msgs/Image` (rgb8, 640x480) | 15 Hz | `camera_link` | RGB camera |
| `camera/camera_info` | `sensor_msgs/CameraInfo` | 15 Hz | `camera_link` | RGB camera |
| `depth/image` | `sensor_msgs/Image` (32FC1, 320x240, 0.3 to 6 m) | 10 Hz | `depth_link` | 3D camera |
| `depth/camera_info` | `sensor_msgs/CameraInfo` | 10 Hz | `depth_link` | 3D camera |
| `scan` | `sensor_msgs/LaserScan` (180 degrees ahead, 180 echoes, 0.3 to 12 m) | 10 Hz | `lidar_link` | 2D lidar |
| `imu` | `sensor_msgs/Imu` | 100 Hz | `imu_link` | IMU |
| `gps/fix` | `sensor_msgs/NavSatFix` | 5 Hz | `gps_link` | GPS |
| `joint_states` | `sensor_msgs/JointState` (the four wheels: angle and speed) | 250 Hz (every physics step) | none | wheel encoders |
| `/model/mana_chair/ground_truth` | `nav_msgs/Odometry` | 20 Hz | `odom` | where the chair really is |
| `/clock` | `rosgraph_msgs/Clock` | every physics step | none | simulation time |

Every node that reads these must run on simulation time (`-p use_sim_time:=true`).

## What `sensors_node` makes of them

| Topic | Type | Rate | Frame | How |
|---|---|---|---|---|
| `odom` | `nav_msgs/Odometry` | 20 Hz | `odom` > `base_link` | the ground truth, with the frame ids of REP 105 |
| `motion` | `mana_msgs/MotionReport` | 100 Hz | `base_link` | pitch and roll from the IMU orientation; `wheel_slip` when the wheels turn at a different speed than the chair moves (only judged in wheels mode) |
| `position` | `mana_msgs/PositionReport` | 5 Hz | `map` | the GPS fix as metres east and north of the origin of the world; error 1.5 m (the simulated GPS gives no covariance) |
| `obstacle` | `mana_msgs/ObstacleReport` | 10 Hz, only when something is there | `lidar_link` | the nearest lidar echo within 0.35 rad of straight ahead and 4 m, source LIDAR |
| `link` | `mana_msgs/LinkReport` | 2 Hz | none | a healthy Wi-Fi link; the faults degrade it |

It also broadcasts the transforms: `map` > `odom` (identity), `odom` > `base_link` (from the ground truth) and
`base_link` > each sensor frame (`mana_sim/frames.py`, the same poses as the model; a test compares them).

## Ground reports

`depth_node` (package `mana_perception`) reads `depth/image` and `depth/camera_info` and publishes `ground`,
with `camera_height_m` 0.97 and `camera_pitch_rad` 0.6 from `frames.depth_mount()`. Its blind zone with this
camera is about 0.53 m. `tests/test_sim_route_depth.py` checks it on this route's own hazards, rendered with this
camera. The scripted stub is off in this launch; `stub:=true` starts it too.

With `detector:=true`, `detector_node` (package `mana_perception`) reads `camera/image` and `camera/camera_info`
and publishes the nearest obstacle on `obstacle`, source DETECTOR. It needs Ultralytics.

## Towards the chair (ROS 2 to Gazebo)

| Topic | Type | Meaning |
|---|---|---|
| `/model/mana_chair/wheels_cmd_vel`, `tracks_cmd_vel` | `geometry_msgs/Twist` | speed of the set that is down |
| `/model/mana_chair/joint/wheel_lift/cmd_pos`, `track_lift_left`, `track_lift_right` | `std_msgs/Float64` | the position of the slides |

The host node sends these; it reads `drive_request`.
