# Design decisions

Each decision records what we chose, what else we looked at, and why. A decision can be revisited; when it is, the old entry stays and a new one says what changed.

## D1. Lidar localisation indoors (#43)

**Proposed, to be confirmed in Gazebo (#13, #14):** slam_toolbox. Map a building once with its mapping mode, then run its localisation mode against the saved map. Its pose is the `LIDAR_MAP` position source.

MANA needs to know where the user is inside a building when GPS is gone, so a helper can be told where to come. That needs a position **on a map of the building**. A track of how far the device has moved since it came in drifts and does not name a place.

| Option | What it gives | Sensor | On ROS 2 Jazzy as a package | Fit for "where in the building" |
|---|---|---|---|---|
| FAST-LIO2 | Lidar-inertial odometry, with a local map built as it goes | 3D lidar and a time-synchronised IMU | No: built from source | Odometry only: no saved map to localise against, so it drifts |
| KISS-ICP | Lidar odometry from point clouds, no IMU needed | 3D lidar point clouds | No: its ROS 2 wrapper is built from source | Odometry only, as above |
| slam_toolbox | Mapping, and localisation against a saved map | 2D lidar scan | Yes, `ros-jazzy-slam-toolbox` 2.8.5 | Yes: the pose is on the building's map |
| AMCL (Nav2) | Localisation against a saved map | 2D lidar scan | Yes, `ros-jazzy-nav2-amcl` 1.3.13 | Yes, but it needs a map made by another tool |

How the package column was checked, with `apt-cache policy` on Ubuntu 24.04, against the ROS 2 package lists of 6 October 2026:

```
ros-jazzy-kiss-icp: no package
ros-jazzy-fast-lio: no package
ros-jazzy-slam-toolbox: 2.8.5-1noble.20260915.204151
ros-jazzy-pointcloud-to-laserscan: 2.0.2-3noble.20260920.234619
ros-jazzy-nav2-amcl: 1.3.13-1noble.20260921.024928
ros-jazzy-robot-localization: 3.8.3-1noble.20260915.182602
```

KISS-ICP and FAST-LIO2 have no package. Other lidar odometry does: `ros-jazzy-rko-lio` (lidar-inertial odometry) and `ros-jazzy-mp2p-icp` (an ICP library). They are odometry too, so the point below applies to them as well.

Why slam_toolbox:
- it gives a position on a saved map;
- it both makes the map and localises against it, so one tool covers both steps;
- it installs as a package, so CI and every member's machine get the same version through rosdep.

What slam_toolbox needs, like AMCL:
- **A starting pose on the map** (`map_start_pose`, or the dock where the map was started).
- **A 2D scan.** With a 3D lidar, `pointcloud_to_laserscan` (`ros-jazzy-pointcloud-to-laserscan` 2.0.2) cuts a horizontal slice. A slice taken in the device's frame tilts on the same ramps and stairs that keep `two_d_mode` off in D2, so on a slope the scan meets the floor or the ceiling. The slice should be cut level, in a frame that follows the IMU's roll and pitch, or kept short of the floor. This is to be checked in Gazebo.

FAST-LIO2 and KISS-ICP are odometry. Either could later feed the EKF below as a better motion source than the wheels on stairs, but neither answers the question MANA has to answer indoors.

What would change this:
- the Gazebo lidar is 3D and its scans are poor for 2D mapping;
- slam_toolbox's position error against Gazebo ground truth is too large for a helper to find the user. This is measured in #43.

## D2. Fusing IMU, wheel encoders and GPS (#43)

**Chosen:** robot_localization, as in its own documentation, with two EKFs and navsat_transform (`src/mana_position/config/localisation.yaml`, `launch/localisation.launch.py`).

- **`ekf_local`** fuses the wheel odometry and the IMU in the `odom` frame. It is smooth and drifts, and it is the `ODOMETRY` source (`odometry/local`).
- **`navsat_transform`** turns GPS fixes into positions in the `map` frame (`odometry/gps`).
- **`ekf_global`** also fuses `odometry/gps` in the `map` frame (`odometry/global`), and publishes the `map` to `odom` transform.
- **`two_d_mode` is off**, because the device tilts on ramps and stairs, and the tilt is what the core reads. In 3D every axis must be measured, or its variance grows without bound, so the wheels also give `vz = 0` in the device's own frame, which stays true on a ramp.
- **One absolute heading per filter:** the IMU's yaw. navsat_transform takes its heading from the IMU too (`use_odometry_yaw: false`), because the wheels give only turning speed, not a heading referenced to the earth. Gazebo's IMU gives yaw in the world frame; a real device needs a magnetometer-backed IMU, or another source of heading.
- **`ekf_global` owns the `map` to `odom` transform.** slam_toolbox also publishes one by default, so it runs with `transform_publish_period: 0.0` (off). Its pose goes to the position node on `lidar/pose`, and later into `ekf_global` as a pose input.
- **`use_sim_time`** is a launch argument for every node, so in Gazebo the filters and the position node's freshness checks use the simulation clock.

The topic names are launch arguments, so they can be remapped to the Gazebo bridge (#14) without editing the files.

## D3. What a driver may still do after MANA stops the chair (#76)

**Chosen:** after a stop, no forward motion at all, but the driver may turn on the spot and reverse at up to 0.3 m/s. Turning is capped at 1.0 rad/s at any time, and nothing moves while the slides change between wheels and tracks.

MANA stops the chair for a user's STOP, for a drop or stairs down at the edge, for an obstacle close ahead, and while it asks about stairs down. In each case the hazard, or what the user stopped for, is ahead. Forward is what must not happen, so the drive request's stop caps forward speed at zero for every driver: the supervisor (#77) and the autopilot (#78).

| Option | After a stop | Problem |
|---|---|---|
| Nothing moves | The chair is held still until the stop clears | The stop clears only when the hazard leaves the sensors' view, and the chair cannot leave it: it is stuck at a drop edge or in front of an obstacle |
| **Turn or reverse slowly** | The driver can move away from what is ahead | Turning swings the chair's ends (on tracks the front is 1.1 m from the centre), and nothing senses what is behind |
| Anything below the speed limit | A stop only slows the chair | A stop that does not stop is not a stop |

Why reversing is slow: the chair has no sensor that looks backwards, so a slow reverse leaves the driver time to see what is behind. Turning on the spot near a drop is the open risk; the open test world (#79) and the scored drives (#81) will show whether it needs a rule of its own, such as no turning within `edge_m`.

## D4. A step that only one sensor sees (#103)

**Chosen:** a step up or a curb measured within `edge_m` by one trusted sensor, with no second sensor to confirm it, stops the chair ("Step ahead, stopping"). The stop holds in manual mode too, unless the user has chosen tracks, which is how they cross it on purpose. It does not stop a chair already on tracks. A report without a distance, such as the camera alone seeing a picture of stairs, still only slows down.

The design has two rules: a change of mode needs two independent sensors, and a stop needs none. Before this, a step seen by one sensor only slowed the chair down. In the dark, where the camera cannot be trusted, the 3D camera alone saw the stairs and the curb, and MANA drove onto both on wheels (found while writing the coverage table, #82).

| Option | One-sensor step at the edge | Problem |
|---|---|---|
| Slow down only (before) | The chair rolls onto it on wheels | A mishap whenever the second sensor is lost |
| Change to tracks | It crosses | One sensor would change the mode: the picture of stairs shows why that is not allowed |
| **Stop, and let the user choose tracks** | It waits; the user crosses on purpose | The user has to act; in the dark that is the safe default |


## D5. How long a stop holds (#97)

**Chosen:** a stop for a drop, an obstacle, a lone step or the stairs-down question holds for `hold_s` (0.5 s) after its hazard was last seen. While it holds, a hazard up to `release_m` (0.1 m) past its limit still counts, so it is released only once the hazard is clearly further away (hysteresis). Choosing tracks to cross a step, a yes, and the user's own go are checked every tick and never held. After the clock goes back (a world reset, a bag played again) nothing seen before counts. A yes heard up to `answer_s` (2 s) after the stairs-down question was last asked still answers it, since one reading can end the question a moment before the user's yes is heard.

Before this, each tick decided from the latest reports alone. In the noisy runs of #54, one distance reading just past `edge_m` or `obstacle_stop_m`, or one lost camera report at the stairs-down question, released the stop for a tick, and it happened again and again while the chair waited.

| Option | After a stop within the limit, 200 noisy runs each | Problem |
|---|---|---|
| Latest reports only (before) | the chair moved on in 196 drop, 167 manual-mode drop and 197 dark-corridor runs, by up to 0.24 m | One reading releases a stop; a yes heard on such a tick is lost |
| Hold for `hold_s` only | moved on in 4 drop, 0 manual-mode and 19 dark-corridor runs, by up to 0.07 m | Readings that sit just past the limit still release it once `hold_s` has passed |
| **Hold, and `release_m` past the limit** | 0 of 200 in all three | A hazard that really is gone is waited for 0.5 s more |

Why 0.5 s: it is as long as a report counts (`max_age_s`), so a stop never holds on a hazard older than any report the core would still use. Why 0.1 m: the noise assumed in #54 is 0.02 m plus 5 % of the distance, about 0.04 m at the drop's 0.5 m and 0.06 m at the obstacle's 0.8 m, so a reading past 0.1 m is rare while the hazard is still at the limit.

## D7. Manual mode when the user's input or the ground sensors go (#85)

**Chosen:**
- **The user's input stops.** The user's commands (stop, go, mode, speed) are single events, not a stream, so silence is not a command. MANA keeps manual mode and the speed the user chose. Its stops still apply. The host stands the chair still when the driver's own stream (`cmd_vel`) goes quiet for `watchdog_s` (D3).
- **Every ground sensor is gone.** MANA slows down to `slow` in manual mode too, on the undercarriage the user chose, with the same message as in automatic mode. Before, manual mode came first in the tree and kept the user's speed, with nothing able to see a drop.

| Option | Problem |
|---|---|
| Leave manual mode when the user is silent | A user who drives on without speaking would be taken out of manual mode. That is not their choice, and nothing is unsafe in silence while MANA's stops apply |
| Keep the user's speed with no ground sensor | A drop can no longer be seen, so no stop can come in time at full speed |
| **Slow down, keep the user's undercarriage** | The user drives slower until a ground sensor is back |
| Stop until a ground sensor is back | A dead camera would leave the user stuck where they are. Automatic mode slows down for this, it does not stop |

