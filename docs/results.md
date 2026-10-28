# Results

## Full-system evaluation in the scripted world (#54)

Every scripted scenario, run 200 times for each controller with noisy inputs: MANA's decision core and the if-else baseline (#53). To make the table again:

```bash
python3 -m mana_core.repeat --runs 200 --seed 1
```

The scripted world itself is exact. For these runs every report is changed as below, and MANA and the baseline get the same seed for the same run. **The noise levels are assumptions, not measurements of our sensors.** They show how each controller copes when its inputs are not perfect; the Gazebo runs (#16) and the real recordings (#89) will give measured ones.

| Noise | Value |
|---|---|
| A report is lost | 5% of reports |
| Trust | ± a standard deviation of 0.05 |
| Distance (3D camera, lidar) | ± a standard deviation of 0.02 m plus 5% of the distance |
| Tilt (IMU) | ± a standard deviation of 0.01 rad |
| A false report: the camera alone says stairs ahead | 0.2% of ticks, about once every 50 s |
| Fault start and end | moved up to 0.5 m either way (marks tied to a fault keep clear of it, and of the tick it takes to react) |
| Spoken commands | moved up to 0.3 s either way |

The stairs, the drops and the obstacles stay where the pass marks expect them. Two new full-system scenarios have the position and alert-link modules as well:
- **Building entry**: up a ramp on GPS, then inside, where GPS is lost but the lidar map still has the position.
- **Wi-Fi drop**: Wi-Fi fades and Bluetooth carries the alerts; then both are gone for 2 m.

Darkness and the spoken stop are the existing dark-corridor and spoken-stop scenarios.

"All marks met" counts the runs that met every pass mark of the scenario, with the 95% (Wilson) interval. A mishap is going over a drop, onto steps on wheels, or into an obstacle.

| Scenario | MANA: all marks met | Baseline: all marks met | MANA mishaps | Baseline mishaps | Marks met, MANA | Marks met, baseline |
|---|---|---|---|---|---|---|
| Level ground | 170/200 (79.4% to 89.3%) | 169/200 (78.8% to 88.9%) | 0/200 | 0/200 | 96.2% (sd 8.9%) | 92.4% (sd 17.9%) |
| Ramp | 200/200 (98.1% to 100.0%) | 118/200 (52.1% to 65.6%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 89.8% (sd 12.3%) |
| Stairs | 200/200 (98.1% to 100.0%) | 200/200 (98.1% to 100.0%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 100.0% (sd 0.0%) |
| Curb | 200/200 (98.1% to 100.0%) | 200/200 (98.1% to 100.0%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 100.0% (sd 0.0%) |
| Spoken stop | 200/200 (98.1% to 100.0%) | 200/200 (98.1% to 100.0%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 100.0% (sd 0.0%) |
| Stairs down | 200/200 (98.1% to 100.0%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 80.0% (sd 0.0%) |
| Drop ahead (edge case) | 194/200 (93.6% to 98.6%) | 2/200 (0.3% to 3.6%) | 0/200 | 0/200 | 99.0% (sd 5.7%) | 67.0% (sd 3.3%) |
| Manual mode at a drop (edge case) | 200/200 (98.1% to 100.0%) | 32/200 (11.6% to 21.7%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 79.0% (sd 9.2%) |
| Picture of stairs (edge case) | 186/200 (88.6% to 95.8%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 98.2% (sd 6.4%) | 73.1% (sd 6.6%) |
| Dark corridor (failure case) | 175/200 (82.2% to 91.4%) | 3/200 (0.5% to 4.3%) | 0/200 | 0/200 | 95.8% (sd 11.0%) | 67.2% (sd 4.1%) |
| Stairs in the dark (failure case) | 200/200 (98.1% to 100.0%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 50.0% (sd 0.0%) |
| Curb in the dark (failure case) | 200/200 (98.1% to 100.0%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 75.0% (sd 0.0%) |
| IMU lost on the stairs (failure case) | 200/200 (98.1% to 100.0%) | 200/200 (98.1% to 100.0%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 100.0% (sd 0.0%) |
| Building entry (full system) | 182/200 (86.2% to 94.2%) | 120/200 (53.1% to 66.5%) | 0/200 | 0/200 | 98.2% (sd 5.7%) | 90.2% (sd 13.1%) |
| Wi-Fi drop (full system) | 186/200 (88.6% to 95.8%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 98.8% (sd 4.3%) | 64.8% (sd 5.5%) |

### What the table shows

- **Neither controller had a mishap in any of the 6,000 runs.** The scripted world's sensors see every hazard in time, and both controllers stop for drops and obstacles from one sensor. The difference between them is in the other marks.
- **A stop holds through a noisy reading (#97).** "Stops before" fails if the chair moves at all after its first stop for the hazard. Before #97, one noisy distance over `edge_m` or `obstacle_stop_m` released the stop for a tick, and it happened again and again while the chair waited. MANA met the stop mark in 2 of 200 drop runs, 32 of 200 manual-mode drop runs and 1 of 200 dark-corridor runs, and came within 0.20 m of an edge the rule keeps 0.5 m away. Now a stop holds for 0.5 s after its hazard was last seen, and while it holds, a hazard up to 0.1 m past its limit still counts (`docs/decisions.md`, D5). Once the chair has stopped within the limit, it no longer moves on in any of the 200 runs of each scenario. Where MANA still misses the mark (6 drop runs, 25 dark-corridor runs), a noisy reading stopped it early: 0.57 to 0.68 m from the edge, 0.85 to 0.98 m from the person. Once the hold ran out, the chair moved on to the real limit and stopped there. The baseline still releases its stops in nearly every run.
- **Stairs down: every mark in 200 of 200 runs.** Before #97 a yes was lost in 13 runs. It was heard on a tick where one lost camera report, or a reading just past `step_m`, had ended the question. The question now holds the same way, and a yes heard up to 2 s after it was last asked still answers it.
- **Where the baseline falls short:**
  - **The picture of stairs** (0 of 200): one camera report is enough for it to ask for tracks.
  - **The ramp** (118 of 200): a single false camera report of stairs makes it ask for tracks on the ramp. **Building entry** (120 of 200) fails for the same reason, on its entrance ramp, so it is the same weakness, not a second one. With false reports turned off, the baseline meets every mark on both in 200 of 200 runs.
  - **The Wi-Fi drop** (0 of 200): the alert link is not part of its rules, so it drives on at full speed while no alert could be sent.
  - **Stairs down** (0 of 200): its if-else rules have no question, so it never asks before going down.
  - **Stairs and a curb in the dark** (0 of 200 each): one sensor is enough for it to change to tracks, so it crosses on the 3D camera alone without stopping. It has no mishap there, but it is the same one-sensor rule that asks for tracks on a wall at the picture of stairs. MANA stops and waits for the user to choose tracks, in 200 of 200 runs of each.
- **Where MANA misses other marks, it is by design.** On level ground (170 of 200), building entry (182) and the Wi-Fi drop (186), every miss comes from the false camera report. One sensor saying stairs is not enough to change mode, but it is enough to slow down for a moment, as the design asks ("possible step ahead"). That slow-down fails the strict "full speed" marks, but it isn't a mishap. With false reports turned off, MANA meets every mark on all three in 200 of 200 runs. On the picture of stairs (186 of 200), a lost camera report can leave one tick at full speed before the picture.

To make the table without false reports:

```bash
python3 -m mana_core.repeat --runs 200 --seed 1 --no-false-reports
```

### Limits

- The noise levels are assumptions (above). They change the numbers, so the table compares the two controllers under the same assumptions; it does not predict real-world rates.
- Building entry does not test the position rule: the lidar map never fails there, so MANA's "position unknown" rule never acts, and the baseline, which has no position rule, passes that mark without one. A run where the lidar map is lost too would test it.
- There is one kind of false report: the camera alone saying stairs, which MANA's two-sensor rule is built to reject. There are no false 3D camera or lidar reports, and no missed detections near steps, where needing two sensors could cost MANA. The table is biased towards MANA in that way.
- The position module switches at once here, where the real node takes up to 2 s (#59). The alert link is up or down, without the heartbeat counting of the link node (#62).
- The scripted world measures distances from the front of the device. The simulated sensors measure from where they are mounted, which matters on tracks: **#96**.

## Mutation testing (#83)

Whether the tests would catch a broken rule: mutmut changes the code one small step at a time and runs the tests on each change. The survivors, and why each one does not change behaviour, are in [mutation.md](mutation.md).

| Module | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| `mana_core/decision.py` | 347 | 293 | 54 | 84.4% |
| `mana_core/messages.py` | 169 | 154 | 15 | 91.1% |
| `mana_position/selector.py` | 34 | 33 | 1 | 97.1% |
| `mana_connectivity/link.py` | 69 | 61 | 8 | 88.4% |

<!-- gazebo results: made by python3 -m mana_sim.gazebo_results, do not edit by hand -->
## Scenarios in Gazebo (#16)

The ten original scripted scenarios of `mana_core.scenarios`, played in Gazebo with the same pass
marks (the same functions and limits; only the distances in them are the ones of the Gazebo route).
Generated by `python3 -m mana_sim.gazebo_results gazebo_traces --update docs/results.md` from
the traces of `ros2 run mana_core evaluate --world gazebo`.
**30 of 39 pass marks are met in Gazebo.**

### How a scenario is played

- A fresh simulation for each scenario: the route's world with the chair at the start of the run and
  the objects of the scenario (a box for the person). The chair settles first; the core is started
  then, and the run begins with its first drive request.
- The real sensors and nodes: the 3D camera (`depth_node`), the lidar, the IMU, the position and
  link nodes, the core. **The camera's ground classifier (#38) is not trained yet**, so a stand-in
  (`scenario_runner`) says what the scripted camera says (it names the ground, no distance) and is
  trusted less as the camera image gets dark. The picture of stairs exists only in what the stand-in
  says.
- Faults are switched with the `set_fault` service (the camera is darkened for `dark`, the IMU stops
  for `imu_lost`); the user's commands are `UserCommand` messages at the times of the scenario.
- A mishap is the chair centre on a step on wheels (stairs 22 to 22.7 m, curb 31 to 31.3 m), over
  the drop (38 m, or the chassis lower than -0.3 m), or at a box. It is judged from the ground
  truth.

### Results

| Scenario | Pass mark | Scripted | Gazebo | Measured in Gazebo | Issue |
|---|---|---|---|---|---|
| `level` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `level` | Full speed on wheels | PASS | PASS | slowest 100% of top speed |  |
| `level` | Reaches the end of the route | PASS | PASS | reached 4.58 of 4 m |  |
| `level` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `ramp` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `ramp` | Slower on the ramp, on wheels | PASS | PASS | at most 30% of top speed on the ramp |  |
| `ramp` | Reaches the end of the route | PASS | PASS | reached 12.02 of 12 m |  |
| `ramp` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `stairs` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 2.27 m before the first step |  |
| `stairs` | Back on wheels once the ground is level | PASS | FAIL | still on tracks | #117 |
| `stairs` | Reaches the end of the route | PASS | FAIL | reached 3.82 of 7 m | #117 |
| `stairs` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `curb` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 2.31 m before the first step |  |
| `curb` | Back on wheels once the ground is level | PASS | FAIL | still on tracks | #117 |
| `curb` | Reaches the end of the route | PASS | FAIL | reached 3.87 of 6 m | #117 |
| `curb` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `stop` | Stop acted on within 1 s | PASS | PASS | stopped 0.20 s after the command |  |
| `stop` | Stays stopped until the user says go | PASS | PASS | held the stop |  |
| `stop` | Moving again within 1 s of go | PASS | PASS | moving 0.20 s after go |  |
| `stop` | Says "Stopped by you" | PASS | PASS | at 2.1 s |  |
| `stop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `drop` | Stops before the drop | PASS | FAIL | stopped, then moved on 0.05 m, to 0.73 m before the drop | #118 |
| `drop` | Says "Drop ahead, stopping" | PASS | PASS | at 7.6 s |  |
| `drop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `manual_drop` | Follows the user in manual mode | PASS | PASS | manual from 0.2 s |  |
| `manual_drop` | Stops before the drop | PASS | FAIL | stopped, then moved on 0.20 m, to 0.33 m before the drop | #118 |
| `manual_drop` | Says "Drop ahead, stopping" | PASS | PASS | at 2.6 s |  |
| `manual_drop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `picture` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `picture` | Slower on the way to the picture, on wheels | PASS | PASS | at most 30% of top speed on the way to the picture |  |
| `picture` | Reaches the end of the route | PASS | PASS | reached 9.00 of 9 m |  |
| `picture` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `dark` | Stops before the person | PASS | FAIL | stopped, then moved on 0.02 m, to 1.31 m before the person | #118 |
| `dark` | Says "Obstacle ahead, stopping" | PASS | PASS | at 7.8 s |  |
| `dark` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `imu_lost` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 2.30 m before the first step |  |
| `imu_lost` | Stays on tracks while the tilt cannot be checked | PASS | FAIL | went back to wheels | #117 |
| `imu_lost` | Shows "On tracks until the ground is level" | PASS | FAIL | not told | #117 |
| `imu_lost` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |

The two issues behind the failing marks:

- **#117:** the 3D camera reader reports a drop at 0 m while the chair climbs the stairs or the curb
  (pitched up to 0.47 rad), and the chair stops. This keeps `stairs`, `curb` and `imu_lost` from
  finishing the climb (6 marks).
- **#118:** the mark "Stops before the ..." counts the chair's braking distance (0.02 to 0.2 m) as a
  released stop (3 marks). The chair stops 0.3 to 1.3 m before the hazard.

### The routes

| Scenario | Chair starts at | Run ends at | Differs from the scripted route |
|---|---|---|---|
| `level` | -1.5 m | 3 m | level ground for 4.5 m: after that the ramp at 8 m is in view of the 3D camera |
| `ramp` | 2 m | 14 m | the ramp is 4 m long (8 to 12 m) and rises 0.5 m, then the plateau |
| `stairs` | 18.2 m | 25 m | 3 steps of 17 cm in 0.66 m; the run ends 1 m before the stairs down |
| `curb` | 27 m | 33.5 m | the 12 cm curb is the edge of a platform 3 m long, then a step down |
| `stop` | -1 m | 8 m | same marks, same limits |
| `drop` | 34.5 m | 38 m | the drop is at 38 m, 4 m after the curb; a drop is 1 m deep |
| `manual_drop` | 34.5 m | 38 m | in Gazebo nobody drives in manual mode: the speed stays at the last limit |
| `picture` | -1 m | 8 m | the picture is only in what the camera stand-in reports; nothing is in the world |
| `dark` | -1 m | 7 m | the person is a box 1.7 m high at 7 m; the camera is darkened by a fault |
| `imu_lost` | 18.2 m | 25 m | same marks, same limits |

The Gazebo route is one course (`src/mana_sim/README.md`), so each scenario is a stretch of it.
Before the ramp at 8 m the 3D camera sees the ramp from 4 m away, so `level` is 4.5 m long. The
stairs down (26 m) and the drop (38 m) are never driven over. A drop is 1 m deep.

### Run it

```bash
colcon build && source install/setup.bash
ros2 run mana_core evaluate --world gazebo               # all ten (about 10 minutes)
ros2 run mana_core evaluate --world gazebo stairs curb   # some of them
ros2 run mana_core evaluate --world gazebo --rerun       # play again, also those with a trace
python3 -m mana_sim.gazebo_results gazebo_traces --update docs/results.md
```
<!-- end of gazebo results -->
