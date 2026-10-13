# MANA

[![CI](https://github.com/MANA-FYP/MANA/actions/workflows/ci.yml/badge.svg)](https://github.com/MANA-FYP/MANA/actions/workflows/ci.yml)

**Multimodal Assistance for Navigation and Accessibility**

Final Year Project F26-049, Department of Computer Science, FAST NUCES Islamabad.

> A person looks and decides. MANA senses and decides.

MANA is an autonomy layer for assistive mobility devices. It is software that reads many sensors, checks how far each one can be trusted at that moment, and turns them into one safe command for the device and one clear message for the user.

**Status:** planning and early set-up (FYP-1). No results are claimed yet. Every accuracy figure in this document is the one published by the cited authors, not a result of this project.

---

## 1. The problem

A wheelchair user looks at the path and decides what to do: keep moving on wheels, change to tracks for stairs, slow down, or stop. Today even wheelchairs that can climb stairs, such as the XSTO X12, are controlled by joystick, remote or app, so the human still makes that decision.

Existing solutions only cover part of the problem:

| System | What it does | What it lacks |
|---|---|---|
| XSTO X12 | Climbs stairs on tracks; joystick, remote or app control | No automatic choice of wheels or tracks |
| LUCI | Stops a power chair at drop-offs and warns of tipping hazards | Cannot change how the chair moves |
| WHILL | Self-drives on pre-mapped airport routes | No general terrain decisions |
| Tomari et al. (2013) | Detects drops; manual or semi-autonomous; wheels only | No mode change |
| Smart canes and apps | Scene descriptions and audio warnings | Warn only; no control |

None of them chooses its way of moving from sensors, or manages position, network and user commands together. This is the gap MANA fills.

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
| `release.yml` | Pushing a tag such as `v0.1.0-mid` | Creates a GitHub Release for the tag with generated release notes |

Every change goes in through a pull request into `main` (see `CONTRIBUTING.md`), and the checks above must pass before it is merged.

## 10. Repository layout

```
.
├── .github/workflows/   CI and release pipelines
├── src/                 ROS 2 packages
├── tests/               Python tests
├── ruff.toml            lint settings
├── pytest.ini           test settings
├── README.md
├── CONTRIBUTING.md
└── LICENSE
```

The ROS 2 workspace and packages will be added to `src/` as the plan progresses.

## 11. References

- He and Saha, 2023. Comparison of YOLO versions for obstacle detection for visually impaired users (arXiv:2312.07571)
- Kurbis et al., 2024. StairNet
- Waghmare et al., 2023. SANPO dataset
- Kim et al., 2021. BC-ResNet
- Schmid et al., 2023. EfficientAT
- Tomari et al., 2013. Manual and semi-autonomous wheelchair with drop detection
- Ultralytics YOLO26 documentation, 2026
- Official pages of XSTO, LUCI and WHILL
