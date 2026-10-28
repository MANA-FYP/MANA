# mana_perception

Perception for MANA (M1 Vision). It holds:

- **`detector_node`**: the nearest obstacle in the camera image (a person, a chair, a car...),
  from the same object detector as the phone demo.
- **`depth_node`**: the ground ahead from the 3D camera (flat, ramp, curb, stairs up, stairs down
  or a drop), the second ground source the decision core's two-sensor rule needs.
- **`stub_node`**: scripted ground and obstacle reports on the real message interface, so the
  decision core can be run and tested before the other models exist.

## Obstacles from the camera

```bash
pip install ultralytics      # not a rosdep key; the CPU build of PyTorch is enough to run it
ros2 run mana_perception detector_node --ros-args \
  -r camera/image:=/camera/image_raw -r camera/camera_info:=/camera/camera_info \
  -p weights:=yolo26n.pt -p imgsz:=416
```

The detector and its maths are in `mana_perception/detection.py`, which the phone demo also uses
(`demo/vision.py` imports it), so the phone and ROS 2 give the same obstacles from the same picture.
For each frame the node runs the detector, keeps the classes with a typical height
(`OBSTACLE_HEIGHT_M`), estimates each one's distance from its box height and the camera's focal
length, and its bearing from the camera's field of view, both from the camera info. A person whose
box is cut by the top or the bottom of the frame (legs below it, or too close to fit) shows only
part of their height, which would read too far: for that box the distance from a 0.4 m shoulder
width is used too, and the nearer is kept, so a close person keeps coming closer instead of staying
at about 1.6 m. 0.4 m errs closer for someone facing the camera, but someone side-on, crossing in
front of the chair, is only about 0.25 to 0.3 m deep and reads about 1.4 times too far: at 0.6 m
they read 0.86 m, so the camera alone stops only at about 0.55 m. The 3D camera and the lidar cover
that case in the full system. It publishes only the **nearest** obstacle, because the core keeps one report per
source. With nothing in view it publishes nothing, and the core lets the last report age out after
0.5 s. The trust is the detector's confidence times `camera_trust_factor` of the frame's mean
brightness, the same rule and the same code as the phone (#71), so the camera is trusted less in the
dark. The detector and the ground classifier both look through the camera, so the core counts them
as one sensor.

| Topic | Type | Meaning |
|---|---|---|
| `camera/image` (in) | `sensor_msgs/Image` | rgb8, bgr8, rgba8 or bgra8; a frame stamped 0 gets the time it arrived |
| `camera/camera_info` (in) | `sensor_msgs/CameraInfo` | focal length (distance) and field of view (bearing) |
| `obstacle` (out) | `mana_msgs/ObstacleReport` | the nearest obstacle, source `DETECTOR`; trust = the detector's confidence × the light factor, as on the phone |

| Parameter | Default | Meaning |
|---|---|---|
| `weights` | `yolo26n.pt` | `yolo26n.pt` (COCO, as at the mid-evaluation), `yoloe-26n-mana.pt` (YOLOE-26 told the 42 names in `demo/build_detector.py`, including stairs, doors, poles, buses and trucks), and later the fine-tuned YOLO26s from #49, with no code change |
| `imgsz` | `416` | size each frame is scaled to |
| `conf` | `0.3` | lowest confidence the detector keeps |
| `min_confidence` | `0.5` | lowest confidence that becomes an obstacle; the core ignores trust under 0.5 anyway |
| `rate_hz` | `10.0` | most frames per second; frames in between are skipped |

**Time per frame** (`python3 tools/time_detector.py --weights yolo26n.pt yoloe-26n-mana.pt
--imgsz 480 416`): on an AMD Ryzen 7 7730U laptop (8 cores, 16 GB, CPU only, PyTorch 2.14.1 and
Ultralytics 8.4.176 from `requirements-ml.txt`), 40 frames of the two Ultralytics sample pictures
after a warm-up, through the same `Detector.detect_image` call the node makes:

| Weights | Image size | Median | 90th percentile | Frames per second |
|---|---|---|---|---|
| `yolo26n.pt` (COCO) | 480 | 53 ms | 59 ms | 19 |
| `yolo26n.pt` (COCO) | 416 | 47 ms | 50 ms | 21 |
| `yoloe-26n-mana.pt` (40 names) | 480 | 73 ms | 87 ms | 14 |
| `yoloe-26n-mana.pt` (40 names) | 416 | 62 ms | 68 ms | 16 |

The times were measured with the 40-name YOLOE. Both keep up with the node's 10 frames per second on
this laptop. On the same street picture both find the same person at the same estimated distance
(3.5 m with a phone-like camera). YOLOE-26n finds only the names it was built with: the 40-name
version missed the bus there, so `bus` and `truck` were added (#72), and rebuilt with 42 names it
finds the bus at 6.1 m. Rebuild `yoloe-26n-mana.pt` with `python -m demo.build_detector` after the
names change.

## Ground from the 3D camera

```bash
ros2 run mana_perception depth_node --ros-args \
  -r depth/image:=/camera/depth/image_raw -r depth/camera_info:=/camera/depth/camera_info \
  -p camera_height_m:=1.0 -p camera_pitch_rad:=0.6
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
| Curb, 0.10 to 0.19 m | 185 / 216 | 211 / 216 (most of the rest read as stairs up, also a step for the core) |
| Stairs up | 209 / 216 | 210 / 216 |
| Stairs down | 128 / 216 | 213 / 216 (most of the rest read as a drop) |
| Drop, 0.30 to 0.35 m | 213 / 216 | 213 / 216 |
| Slope of 30 % (17 degrees) | 37 / 60 | the rest read as a step far along the slope |

**No step or drop is ever missed, or put more than 15 cm past where it really is.** All 108 edges
in the blind zone are reported no farther than they are. The few misses above are edges a few
centimetres past the blind zone, reported at 0 m: closer than they are, which is safe.

**Depth noise.** Each image row's depth is taken as its median across the path strip before it is
turned into points: an edge across the path runs along a row, so the median keeps it, and the noise
of single pixels falls by about the square root of the strip's width. Without it, noise of a few
centimetres per pixel made flat ground read as a curb right in front, because just inside the
blind zone only the pixels whose noise shortened them land, and those sit higher. With the
simulation's 3D camera and its `depth_noise` fault (5 cm per pixel, `src/mana_sim`), flat ground
stays flat, the ramp stays a ramp, and stairs, curbs and the drop are read at their place, to within
5 cm; the tests check this.

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
python -m pytest tests/test_detection.py tests/test_depth_ground.py tests/test_perception_stub.py
colcon test --packages-select mana_perception    # the detector and depth nodes end to end, in ROS 2
```
