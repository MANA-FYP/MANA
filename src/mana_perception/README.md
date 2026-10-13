# mana_perception

Perception for MANA (M1 Vision). For now it holds a **stub** that publishes scripted ground and
obstacle reports on the real message interface, so the decision core can be run and tested before
the vision models exist. The real models will publish the same messages.

## Run the stub

```bash
colcon build --packages-select mana_msgs mana_perception
source install/setup.bash
ros2 run mana_perception stub_node                              # plays every scenario in turn
ros2 run mana_perception stub_node --ros-args -p scenario:=stairs
```

## Topics

| Topic | Type | Meaning |
|---|---|---|
| `ground` | `mana_msgs/GroundReport` | what the camera and the 3D camera see of the ground ahead |
| `obstacle` | `mana_msgs/ObstacleReport` | the nearest obstacle, as seen by the lidar |

## Parameters

| Name | Default | Meaning |
|---|---|---|
| `scenario` | `cycle` | `level`, `ramp`, `stairs`, `curb`, `drop`, `obstacle`, `conflict`, or `cycle` to play them all |
| `hold_s` | `5.0` | seconds each scenario lasts in `cycle` |
| `rate_hz` | `10.0` | reports per second |
| `trust` | `0.9` | trust given to every report |

## Scenarios

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
python -m pytest tests/test_perception_stub.py
```
