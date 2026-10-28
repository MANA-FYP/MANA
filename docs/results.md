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
| Fault start and end | moved up to 0.5 m either way (marks tied to a fault keep 0.5 m clear of it) |
| Spoken commands | moved up to 0.3 s either way |

The stairs, the drops and the obstacles stay where the pass marks expect them. Two new full-system scenarios have the position and alert-link modules as well:
- **Building entry**: up a ramp on GPS, then inside, where GPS is lost but the lidar map still has the position.
- **Wi-Fi drop**: Wi-Fi fades and Bluetooth carries the alerts; then both are gone for 2 m.

Darkness and the spoken stop are the existing dark-corridor and spoken-stop scenarios.

"All marks met" counts the runs that met every pass mark of the scenario, with the 95% (Wilson) interval. A mishap is going over a drop, onto steps on wheels, or into an obstacle.

| Scenario | MANA: all marks met | Baseline: all marks met | MANA mishaps | Baseline mishaps | Marks met, MANA | Marks met, baseline |
|---|---|---|---|---|---|---|
| Level ground | 170/200 (79% to 89%) | 169/200 (79% to 89%) | 0/200 | 0/200 | 96% (sd 9%) | 92% (sd 18%) |
| Ramp | 200/200 (98% to 100%) | 118/200 (52% to 66%) | 0/200 | 0/200 | 100% (sd 0%) | 90% (sd 12%) |
| Stairs | 200/200 (98% to 100%) | 200/200 (98% to 100%) | 0/200 | 0/200 | 100% (sd 0%) | 100% (sd 0%) |
| Curb | 200/200 (98% to 100%) | 200/200 (98% to 100%) | 0/200 | 0/200 | 100% (sd 0%) | 100% (sd 0%) |
| Spoken stop | 200/200 (98% to 100%) | 200/200 (98% to 100%) | 0/200 | 0/200 | 100% (sd 0%) | 100% (sd 0%) |
| Drop ahead (edge case) | 198/200 (96% to 100%) | 198/200 (96% to 100%) | 0/200 | 0/200 | 100% (sd 3%) | 100% (sd 3%) |
| Manual mode at a drop (edge case) | 200/200 (98% to 100%) | 200/200 (98% to 100%) | 0/200 | 0/200 | 100% (sd 0%) | 100% (sd 0%) |
| Picture of stairs (edge case) | 186/200 (89% to 96%) | 0/200 (0% to 2%) | 0/200 | 0/200 | 98% (sd 6%) | 73% (sd 7%) |
| Dark corridor (failure case) | 199/200 (97% to 100%) | 199/200 (97% to 100%) | 0/200 | 0/200 | 100% (sd 2%) | 100% (sd 2%) |
| IMU lost on the stairs (failure case) | 200/200 (98% to 100%) | 200/200 (98% to 100%) | 0/200 | 0/200 | 100% (sd 0%) | 100% (sd 0%) |
| Building entry (full system) | 182/200 (86% to 94%) | 120/200 (53% to 67%) | 0/200 | 0/200 | 98% (sd 6%) | 90% (sd 13%) |
| Wi-Fi drop (full system) | 185/200 (88% to 95%) | 0/200 (0% to 2%) | 0/200 | 0/200 | 99% (sd 5%) | 65% (sd 5%) |

### What the table shows

- **Neither controller had a mishap in any of the 4,800 runs.** The scripted world's sensors see every hazard in time, and both controllers stop for drops and obstacles from one sensor. The difference between them is in the other marks.
- **Where the baseline falls short:**
  - **The picture of stairs** (0 of 200): one camera report is enough for it to ask for tracks.
  - **The ramp** (118 of 200) and **building entry** (120 of 200): a single false camera report of stairs makes it ask for tracks on the ramp. With false reports turned off it meets every ramp mark in 60 of 60 runs.
  - **The Wi-Fi drop** (0 of 200): the alert link is not part of its rules, so it drives on at full speed while no alert could be sent.
- **Where MANA misses marks, it is mostly by design.** On level ground (170 of 200), building entry and the Wi-Fi drop, every miss comes from the false camera report. One sensor saying stairs is not enough to change mode, but it is enough to slow down for a moment, as the design asks ("possible step ahead"). That slow-down fails the strict "full speed" marks, but it isn't a mishap. With false reports turned off, MANA meets every mark on level ground, building entry and the Wi-Fi drop in 60 of 60 runs each. On the picture of stairs, a lost camera report can leave one tick at full speed before the picture.
- **What the noise found, that the exact world could not:** a stop for a drop or an obstacle can be released for a tick by one noisy distance reading. In the drop runs (2 of 200) and the dark corridor (1 of 200), the chair had stopped 0.43 m from the edge and 0.69 m from the person. A reading above `edge_m` or `obstacle_stop_m` let it move for a tick, and the run ended on that tick. It never came close to the hazard, but a stop should not depend on one reading: **#97**.

### Limits

- The noise levels are assumptions (above). They change the numbers, so the table compares the two controllers under the same assumptions; it does not predict real-world rates.
- The position module switches at once here, where the real node takes up to 2 s (#59). The alert link is up or down, without the heartbeat counting of the link node (#62).
- The scripted world measures distances from the front of the device. The simulated sensors measure from where they are mounted, which matters on tracks: **#96**.
