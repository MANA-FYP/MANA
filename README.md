# MANA

[![CI](https://github.com/MANA-FYP/MANA/actions/workflows/ci.yml/badge.svg)](https://github.com/MANA-FYP/MANA/actions/workflows/ci.yml)

**Multimodal Assistance for Navigation and Accessibility**

Final Year Project F26-049, Department of Computer Science, FAST NUCES Islamabad.

> A person looks and decides. MANA senses and decides.

MANA is an autonomy layer for assistive mobility devices. It is software that reads many sensors, checks how far each one can be trusted at that moment, and turns them into one safe command for the device and one clear message for the user.

**Status:** planning and early set-up (FYP-1). No results are claimed yet. Every accuracy figure in this document is the one published by the cited authors, not a result of this project.

---

## 1. The problem

A wheelchair user looks at the path and decides what to do: keep moving on wheels, change to tracks for stairs, slow down, or stop. Wheelchairs that climb stairs exist, and the XSTO X12 even switches between wheels and tracks by itself, but how it decides is not published and works only on that chair.

Existing solutions only cover part of the problem:

| System | What it does | What it lacks |
|---|---|---|
| XSTO X12 | Climbs stairs on tracks; switches between wheels and tracks by itself, using lidar and gyroscopes | Its rules are closed and work only on that chair |
| LUCI | Stops a power chair at drop-offs and warns of tipping hazards | Cannot change how the chair moves |
| WHILL | Self-drives on pre-mapped airport routes | No general terrain decisions |
| Tomari et al. (2013) | Detects drops; manual or semi-autonomous; wheels only | No mode change |
| Smart canes and apps | Scene descriptions and audio warnings | Warn only; no control |

Only the XSTO X12 chooses its way of moving from sensors, and its rules are closed. None of them manages position, network and user commands together, or publishes tests that others can run. This is the gap MANA fills: an open, tested decision layer for any device.

## 2. What MANA does

MANA makes four decisions continuously:

1. **How to move.** Wheels on level ground and ramps, tracks for stairs, curbs and rough or slippery ground, slow down or stop for obstacles and warning sounds.
2. **Where it is.** GPS outdoors, lidar where GPS fails, with hysteresis so it does not flip back and forth.
3. **How to stay connected.** Wi-Fi, or Bluetooth when Wi-Fi is weak.
4. **Who is in control.** The user (manual) or MANA (automatic). A spoken "stop" always wins.

A change of motion mode needs two independent sources to agree. A spoken stop needs none.

### Why it is hard

The sensors report at very different speeds, from once a second (GPS) to 100 times a second (IMU), and each one fails in its own way. The goals also conflict: change to tracks before the first step but never by mistake on flat ground, switch networks quickly but never back and forth, and follow the user but never into danger. No single fixed rule satisfies all of these at once, which is why a decision layer is needed.

## 3. How it works

```
Sensors  ->  MANA decision layer  ->  Outputs
```

| Inputs | Decision layer | Outputs |
|---|---|---|
| Camera and 3D camera | Align in time | Command to the chair: mode, speed, stop |
| Lidar | Weigh trust (confidence) | Voice and screen message to the user |
| IMU (tilt) | Choose one safe command | Step directions for a stick user |
| Wheel encoders | | Status for the caregiver (secondary) |
| GPS | | |
| Wi-Fi and Bluetooth signal | | |
| Microphone (voice, sirens, horns) | | |
| Touch screen and buttons | | |

### Sensor rates

| Sensor | Typical rate |
|---|---|
| IMU | 100 / s |
| Wheel encoders | 50 / s |
| Camera and 3D camera | 15 to 30 / s |
| Lidar | 10 / s |
| GPS | 1 / s |
| Wi-Fi / Bluetooth signal | 1 / s |

### Motion mode logic

| Situation | Evidence | Decision |
|---|---|---|
| Level ground | Ground model and IMU level; no drop ahead | Wheels |
| Ramp | 3D camera sees a smooth slope; tilt within limits | Wheels, slower |
| Curb or single step | 3D camera measures a height jump | Tracks |
| Stairs | Ground model and 3D camera both confirm | Tracks |
| Rough or slippery ground | IMU vibration or encoder wheel slip | Tracks |
| Obstacle in path | Object detector | Slow down or stop |
| Siren or car horn | Audio model | Warn; slow near road |
| User says "stop" | Voice model or Stop button | Stop, always |

## 4. Models

| Job | Model | Training data | Published figure |
|---|---|---|---|
| Obstacles and stairs | YOLO26s (fine-tuned) | SANPO, COCO | 48.6 mAP on COCO |
| Ground type | MobileNetV2 | StairNet | 98.4% on StairNet test set |
| Voice commands | BC-ResNet-8 | Speech Commands v2 | 98.7% on Speech Commands |
| Sirens and horns | EfficientAT-mn10 | AudioSet, UrbanSound8K | 47.1 mAP on AudioSet |

Obstacle detection is compared against a YOLOv8s baseline trained the same way. Distance and step height come from the 3D camera. Position uses an extended Kalman filter (`robot_localization`) over IMU, wheel encoders and GPS, with lidar localisation indoors. The decision core is a behaviour tree, compared against simple if-else rules on the same fault runs.

## 5. Scope

| Included | Not included |
|---|---|
| Autonomy software: motion mode, position source, network link and user messages | Building the wheelchair or any hardware |
| Models for obstacles, ground type, distance, voice commands and warning sounds | Motor control and the climbing mechanics (done by the chair's own certified controller) |
| Tablet app with voice, touch and buttons; manual and automatic modes | Route planning across a city |
| Caregiver status view (secondary) | Medical claims and certification of a real wheelchair |
| Gazebo simulation of the device with faults and measured tests | |

The wheelchair and its sensors are simulated in ROS 2 and Gazebo. Commands are tested in simulation only. English is the primary voice language.

## 6. Tech stack

- ROS 2 and Gazebo (simulation and integration)
- Python (decision core, perception nodes)
- PyTorch, Ultralytics YOLO and ONNX (models)
- Tablet app with voice, touch and buttons

### Set up a development machine

On Ubuntu 24.04, native or in WSL 2, from the repository root:

```bash
bash tools/setup_ubuntu.sh        # ROS 2 Jazzy, Gazebo Harmonic, colcon, rosdep
bash tools/setup_ubuntu.sh --ml   # the same, plus ~/.venvs/mana-ml for the model work
bash tools/check_env.sh           # one PASS or FAIL line per item
```

The ML packages are pinned in `requirements-ml.txt`. The setup installs the CPU build of PyTorch; train on a GPU machine with the matching CUDA build. The `Setup check` workflow runs both scripts on a fresh Ubuntu 24.04 runner whenever they change, so the setup is tested on a clean machine.

### Get the datasets

```bash
tools/fetch_datasets.sh           # StairNet, SANPO, COCO val2017, Speech Commands v2, UrbanSound8K
tools/fetch_datasets.sh --list    # sizes, and which one needs a login
```

The data goes to `datasets/`, which git ignores. Sources, licences and splits are in [`docs/datasets.md`](docs/datasets.md), and the licences are also listed in [`NOTICE`](NOTICE).

## 7. Team

| Member | Roll no. | Main responsibility |
|---|---|---|
| Mushahid Hussain | i230541 | Decision core (behaviour tree); motion-mode, position and link switching; baseline comparison |
| Muhammad Waqas | i230569 | Perception models; datasets and training; evaluation and testing |
| Komail Raza | i230717 | Tablet app, voice and sound models; Gazebo simulation; ROS 2 integration |

Shared: architecture, integration, documentation and the defence presentation.

**Supervisors:** Dr. Muhammad Rizwan and Dr. Suleman Khan

## 8. Plan

The FYP-1 plan runs from 6 October 2026 and covers 53 tasks in 9 work packages: setup, CI/CD, simulation, decision core, perception, app and voice, integration, documentation, and reviews.

## 9. CI/CD

GitHub Actions runs the pipeline.

| Workflow | When | What it does |
|---|---|---|
| `ci.yml` (lint) | Every pull request and push to `main` | Runs `ruff` for code style and formatting |
| `ci.yml` (build-test) | Every pull request and push to `main` | In a `ros:jazzy-ros-base` container: builds the ROS 2 packages in `src/` with `colcon`, runs `colcon test` and the Python tests in `tests/`, and uploads a coverage report |
| `ci.yml` (image) | Every pull request and push to `main` | Builds the Docker image from `Dockerfile` and checks that ROS 2 runs inside it |
| `release.yml` | Pushing a tag such as `v0.1.0-mid` | Runs every `ci.yml` check first. If they pass, it publishes the Docker image `ghcr.io/mana-fyp/mana:<tag>` to GitHub Packages and creates a GitHub Release with generated release notes, marked as a pre-release when the tag has a suffix such as `-mid` |

Every change goes in through a pull request into `main` (see `CONTRIBUTING.md`). Lint and build-test must pass, and another member must approve, before it is merged.

Once a version is released, anyone can run it without installing ROS 2:

```bash
docker run --rm -it ghcr.io/mana-fyp/mana:v0.1.0-mid ros2 run mana_core core_node
```

## 10. Repository layout

```
.
├── .github/workflows/   CI and release pipelines
├── src/                 ROS 2 packages (colcon workspace)
│   ├── mana_core/       decision core (M6)
│   └── mana_msgs/       message types between modules, decision core and host
├── demo/                phone demo: laptop server, phone page, dashboard
├── tests/               Python tests
├── tools/               set up a development machine, fetch the datasets
├── requirements-ml.txt  pinned packages for the model work
├── ruff.toml            lint settings
├── pytest.ini           test settings
├── docs/                datasets (datasets.md) and system diagrams
├── Dockerfile           release image: the ROS 2 workspace, built
├── README.md
├── CONTRIBUTING.md
├── NOTICE               dataset licences
└── LICENSE
```

### ROS 2 workspace

The repository root is a colcon workspace for ROS 2 Jazzy:

```bash
source /opt/ros/jazzy/setup.bash
colcon build
source install/setup.bash
```

Each module is its own package, added by its owner in their own pull request:

| Package | Part | Owner |
|---|---|---|
| `mana_msgs` | Message types (shared) | All |
| `mana_perception` | M1 Vision (perception) | Waqas |
| `mana_position` | M2 Position and motion | Mushahid |
| `mana_connectivity` | M3 Connectivity | Mushahid |
| `mana_voice` | M4 Voice and audio | Komail |
| `mana_ui` | M5 User interface | Komail |
| `mana_core` | M6 Decision core | Mushahid |
| `mana_sim` | Gazebo world, robot and scenarios | Komail |

### Messages (`mana_msgs`)

| Message | From → to | Carries |
|---|---|---|
| `GroundReport` | M1 → core | Ground ahead (flat, ramp, stairs, curb, drop), its distance and step height |
| `ObstacleReport` | M1 → core | Nearest obstacle: distance, bearing and label |
| `MotionReport` | M2 → core | Tilt and wheel slip |
| `PositionReport` | M2 → core | One position source (GPS, lidar map, odometry) and its error |
| `LinkReport` | M3 → core | One link (Wi-Fi or Bluetooth): signal and missed heartbeats |
| `UserCommand` | M4, M5 → core | One voice, touch or button command |
| `DriveRequest` | core → host | Wheels or tracks, speed limit, stop |
| `UserMessage` | core → M4, M5 | Text for the user, its priority, and whether to speak it |

Every report to the core starts with the same two fields: `header` (its stamp is when the value was measured) and `trust` (0 to 1). The core uses them to line reports up in time and weigh them. `tests/test_msgs_contract.py` checks this rule on every pull request.

### Decision core (`mana_core`)

```bash
ros2 run mana_core core_node
```

The node listens on `ground`, `obstacle`, `motion`, `position`, `link` and `user_command`. It decides 10 times a second, publishes `drive_request` every time, and publishes `user_message` when the reason changes. The decision is a behaviour tree (py_trees) that acts on the first rule that holds:

1. The user said stop: stop until they say go
2. A drop within 0.5 m or an obstacle within 0.8 m: stop
3. Stairs down seen by two sensors: stop and ask; after the user says yes, go down on tracks, slowly, whatever the mode (a no or a stop at any trust, or a new staircase, asks again)
4. Manual mode: follow the user's mode and speed
5. Stairs up or a curb seen by both cameras, or wheel slip: tracks, slow
6. On tracks and not yet level for 2 s: stay on tracks
7. No trusted ground report: slow down
8. An obstacle, drop, ramp or possible step ahead: slow down
9. No link could carry an alert, or neither GPS nor the lidar map has a recent, trusted position: slow down. Only when the host runs a link or position module; these reports come about once a second, so they count as recent for 2.5 s, and odometry alone does not count because it drifts. In manual mode the user drives and the warning waits.
10. Otherwise: wheels, full speed

The distances and times are defaults, set as ROS parameters, to be tuned in simulation. `tests/test_decision.py` tests every rule, with edge and failure cases; `src/mana_core/test/test_core_node.py` tests the node through ROS topics.

### Position source (`mana_position`)

```bash
ros2 run mana_position position_node
```

The node listens on `gps/fix` (NavSatFix), `lidar/odometry` (the lidar localisation) and `odometry/filtered` (the fused odometry from robot_localization). Ten times a second it chooses one source and publishes a `PositionReport` for it on `position`:

- GPS is left once its error has been worse than 15 m, or there has been no fix, for 1.5 s, counted from the last good fix even when the GPS simply falls silent, so walking into a building switches within the 2 s target and one bad reading does not switch at all;
- GPS is chosen again only after its error has been better than 8 m for 5 s, so one outage gives one switch away and one back;
- away from GPS the lidar map is used while the lidar localisation reports, and odometry, which drifts, only while it does not.

GPS positions are metres east and north of the first fix, in their own frame (`gps_origin`), until robot_localization aligns them with the map (#43). The levels and times are ROS parameters. `src/mana_position/test` tests the rule and sends a lost GPS fix through ROS.

The whole position stack starts with one launch file: robot_localization's two EKFs (wheels and IMU in `odom`, plus GPS in `map`), navsat_transform, and the position node. The topic names are launch arguments:

```bash
ros2 launch mana_position localisation.launch.py wheel_odometry:=/chair/odom imu:=/chair/imu gps:=/chair/gps
```

The lidar map source is slam_toolbox's localisation mode on a saved map of the building. Its pose goes to `lidar/pose`. Why slam_toolbox and not FAST-LIO2 or KISS-ICP is in [`docs/decisions.md`](docs/decisions.md).

### Scenarios, pass marks and the live demo

Until the Gazebo world is ready, `mana_core/world.py` scripts a simple world: a chair on a straight route, and the reports its sensors would send. The camera names the ground but cannot measure distance, the 3D camera measures distance and step height, the lidar sees obstacles, and the IMU gives the tilt. Faults lower a sensor's trust or cut its reports. `mana_core/scenarios.py` defines the scenarios and their pass marks:

| Scenario | What it shows |
|---|---|
| `level` | Wheels at full speed on level ground |
| `ramp` | A ramp is driven on wheels, more slowly, never on tracks |
| `stairs` | Both cameras confirm the stairs: tracks before the first step, wheels again once level at the top |
| `curb` | A 12 cm curb crossed on tracks |
| `stop` | A spoken stop at full speed, held until the user says go |
| `drop` | Edge case: slows down for a drop, then stops 0.5 m before it on the 3D camera's distance |
| `manual_drop` | Edge case: in manual mode at full speed, safety still stops the chair before a drop |
| `picture` | Edge case: only the camera sees stairs (a picture), so MANA slows down but does not change mode |
| `dark` | Failure case: the camera is not trusted in the dark; the lidar still sees a person, and MANA stops and says why |
| `imu_lost` | Failure case: the IMU stops during the climb, so MANA cannot confirm level ground and stays on tracks |

The pass marks are the team's targets: no track request on flat ground or ramps, tracks requested at least 1.5 m before the first step, a stop acted on within 1 s, and obstacles still announced in the dark. No scenario may end with the chair on a step on wheels, over a drop or against an obstacle.

Check every scenario in fast time (CI runs the same as `tests/test_scenarios.py`):

```bash
ros2 run mana_core evaluate              # or one or more by name: ros2 run mana_core evaluate stairs dark
```

Watch one live, with the real core node on ROS 2 topics. The scenario node plays the route in real time, drives the chair as the core asks, prints each decision and each message for the user, and ends with the pass marks:

```bash
ros2 launch mana_core demo.launch.py scenario:=stairs
```

### Phone demo

The decision layer driven by real data from a phone, shown on a laptop dashboard, with no ROS 2
and no Gazebo. The phone page lists its sensors as eight small tiles, and a sensor the browser
cannot read is shown as **N/A**; nothing is simulated. Both pages fit one screen, and the phone
is connected with a USB cable. Stairs, ramps and drops that a phone
cannot sense are verified by scripted scenarios and labelled as tests. See
[`demo/README.md`](demo/README.md) for how to run it and what is real.

```bash
pip install -r demo/requirements.txt
python -m demo.server
```

## 11. References

- He and Saha, 2023. Comparison of YOLO versions for obstacle detection for visually impaired users (arXiv:2312.07571)
- Kurbis et al., 2024. StairNet
- Waghmare et al., 2023. SANPO dataset
- Kim et al., 2021. BC-ResNet
- Schmid et al., 2023. EfficientAT
- Tomari et al., 2013. Manual and semi-autonomous wheelchair with drop detection
- Ultralytics YOLO26 documentation, 2026
- Official pages of XSTO, LUCI and WHILL
