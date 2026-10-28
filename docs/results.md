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

## Reaction time and stopping distance (#84)

How far the chair travels between a hazard appearing and the chair standing still, and whether the stop distances leave room for it.

### From a depth frame to the wheels at 0

`tools/measure_latency.py` runs the real `depth_node`, `core_node` and `host_node` (ROS 2 Jazzy in WSL, on the AMD Ryzen 7 7730U laptop, 8 cores). It sends them 30 depth frames a second. Each trial is level ground, then, at a random moment, a drop in the blind zone, which stops the chair from one sensor. The times are taken by the probe's own clock when each message arrives. Gazebo is not needed, because the host's wheel command is where a chair starts to brake.

```bash
python3 tools/measure_latency.py --trials 50   # in a built and sourced workspace
```

| Stage, 50 trials | Median | 95th percentile | Most |
|---|---|---|---|
| frame to DROP report (`depth_node`) | 45 ms | 105 ms | 119 ms |
| DROP report to stop request (`core_node`) | 16 ms | 77 ms | 103 ms |
| stop request to wheels at 0 (`host_node`) | 29 ms | 30 ms | 31 ms |
| **frame to wheels at 0** | **116 ms** | **150 ms** | **184 ms** |

Most of the time is waiting, not computing. `depth_node` reads at most 10 frames a second, so a frame can wait up to 100 ms for its turn. `core_node` ticks at 10 Hz, so a report can wait up to 100 ms. `host_node` ticks at 20 Hz, so up to 50 ms. The host's share is nearly fixed within one launch (29 to 31 ms here, 45 ms in a 5-trial launch before), because the two timers keep their phase. A later launch can give anything up to 50 ms.

Not in these numbers:
- the camera's own exposure and transfer before a frame is published;
- an obstacle seen by the detector. The detector takes 73 ms a frame (median, 87 ms at the 90th percentile, YOLOE at 480: `src/mana_perception/README.md`), instead of the time `depth_node` takes to read a frame. That path is not measured end to end;
- a computer other than this laptop (we have no other target yet).

### Where the chair stands still

`tools/stopping_distance.py` drives the chair at top speed (1.2 m/s). It slows to 0.36 m/s once the hazard is within 2 m and stops it at the stop distance. Each request reaches the wheels after the reaction time above, and the chair then brakes at a constant deceleration. A real chair's deceleration is not measured yet. The simulated chair allows 3 m/s², so the table runs from 0.5 to 3.

Metres left to the hazard at standstill, with a reaction of 0.2 s (the most measured, 184 ms, rounded up). In brackets is the speed when the hazard crossed the stop distance:

| Case | 0.5 m/s² | 1.0 m/s² | 1.5 m/s² | 2.0 m/s² | 3.0 m/s² |
|---|---|---|---|---|---|
| drop: seen at 2 m, stop at 0.5 m | 0.29 (0.42 m/s) | 0.36 (0.36 m/s) | 0.38 (0.36 m/s) | 0.40 (0.36 m/s) | 0.41 (0.36 m/s) |
| obstacle: seen at 2 m, stop at 0.8 m | 0.32 (0.69 m/s) | 0.66 (0.36 m/s) | 0.68 (0.36 m/s) | 0.70 (0.36 m/s) | 0.71 (0.36 m/s) |
| late: an obstacle appears 1.2 m ahead | **−0.48** (1.13 m/s) | 0.24 (1.06 m/s) | 0.48 (0.98 m/s) | 0.60 (0.89 m/s) | 0.69 (0.69 m/s) |

With 0.3 s, which allows for the detector or a slower computer, the late obstacle has 0.12 m left at 1.0 m/s², and the drop 0.20 m at 0.5 m/s².

What this shows:
- **A hazard seen 2 m ahead is stopped for at every deceleration in the table.** With 1.0 m/s² or more, the chair has slowed to 0.36 m/s before the stop distance, and it stands 0.33 m or more from a drop and 0.63 m or more from an obstacle, even with a 0.3 s reaction. At 0.5 m/s² it is still going faster when it crosses the stop distance and stands only 0.20 to 0.32 m away.
- **An obstacle that appears close is the case that decides.** A chair at top speed reaches a person who steps out 1.2 m ahead if it brakes at 0.5 m/s². At 1.0 m/s² it stops only 0.12 to 0.24 m short of them. From 1.5 m/s² it keeps 0.36 m or more.
- **For #86:** at 1.5 m/s² or more, the chair keeps 0.35 m or more from every hazard in the table, with either reaction time. Below that, either the top speed should come down, or the stop distance for obstacles should grow, and the tool shows what either change gives. The real chair's deceleration must be measured first. That number is the input this check is waiting for.

## Mutation testing (#83)

Whether the tests would catch a broken rule: mutmut changes the code one small step at a time and runs the tests on each change. The survivors, and why each one does not change behaviour, are in [mutation.md](mutation.md).

| Module | Mutants | Killed | Survived | Score |
|---|---|---|---|---|
| `mana_core/decision.py` | 347 | 293 | 54 | 84.4% |
| `mana_core/messages.py` | 169 | 154 | 15 | 91.1% |
| `mana_position/selector.py` | 34 | 33 | 1 | 97.1% |
| `mana_connectivity/link.py` | 69 | 61 | 8 | 88.4% |
