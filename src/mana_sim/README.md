# mana_sim

The Gazebo test route and the wheel-and-track test host. This is the first step of the simulation: a
world and a chair that follows the core's `DriveRequest`. Sensors, faults and the scenarios come
next (bridge, faults, ten scenarios, CI smoke test).

## Run it

Needs Ubuntu 24.04 with ROS 2 Jazzy and Gazebo Harmonic (`bash tools/setup_ubuntu.sh`).

```bash
source /opt/ros/jazzy/setup.bash
colcon build --packages-select mana_msgs mana_sim
source install/setup.bash
ros2 launch mana_sim world.launch.py                  # with the Gazebo window
ros2 launch mana_sim world.launch.py headless:=true   # no window
```

The launch file sets the model path, starts Gazebo, bridges the chair's topics to ROS 2 and starts the
host node. It also unpauses the simulation, because the window sometimes starts paused.

Drive the chair by hand (the request must repeat: the chair stops 0.5 s after the last one):

```bash
ros2 topic pub -r 10 /drive_request mana_msgs/msg/DriveRequest \
  "{mode: 0, stop: false, speed_limit: 0.5, reason: test}"      # wheels, half speed
ros2 topic pub -r 10 /drive_request mana_msgs/msg/DriveRequest \
  "{mode: 1, stop: false, speed_limit: 0.3, reason: test}"      # tracks, 30 percent
```

To drive it with the decision core instead, start
`ros2 run mana_core core_node --ros-args -p use_sim_time:=true` in another terminal. The host node runs
on the simulation clock (`use_sim_time`), so the 2.5 s slide switch and the 0.5 s watchdog count
simulation seconds, also when Gazebo runs slower than real time. The core must use the same clock.

## The route

One straight course along +x, 3 m wide. The chair starts at x = 1 m.

![The route from the side: the chair at the start, the ramp, the stairs, the curb and the drop](docs/route.png)

| x (m) | What | What the core should do |
|---|---|---|
| -2 to 8 | level ground | wheels, full speed |
| 8 to 12 | ramp up, 0.5 m rise (7 degrees) | wheels, slow |
| 12 to 14 | plateau, 0.5 m high | |
| 14 to 18 | ramp down | wheels, slow |
| 18 to 22 | level ground | |
| 22 to 22.66 | stairs up: 3 risers of 17 cm (x = 22, 22.33, 22.66) | tracks, slow |
| 22.66 to 26 | top of the stairs, 51 cm high | stay on tracks until level for 2 s |
| 26 to 26.66 | stairs down: 3 risers (x = 26, 26.33, 26.66) | stop and ask |
| 26.66 to 31 | level ground | back on wheels |
| 31 to 34 | curb, 12 cm high | tracks |
| 34 to 38 | level ground | |
| 38 to 46 | a drop of 1 m (the ground ends) | stop 0.5 m before the edge |

## The host

![The test host on its wheels, with the tracks lifted](docs/host.png)

`mana_chair` has a chassis on two sets of undercarriage, each on a vertical slide: four driven wheels
(wheels mode) and two tracks (tracks mode). Only one set touches the ground. `host.py` turns each
drive request into four commands (the two slide positions and a speed for each set). A change of
mode stops the chair, moves the slides (2.5 s) and then drives on the new set. `host_node.py` runs
it in ROS 2, and stops the chair when no request has arrived for `watchdog_s` (0.5 s).

| Topic | Type | Meaning |
|---|---|---|
| `drive_request` | `mana_msgs/DriveRequest` | what the host follows (mode, stop, speed_limit) |
| `/model/mana_chair/ground_truth` | `nav_msgs/Odometry` | where the chair really is |

Speed is `speed_limit` times 1.2 m/s, the same top speed as the scripted world.

## Known limits

- The tracks climb the 17 cm steps (the step height of the scripted world), the ramps and the curb at
  up to 0.5 m/s. Faster, they have not been tried on the stairs.
- The chair drives straight; there is no steering command yet.
- The slides are open loop: both sets move at once for the 2.5 s of a mode change, so for a moment
  neither fully carries the chassis, and the host does not check that the slides have arrived.
- The tracks (belt and sloping ends) are about 2.2 m long, against a 0.9 m chassis: the front of the
  chair is 1.1 m from its centre. Take that into account when a scenario measures the distance to an edge.
- A drive request with a mode other than 0 or 1 is ignored (the chair stands still) and logged.
- No sensors yet (the next issue bridges them).

## Tests

`colcon test --packages-select mana_sim` runs `test/test_host.py` (the mode switch, the speeds, the
stop) without Gazebo.
