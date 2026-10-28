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
| Drop ahead (edge case) | 2/200 (0.3% to 3.6%) | 2/200 (0.3% to 3.6%) | 0/200 | 0/200 | 67.0% (sd 3.3%) | 67.0% (sd 3.3%) |
| Manual mode at a drop (edge case) | 32/200 (11.6% to 21.7%) | 32/200 (11.6% to 21.7%) | 0/200 | 0/200 | 79.0% (sd 9.2%) | 79.0% (sd 9.2%) |
| Picture of stairs (edge case) | 186/200 (88.6% to 95.8%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 98.2% (sd 6.4%) | 73.1% (sd 6.6%) |
| Dark corridor (failure case) | 1/200 (0.1% to 2.8%) | 3/200 (0.5% to 4.3%) | 0/200 | 0/200 | 66.8% (sd 2.4%) | 67.2% (sd 4.1%) |
| IMU lost on the stairs (failure case) | 200/200 (98.1% to 100.0%) | 200/200 (98.1% to 100.0%) | 0/200 | 0/200 | 100.0% (sd 0.0%) | 100.0% (sd 0.0%) |
| Building entry (full system) | 182/200 (86.2% to 94.2%) | 120/200 (53.1% to 66.5%) | 0/200 | 0/200 | 98.2% (sd 5.7%) | 90.2% (sd 13.1%) |
| Wi-Fi drop (full system) | 186/200 (88.6% to 95.8%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 98.8% (sd 4.3%) | 64.8% (sd 5.5%) |

### What the table shows

- **Neither controller had a mishap in any of the 4,800 runs.** The scripted world's sensors see every hazard in time, and both controllers stop for drops and obstacles from one sensor. The difference between them is in the other marks.
- **A stop is released for a moment in nearly every noisy run, by both controllers alike: #97.** "Stops before" fails if the chair moves at all after its first stop for the hazard. After that first stop, one noisy distance over `edge_m` or `obstacle_stop_m` lets the chair move for a tick, and it happens again later in the run:

  | Scenario | Runs where the stop was released (MANA) | Moved on after the first stop (median, most) | Closest to the hazard |
  |---|---|---|---|
  | Drop ahead | 198/200 | 0.11 m, 0.32 m | 0.34 m from the edge |
  | Manual mode at a drop | 168/200 | 0.12 m, 0.24 m | 0.20 m from the edge |
  | Dark corridor | 199/200 | 0.14 m, 0.32 m | 0.58 m from the person |

  The baseline's numbers are the same within a few runs, because both stop on the same one reading. None of these runs reached the hazard, but the manual-mode runs came within 0.20 m of an edge that the rule keeps 0.5 m away. A stop should not depend on one reading.
- **Where the baseline falls short:**
  - **The picture of stairs** (0 of 200): one camera report is enough for it to ask for tracks.
  - **The ramp** (118 of 200): a single false camera report of stairs makes it ask for tracks on the ramp. **Building entry** (120 of 200) fails for the same reason, on its entrance ramp, so it is the same weakness, not a second one. With false reports turned off, the baseline meets every mark on both in 200 of 200 runs.
  - **The Wi-Fi drop** (0 of 200): the alert link is not part of its rules, so it drives on at full speed while no alert could be sent.
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
