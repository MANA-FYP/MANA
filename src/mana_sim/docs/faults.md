# Sensor faults

Every fault of the scripted world can be switched on and off during a run in Gazebo, so a failure can be
shown live. `fault_node` sits between the bridge and the rest of MANA: the bridge publishes each sensor
that can fail on a `*_raw` topic, and the node passes it on under the name the others read. While no fault is
on, the message goes through unchanged.

```
Gazebo > bridge > camera/image_raw > fault_node > camera/image > perception, core
```

## Switching a fault

```bash
ros2 service call /set_fault mana_msgs/srv/SetFault \
  "{sensor: gps, kind: dropout, active: true, value: 0.0}"     # on
ros2 service call /set_fault mana_msgs/srv/SetFault \
  "{sensor: gps, kind: dropout, active: false}"                # off
ros2 topic echo /fault_state                                   # which faults are on
```

`value` is the strength; 0 means the default. A fault that does not exist (for example noise on the Wi-Fi) is
refused, and the answer says why. Run the nodes on simulation time (`-p use_sim_time:=true`).

| Sensor | Kind | What it does | Default strength (`value`) |
|---|---|---|---|
| camera, depth, lidar, imu, gps | `noise` | adds Gaussian noise: 20 grey levels, 5 cm, 5 cm, 0.05 rad/s and m/s^2, 3 m. A GPS gets the matching covariance, so the `position` error grows | 1 (times the sizes on the left) |
| camera, depth, lidar, imu, gps | `dropout` | the sensor sends nothing (a GPS `dropout` is the GPS loss) | none |
| camera | `dark` | the image keeps a share of its light | 0.05 |
| wifi | `fade` | more and more of the Wi-Fi heartbeats are lost, until none gets through after this many seconds; `link_node` then reports missed heartbeats and the link gone, and MANA changes to Bluetooth | 10 (seconds) |
| wheels | `slip` | the wheel encoders (`joint_states`) read this many times faster than the chair moves, so `motion` says `wheel_slip` on wheels | 2 |

## Scenarios

One scenario for each fault, played on the running simulation and core:

```bash
ros2 run mana_sim fault_script                       # lists them
ros2 run mana_sim fault_script gps_loss --ros-args -p use_sim_time:=true
```

Each switches its fault on at 10 s and off at 30 s (simulation time) and ends.

| Scenario | Fault | What the core should do |
|---|---|---|
| `depth_noise` | 3D camera noise, 4 times the usual | ground reports get less certain; a flat floor is not called a step |
| `lidar_dropout` | the lidar sends nothing | no obstacle reports; the other sources carry on |
| `gps_loss` | the GPS sends nothing | no position reports; the position source switches |
| `wifi_fade` | Wi-Fi fades in 10 s | the link falls, then MANA slows down and changes link |
| `wheel_slip` | the encoders read twice the speed | a slip report; on wheels MANA changes to tracks |
| `darkness` | the camera keeps 5 percent of the light | the camera is distrusted; the 3D camera carries on |
| `imu_lost` | the IMU sends nothing | no motion reports; MANA does not rely on tilt |

The scenarios say what to expect in words. Scoring them with pass marks comes with the scenario runner
(the ten scenarios in Gazebo).

## Known limits

- The faults are made on the messages, not in the physics. Wheel slip is the encoders reading fast: the chair
  does not really slide. Darkness darkens the image; the light in the world stays on.
- `fault_node` also sends the heartbeats of the two radios (once a second); `link_node` of `mana_connectivity` turns them into the `link` reports. The Bluetooth heartbeats never stop.
- Images are copied through Python: at the sizes of the simulation this is fine, a larger camera would need a faster path.
