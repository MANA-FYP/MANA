# Hazard coverage

For every hazard a user can meet: which sensor sees it, which rule of the decision core acts on it, and which test shows it. **Covered** means a rule, a scripted scenario and unit tests; **partly** means some of these; **not covered** means no rule acts on it yet. "Gazebo" is the simulation test, which waits for the scenario runner (#16); the simulated sensors are in (#92, #94, #112).

Rows that an open pull request covers say so: they become **covered** when it merges. The rule names are the branches of the behaviour tree in `mana_core/decision.py`, highest priority first: user stop, drop, obstacle, lone step, stairs down, going down, manual, step, slip, hold tracks, no ground, slow, no alert link, no position.

## Ground and obstacles

| Hazard | Sensors | Rule | Scripted scenario | Unit tests | Gazebo | Status |
|---|---|---|---|---|---|---|
| Level ground | camera, 3D camera | wheels at full speed | `level` | `test_level_ground_drives_on_wheels_at_full_speed` | #16 | covered |
| Ramp | 3D camera (slope over about 3°), camera | slow, on wheels | `ramp` | `test_ramp_slows_down_on_wheels` | #16 | covered |
| Stairs up | camera and 3D camera, both | step: tracks at least 1.5 m before | `stairs` | `test_stairs_seen_by_both_cameras_change_to_tracks`, `test_one_camera_is_not_enough_to_change_mode` | #16 | covered |
| Stairs up, only one sensor (in the dark) | 3D camera | lone step: stop at `edge_m`; the user can choose tracks to cross | `dark_stairs` | `test_a_step_one_sensor_measures_at_the_edge_stops_the_chair` | #16 | covered (#103) |
| Curb | 3D camera (up to 0.2 m), camera | step: tracks | `curb` | `test_curb_seen_by_both_cameras_changes_to_tracks` | #16 | covered |
| Curb, only one sensor | 3D camera | as for stairs above | `dark_curb` | `test_a_step_one_sensor_measures_at_the_edge_stops_the_chair` | #16 | covered |
| Stairs down | camera and 3D camera | stairs down: stop and ask; going down after a yes | `stairs_down` | the stairs-down tests in `tests/test_decision.py` | #52 | covered |
| Stairs down, only one sensor | 3D camera (StairNet has no stairs down) | drop: stop at `edge_m` | none | `test_stairs_down_seen_by_one_sensor_stop_at_the_edge_like_a_drop` | #52 | partly: no scenario |
| Drop, platform edge | 3D camera (0.3 m of missing ground) | drop: stop at `edge_m`, one sensor is enough | `drop`, `manual_drop` | `test_drop_stops_at_the_edge_and_slows_before_it` | #16 | covered; the stop holds through a noisy reading (#97); distance from the sensor, not the front of the device: #96 |
| Pothole, small drop | 3D camera, if at least 0.3 m of ground is missing along the path | drop | none | `tests/test_depth_ground.py` (the depth reader) | none | partly: less missing ground than `hole_m` (0.3 m) is not a drop to the depth reader |
| Obstacle ahead (person, furniture) | lidar, detector, 3D camera | obstacle: stop at 0.8 m, slow from 2 m | `dark` | `test_obstacle_stops_close_and_slows_further_out` | #16 | covered; the stop holds through a noisy reading (#97) |
| Moving people, crowds | detector (nearest only), lidar | obstacle, on the nearest only | none: scripted obstacles stand still | none | none | not covered: no motion or prediction |
| Glass door | detector (`glass door` in YOLOE's names) | obstacle | none | none | none | partly: a detector class only. Real lidars can see through glass; Gazebo's cannot show that |
| Narrow doorway | lidar, by bearing | none | none | none | none | not covered: nothing checks the width ahead against the device |
| Obstacle at head height (table edge, branch) | detector (`table`); the lidar is one plane, about 0.6 m up in #92's model | obstacle, if the detector reports it | none | none | none | not covered: nothing compares an obstacle's height with the device or the user, which matters most for a cane user |
| Low obstacle, under the lidar plane | 3D camera (a rise over 0.25 m is "an object, not a step") | none | none | `tests/test_depth_ground.py` | none | not covered: the 3D camera sees it, but `depth_node` sends only ground reports, never an obstacle |
| Vehicle | detector (car, bus, truck, motorcycle) | obstacle | none | `tests/test_detection.py` | none | partly: as any obstacle |
| Siren or horn | microphone (#47) | none yet | none | none | none | not covered: #47 is open |
| Wet or slippery floor | wheel encoders against ground speed (slip) | slip: tracks | none | `test_wheel_slip_changes_to_tracks` | #15 | partly: no scenario. The simulation reads slip from the wheels against the ground speed, and in a turn against the gyro (#112) |
| Gravel, uneven ground | IMU (tilt) | hold tracks, only on a steady tilt | none | none | none | not covered: nothing reads roughness |
| Picture of stairs (a false step) | camera alone | two-sensor rule: only slow | `picture` | `test_one_camera_is_not_enough_to_change_mode` | none | covered |

## Sensors, position and the alert link

| Case | Sensors | Rule | Scripted scenario | Unit tests | Gazebo | Status |
|---|---|---|---|---|---|---|
| Darkness | camera trust × the light factor (#73) | untrusted reports are ignored | `dark` | `test_dark_corridor_untrusted_camera_is_ignored_but_depth_still_stops` | #15 | covered, also for steps in the dark |
| IMU lost on the stairs | IMU | hold tracks until the tilt is known | `imu_lost` | `test_stays_on_tracks_when_the_imu_is_lost` | #15 | covered |
| A stale or doubtful report | any | reports older than 0.5 s or under trust 0.5 are ignored | lost and doubtful reports in the noisy runs (`python3 -m mana_core.repeat`) | `test_stale_reports_are_ignored_and_the_core_slows_down`, `test_untrusted_obstacle_is_ignored` | none | covered |
| GPS lost indoors | GPS, lidar localisation (#43) | no position: slow only when neither GPS nor the lidar map has one | `entry` | `test_a_lost_position_slows_down`, `test_any_one_fresh_position_source_is_enough`; `mana_position` 26 tests | #42 | covered; `entry` does not lose the lidar map, so it does not test the rule itself (`docs/results.md`) |
| Wi-Fi lost | Wi-Fi and Bluetooth heartbeats | no alert link: slow when neither can carry an alert | `wifi_drop` | `test_no_alert_link_slows_down`, `test_bluetooth_carries_the_alerts_when_wifi_is_gone`; `mana_connectivity` 25 tests | #44 | covered |
| The user says stop | voice, touch, button | user stop, at any trust, until a trusted GO | `stop` | `test_spoken_stop_wins_over_everything_until_go`, `test_stop_is_obeyed_even_at_low_trust` | none | covered |
| Manual mode at a hazard | user commands | manual sits below drop, obstacle and stairs down | `manual_drop` | `test_manual_mode_follows_the_user_but_safety_still_stops` | none | covered |
| A part of MANA dies | node, topic, bridge | the host stops after 0.5 s without a drive request | none | `mana_sim` host tests | #85 | partly: only the host's watchdog is tested |

## Requirements

The functional requirements the repository and the issues name, and where each is tested:

| Requirement | What | Tested by |
|---|---|---|
| FR-10 | position and link reports in the core | `tests/test_decision.py` (position and link tests), #41 |
| FR-12 | object detection | `tests/test_detection.py`, `mana_perception` node tests, #40 |
| FR-13 | the fine-tuned detector | #49, open |
| FR-14 | ground from the camera and the 3D camera | `tests/test_depth_ground.py`, #38 open, #39 |
| FR-19 | GPS to the lidar map within 2 s | `mana_position` selector and node tests (sim time), #42 |
| FR-20 | Wi-Fi to Bluetooth with no alert lost | `mana_connectivity` tests, #44 |
| FR-21 | offline command words | #46, open |
| FR-22 | one message at a time, most urgent first | `tests/test_messages.py`, #48 |
| FR-23 | the caregiver's status view | `tests/test_caregiver.py`, #50 |
| FR-24 | the Gazebo world with the test host | `mana_sim` tests, #13 |
| FR-25 | stairs down: stop and ask | `tests/test_decision.py` stairs-down tests and the `stairs_down` scenario, #52 |
| FR-26 | the if-else baseline | `tests/test_baseline.py`, #53 |

FR-4 is named in #48 only, without its text; the report's own list should be checked for any requirement not named here.

## Gaps, and where each is tracked

- A stop released by one noisy reading: fixed (#97).
- Stop distances from the sensor, not the front of the device: **#96**.
- How far the chair travels before it stands still, against the stop distances: measured (#84, `docs/results.md`). A real chair's deceleration is still to be measured (#86).

### Limits of this version

These have no rule, and they are stated as limits in the report rather than tracked as issues for this version:
- **Moving people and crowds:** the core acts on the nearest obstacle as it is now, with no prediction of where it is going.
- **Narrow doorways:** nothing compares the width ahead with the device's width.
- **Obstacles at head height** (a table edge, a branch) **and low obstacles under the lidar plane:** nothing compares an obstacle's height with the device or the user. The 3D camera sees a low object but `depth_node` reports only the ground.
- **Gravel and uneven ground:** nothing reads how rough the ground is. Only a steady tilt is held on tracks.
- **Small potholes:** less missing ground than `hole_m` (0.3 m) is not a drop to the depth reader.
- **Sirens and horns:** no microphone input yet (#47).
