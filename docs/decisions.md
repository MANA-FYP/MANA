# Design decisions

Each decision records what we chose, what else we looked at, and why. A decision can be revisited; when it is, the old entry stays and a new one says what changed.

## D1. Lidar localisation indoors (#43)

**Proposed, to be confirmed in Gazebo (#13, #14):** slam_toolbox. Map a building once with its mapping mode, then run its localisation mode against the saved map. Its pose is the `LIDAR_MAP` position source.

MANA needs to know where the user is inside a building when GPS is gone, so a helper can be told where to come. That needs a position **on a map of the building**. A track of how far the device has moved since it came in drifts and does not name a place.

| Option | What it gives | Sensor | On ROS 2 Jazzy as a package | Fit for "where in the building" |
|---|---|---|---|---|
| FAST-LIO2 | Lidar-inertial odometry, with a local map built as it goes | 3D lidar and a time-synchronised IMU | No, a source build (checked with `apt-cache policy`, 27 Oct 2026) | Odometry only: no saved map to localise against, so it drifts |
| KISS-ICP | Lidar odometry from point clouds, no IMU needed | 3D lidar point clouds | No, a source build (same check) | Odometry only, as above |
| slam_toolbox | Mapping, and localisation against a saved map | 2D lidar scan | Yes, `ros-jazzy-slam-toolbox` 2.8.5 | Yes: the pose is on the building's map |
| AMCL (Nav2) | Localisation against a saved map | 2D lidar scan | Yes, `ros-jazzy-nav2-amcl` 1.3.13 | Yes, but it needs a map made by another tool and an initial pose |

Why slam_toolbox:
- it gives a position on a saved map;
- it both makes the map and localises against it, so one tool covers both steps;
- it installs as a package, so CI and every member's machine get the same version through rosdep.

FAST-LIO2 and KISS-ICP are odometry. Either could later feed the EKF below as a better motion source than the wheels on stairs, but neither answers the question MANA has to answer indoors.

What would change this:
- the Gazebo lidar is 3D and its scans are poor for 2D mapping;
- slam_toolbox's position error against Gazebo ground truth is too large for a helper to find the user. This is measured in #43.

## D2. Fusing IMU, wheel encoders and GPS (#43)

**Chosen:** robot_localization, as in its own documentation, with two EKFs and navsat_transform (`src/mana_position/config/localisation.yaml`, `launch/localisation.launch.py`).

- **`ekf_local`** fuses the wheel odometry and the IMU in the `odom` frame. It is smooth and drifts, and it is the `ODOMETRY` source (`odometry/local`).
- **`navsat_transform`** turns GPS fixes into positions in the `map` frame (`odometry/gps`).
- **`ekf_global`** also fuses `odometry/gps` in the `map` frame (`odometry/global`), and publishes the `map` to `odom` transform.
- **`two_d_mode` is off**, because the device tilts on ramps and stairs, and the tilt is what the core reads.

The topic names are launch arguments, so they can be remapped to the Gazebo bridge (#14) without editing the files.
