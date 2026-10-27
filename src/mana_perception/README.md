# mana_perception

Perception for MANA (M1 Vision). It holds:

- **`depth_node`**: the ground ahead from the 3D camera (flat, ramp, curb, stairs up, stairs down
  or a drop), the second ground source the decision core's two-sensor rule needs.
- **`stub_node`**: scripted ground and obstacle reports on the real message interface, so the
  decision core can be run and tested before the other models exist.

## Ground from the 3D camera

```bash
ros2 run mana_perception depth_node --ros-args   -r depth/image:=/camera/depth/image_raw -r depth/camera_info:=/camera/depth/camera_info   -p camera_height_m:=1.0 -p camera_pitch_rad:=0.6
```

The work is in `mana_perception/depth.py`, which is pure numpy, so it is tested without ROS. Every
pixel in a strip down the middle of the image becomes a point in a level frame (distance ahead,
height above the ground under the chair), and the median height in 5 cm bins gives the ground
profile along the path. Read from near to far:

- a short, fast change in height between level stretches is a **step**: one step up to 0.2 m is a
  **curb**, more than one is **stairs up**; going down, one step up to 0.2 m is a curb, more than
  one is **stairs down**, and anything deeper is a **drop**;
- a lower level seen past ground the camera could not see is the edge of a drop or a stair tread;
- ground the camera should see but does not, with nothing after it, is a **drop** of unknown
  height (a ledge with the floor out of sight);
- a rise taller than 0.25 m is a wall or an object, not a step: the obstacle sensors handle it;
- a steady slope above 5 % seen over at least 30 cm is a **ramp**;
- the camera cannot see the ground right in front of the chair (its **blind zone**, where the
  bottom row of the image meets the ground: about 0.5 m with a D435-like camera, 0.37 m with the
  test camera). If the nearest ground it does see is more than 6 cm lower or higher than the
  ground the chair stands on, the edge is somewhere in that zone, and it is reported **at 0 m**
  with its measured height, the worst case. If it sees nothing near but valid depth far beyond,
  the floor is out of range: a **drop** at 0 m.

The 3D camera is where stairs down and drops come from: the camera classifier is trained on
StairNet, which has only stairs going up. The core stops at a drop or stairs down within 0.5 m
even when only this sensor sees it (#60).

| Topic | Type | Meaning |
|---|---|---|
| `depth/image` (in) | `sensor_msgs/Image` | depth, 32FC1 in metres or 16UC1 in millimetres (REP 118); a frame stamped 0 gets the time it arrived |
| `depth/camera_info` (in) | `sensor_msgs/CameraInfo` | focal lengths and principal point |
| `ground` (out) | `mana_msgs/GroundReport` | one report per frame, source `DEPTH_CAMERA` |

| Parameter | Default | Meaning |
|---|---|---|
| `camera_height_m` | `1.0` | optical centre above the ground; it must be right to about 5 cm |
| `camera_pitch_rad` | `0.6` | tilt below level (34 degrees); it must be right to about 2 degrees |
| `max_range_m` | `3.0` | farthest ground read |
| `rate_hz` | `10.0` | most reports per second |
| `trust` | `0.9` | trust of a reading; `UNKNOWN` (no ground in view) is sent with trust 0 |

**Tested on rendered frames.** Until the Gazebo world exists (#13, #14), `mana_perception/synthetic.py`
renders depth frames of known ground by casting a ray for every pixel, and both the tests and
`python3 tools/evaluate_depth_ground.py` use it, with 1 % depth noise and 10 % of pixels missing.
Over 1,104 frames (distances 0.1 to 2.4 m, tilts 0.5 to 0.7 rad, step heights 0.10 to 0.35 m).
The right distance is within 15 cm, or, for an edge in the blind zone, no farther than the edge:

| Ground | Right type | Found as a step or drop at the right distance |
|---|---|---|
| Flat | 60 / 60 | no false steps |
| Ramp up or down, 8 % | 120 / 120 | no false steps |
| Curb, 0.10 to 0.19 m | 193 / 216 | 211 / 216 (most of the rest read as stairs up, also a step for the core) |
| Stairs up | 210 / 216 | 210 / 216 |
| Stairs down | 129 / 216 | 214 / 216 (most of the rest read as a drop) |
| Drop, 0.30 to 0.35 m | 215 / 216 | 215 / 216 |
| Slope of 30 % (17 degrees) | 52 / 60 | the rest read as a step far along the slope |

**No step or drop is ever missed, or put more than 15 cm past where it really is.** All 108 edges
in the blind zone are reported no farther than they are. The few misses above are edges a few
centimetres past the blind zone, reported at 0 m: closer than they are, which is safe.

Known limits:

- **Stairs down** from more than about 1.2 m away read as a **drop**. From 1 m up, each tread
  below is hidden behind the edge above it, so the camera sees only one lower level. A drop is the
  safe reading (the core slows down and stops 0.5 m before the edge), and as the chair comes
  closer the treads come into view and the reading becomes stairs down.
- **A slope steeper than about 25 %** can be read as a step. No wheelchair ramp is that steep
  (building codes allow up to 8.3 %).
- **Glass, black or shiny floors** give no depth reading; a large missing stretch is read as a
  drop, which stops the chair rather than letting it drive on.
- **A frame with no depth at all** reads `UNKNOWN`, sent with trust 0. That is what a covered lens
  gives, and also a ledge whose floor is beyond the sensor's own range (about 10 m for a D435), so
  the two cannot be told apart from one frame. The core then has no 3D camera report and acts on
  the other sensor alone.
- The camera tilt must be set right: an error of 2 degrees looks like a 3.5 % slope, under the
  5 % ramp limit. The mounting height must be right to about 5 cm, or level ground just past the
  blind zone looks like a step there.
- The tilt is a fixed parameter. On a ramp, or on tracks on the stairs, the chair's own pitch
  changes it; adding the IMU pitch from `motion` to it is a follow-up.
- **Stairs up that start in the blind zone** are told from a wall only by risers climbing behind
  the first one in view; a single tall rise right in front is left to the obstacle sensors.

## The stub

```bash
colcon build --packages-select mana_msgs mana_perception
source install/setup.bash
ros2 run mana_perception stub_node                              # plays every scenario in turn
ros2 run mana_perception stub_node --ros-args -p scenario:=stairs
```

### Topics

| Topic | Type | Meaning |
|---|---|---|
| `ground` | `mana_msgs/GroundReport` | what the camera and the 3D camera see of the ground ahead |
| `obstacle` | `mana_msgs/ObstacleReport` | the nearest obstacle, as seen by the lidar |

### Parameters

| Name | Default | Meaning |
|---|---|---|
| `scenario` | `cycle` | `level`, `ramp`, `stairs`, `curb`, `drop`, `obstacle`, `conflict`, or `cycle` to play them all |
| `hold_s` | `5.0` | seconds each scenario lasts in `cycle` |
| `rate_hz` | `10.0` | reports per second |
| `trust` | `0.9` | trust given to every report |

### Scenarios

| Scenario | What the sources report | What it tests in the decision core |
|---|---|---|
| `level` | both see flat ground | wheels at full speed |
| `ramp` | both see a ramp 1.5 m ahead | slow down |
| `stairs` | both see stairs 1.5 m ahead | change to tracks (two sources agree) |
| `curb` | both see a curb 1.0 m ahead | change to tracks |
| `drop` | only the 3D camera sees a drop 0.4 m ahead | stop before the edge |
| `obstacle` | the lidar sees a person 0.7 m ahead | stop |
| `conflict` | the camera sees stairs, the 3D camera sees flat ground | do **not** change to tracks |

## Tests

```bash
python -m pytest tests/test_depth_ground.py tests/test_perception_stub.py   # no ROS needed
colcon test --packages-select mana_perception    # the depth node end to end, in ROS 2
```
