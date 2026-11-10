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

Darkness and the spoken stop are the existing dark-corridor and spoken-stop scenarios. Two edge cases show what MANA's trust and age checks add, which the other scenarios do not, since their sensors always report the right ground:
- **Glare**: the 3D camera alone sees a drop on level ground, at a trust of 0.3.
- **A person steps aside**: MANA stops for a person, who then steps aside, so their last report gets old.

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
| Glare: a doubtful drop (edge case) | 174/200 (81.6% to 91.0%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 96.8% (sd 8.4%) | 47.0% (sd 8.1%) |
| A person steps aside (edge case) | 187/200 (89.2% to 96.2%) | 0/200 (0.0% to 1.9%) | 0/200 | 0/200 | 98.4% (sd 6.2%) | 50.0% (sd 0.0%) |

### What the table shows

- **Neither controller had a mishap in any of the 6,800 runs.** The scripted world's sensors see every hazard in time, and both controllers stop for drops and obstacles from one sensor. The difference between them is in the other marks.
- **A stop holds through a noisy reading (#97).** "Stops before" fails if the chair moves at all after its first stop for the hazard. Before #97, one noisy distance over `edge_m` or `obstacle_stop_m` released the stop for a tick, and it happened again and again while the chair waited. MANA met the stop mark in 2 of 200 drop runs, 32 of 200 manual-mode drop runs and 1 of 200 dark-corridor runs, and came within 0.20 m of an edge the rule keeps 0.5 m away. Now a stop holds for 0.5 s after its hazard was last seen, and while it holds, a hazard up to 0.1 m past its limit still counts (`docs/decisions.md`, D5). Once the chair has stopped within the limit, it no longer moves on in any of the 200 runs of each scenario. Where MANA still misses the mark (6 drop runs, 25 dark-corridor runs), a noisy reading stopped it early: 0.57 to 0.68 m from the edge, 0.85 to 0.98 m from the person. Once the hold ran out, the chair moved on to the real limit and stopped there. The baseline still releases its stops in nearly every run.
- **Stairs down: every mark in 200 of 200 runs.** Before #97 a yes was lost in 13 runs. It was heard on a tick where one lost camera report, or a reading just past `step_m`, had ended the question. The question now holds the same way, and a yes heard up to 2 s after it was last asked still answers it.
- **Where the baseline falls short:**
  - **The picture of stairs** (0 of 200): one camera report is enough for it to ask for tracks.
  - **The ramp** (118 of 200): a single false camera report of stairs makes it ask for tracks on the ramp. **Building entry** (120 of 200) fails for the same reason, on its entrance ramp, so it is the same weakness, not a second one. With false reports turned off, the baseline meets every mark on both in 200 of 200 runs.
  - **The Wi-Fi drop** (0 of 200): the alert link is not part of its rules, so it drives on at full speed while no alert could be sent.
  - **Stairs down** (0 of 200): its if-else rules have no question, so it never asks before going down.
  - **Glare and a person who steps aside** (0 of 200 each): every report counts for it, however doubtful or old. It stops for the drop at trust 0.3 and for the person's last report, and waits there until the time runs out. MANA drives on in both.
  - **Stairs and a curb in the dark** (0 of 200 each): one sensor is enough for it to change to tracks, so it crosses on the 3D camera alone without stopping. It has no mishap there, but it is the same one-sensor rule that asks for tracks on a wall at the picture of stairs. MANA stops and waits for the user to choose tracks, in 200 of 200 runs of each.
- **Where MANA misses other marks, it is by design.** On level ground (170 of 200), building entry (182), the Wi-Fi drop (186), glare (174) and the person who steps aside (187), every miss comes from the false camera report. One sensor saying stairs is not enough to change mode, but it is enough to slow down for a moment, as the design asks ("possible step ahead"). That slow-down fails the strict "full speed" marks, but it isn't a mishap. With false reports turned off, MANA meets every mark on all five in 200 of 200 runs. On the picture of stairs (186 of 200), a lost camera report can leave one tick at full speed before the picture.

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

## Fail-safe: one of MANA's own parts dies mid-drive (#85)

What MANA should do when one of its own nodes dies or hangs, then what it did. The node cases are in `src/mana_sim/test/test_failsafe.py`. It runs the real nodes as processes (`ros2 run`), on a ROS domain of their own. A probe feeds them what the sensors would, then kills one node (SIGKILL) or freezes it (SIGSTOP). The times are from the kill to the first message that shows the safe behaviour. They come from 3 or 4 runs of the file in WSL. The test checks each against the limit it gives, plus 0.3 s for the timers and a loaded machine.

| Case | Expected | Test | Measured |
|---|---|---|---|
| The core node is killed, or hangs | The host's watchdog stops the chair `watchdog_s` (0.5 s) after the last drive request | `test_when_the_core_dies_or_hangs_...` | the wheels' command is 0 after 0.50 s, both ways, in 4 runs, and stays 0 |
| A perception node dies or a camera topic stalls | Its reports age out after `max_age_s` (0.5 s). With no ground sensor left, MANA slows down: "Ground sensors not available, slowing down", speed 0.3 | `test_when_the_ground_reports_stop_...` | slowed after 0.50 s in 4 runs |
| The position node dies | Its reports age out after `position_max_age_s` (2.5 s): "Position unknown, slowing down" | `test_when_the_position_or_link_node_dies_...` | slowed after 2.54 and 2.59 s, and once after 1.43 s |
| The link node dies | Its reports age out after `link_max_age_s` (2.5 s): "No alert link, slowing down" | same | slowed after 2.50 to 2.58 s, and once after 1.35 s |
| The bridge to Gazebo dies | The host cannot stop the chair: the drive plugins keep the last command they got. So the launch file starts the bridge again (`respawn`, after 0.5 s) | by hand, in Gazebo without cameras; the respawn also in `tools/sim_faults.py` (sim-smoke in CI) | **without respawn** the chair drove on at 0.36 m/s for at least 10 s of simulation time. **With respawn** a new bridge was up within 3 s, and a stop said after the kill stopped the chair. In `sim_faults.py` the clock came back 2.9 to 3.9 s after the kill |
| The user's commands stop coming, in manual mode | Commands are single events, not a stream. MANA keeps manual mode, and its stops still apply. A driver that goes quiet is stood still by the host (D7) | `test_when_following_a_driver_the_chair_stands_still_...` (host) | as expected |
| All ground sensors lost in manual mode | MANA cannot see a drop, so it slows down as in automatic mode, on the user's undercarriage (D7) | `test_in_manual_mode_with_no_ground_sensor_...` | before #85 it kept the user's speed |
| The clock jumps back (a world reset) | The core drops what it saw before (#109, #111). The host stands still on a request received "in the future" | `test_after_the_clock_goes_back_...` (core), `test_a_clock_gone_back_...` (host) | before #85 the host kept driving on the last request until the clock caught up |
| The clock stops | On simulation time, every node's timer stops with it. If Gazebo is paused, the chair stands still. If the bridge died, see that row | `tools/sim_faults.py` (sim-smoke): Gazebo paused for 5 s of wall time, then run again | while paused, Gazebo still sent the clock but its time stood still: 0.00 s of simulation time and no new decisions, in 2 runs. When it ran again, the core decided again ("Level ground") |

Two slow-downs came early, after 1.43 s and 1.35 s. The cause is WSL's system clock, which is stepped while the tests run. The probe's clock is monotonic. The nodes stamp their reports with the system clock, and the core ages the reports by it. In a later run, two position reports came 5.2 s apart by the probe's clock, but their stamps were 8.1 s apart. In another, a link report came 0.09 s before the kill, stamped 3 s before it. A step forward makes every report look older at once, so the core slows down early: the safe side. A step back is the clock going back (#109). A real device's clock can be stepped the same way, by NTP. On a real device, the motor controller needs its own command timeout, just as the drive plugin should have had one here.

## Faults switched in Gazebo (#42, #44)

`tools/sim_smoke.sh --faults` starts the world headless with no cameras and no lidar, as in CI, and starts the core. Then `tools/sim_faults.py` switches the simulation's faults (`set_fault`) one after the other, and checks what MANA does. The world has no cameras, so the probe sends flat ground from both cameras: otherwise the core slows down for the missing ground first and never gets to the position or the link. The chair follows only a driver (`driver:=true`), and none drives, so it stands still while the core decides. The times are in simulation seconds, from 5 runs in WSL. The same check runs in the sim-smoke job in CI.

| Fault | Expected | Measured |
|---|---|---|
| GPS lost (`gps dropout`) | The position node stops reporting: there is no lidar map in this world yet (#43). The core says "Position unknown, slowing down" once the last report is older than 2.5 s | after 4.0 to 4.1 s: the fix ages out after 1.5 s, then the report after 2.5 s |
| GPS back | The selector takes GPS back after 5 s of good fixes (`back_s`), and the core says "Level ground" again | after 5.3 to 5.4 s |
| Wi-Fi fade over 10 s (`wifi fade`) | The link node gives Wi-Fi up, Bluetooth still carries the alerts, and the core never slows down for the link | Wi-Fi given up after 6.4 to 9.5 s. The core said "Level ground" throughout, in every run |
| Alerts during the fade | The probe is the helper's phone. It sends an urgent message every 0.5 s of the fade (20 in all), answers the alerts that come on Bluetooth, and takes Wi-Fi as delivering nothing once the fade began, the worst case. Every alert still arrives, and the alert link changes at most once | in 4 runs, all 20 alerts arrived and the link changed once. In the last 2 runs, 17 and 20 of them had gone out on Wi-Fi first and were sent again on Bluetooth |
| The bridge is killed | The launch file starts it again (#85), and the clock comes back | after 2.9 to 3.9 s of wall time |

Not tested here: the switch from GPS to the lidar map, which needs the lidar localisation (#43), and so a lidar, which this world has no GPU for.

## Camera depth and stairs on real phone frames (#131)

Measured on 9 November 2026 on real iPhone frames from the Hugging Face dataset
`mateoguaman/iphone_stairs_ramps`: 400 frames read from four row groups of its validation part,
filmed outdoors and indoors on a university campus with the camera held 4 to 20 degrees down. The
frames are not copied into this repository (the dataset states no licence). The dataset's own
labels are path points, not ground types, and its paths have no gravity, so 28 frames were labelled
by eye: 12 with stairs going up ahead, 14 of level floor, and 2 ramps. Each frame was cut to 480 x 270,
as the phone sends it. The depth is Depth Anything V2 Metric Indoor Small. The tilt is the first point
of the frame's own walked path, the camera height 1.0 m, and the field of view from the frame's
intrinsics.

| What | Frames | Result |
|---|---|---|
| Ground from the shape of the depth (`read_ground`, settings loosened for one camera) | 14 level | 3 read level; the rest ramp, drop, curb or steps |
| | 12 stairs | 3 read steps up, 4 a curb (a step), the rest flat, drop or ramp |
| | 2 ramps | both read a curb |
| Stairs from the detector (YOLOE-26s with the demo's classes, confidence 0.3 or more) | 12 stairs | 6 found; the misses were close up, where the steps fill the picture, or far off |
| | 16 level and ramps | **0 false alarms** |
| Up or down for those 6, from the depth (the box's middle above or below the floor) | 6 | 6 up, right in every one (0.32 to 0.85 m above the floor) |
| Distance to those 6, from the floor under the box's bottom edge | 6 | 1.7 to 7.2 m; no tape measure in this dataset, so checked by eye only |

What follows for the demo:

- **The ground read from the shape of the depth is not sent to the decision core** by default
  (`--depth-ground` sends it). The synthetic tests with the scale 20 percent off all passed, but
  real frames bend in ways those tests do not model, and it took level floors for drops: the
  person would be stopped on level ground.
- **Stairs come from the detector's box**: what it is from the box, where from the floor under
  the box (camera height and tilt), up or down from the depth. It found half the stair frames
  with no false alarm; on a walk towards stairs it has several frames to find them in.
- The depth still measures how far the nearest thing in the path is, and each detected object.
  On these frames walls read 2.5 to 2.7 m and open corridors 5.7 to 11 m, which look right but
  were not measured.
- Each frame took 1.27 s on the laptop's CPU for depth and the reader together.

## Obstacle distance: box height against a depth model, on real walks (#88)

Measured on 9 November 2026 with `tools/depth_vs_box.py` on SANPO-Real's test split: all 141
test sessions were tried, and the 45 with segmentation masks gave 259 frames with a person, a
vehicle or an animal. The detector (`yolo26n.pt` at 480) found 413 of those objects (a box matched
to the object's mask with IoU 0.5 or more, in 153 frames). The true distance is the median of
SANPO's depth over the object's mask; that depth is itself from stereo, so it is a reference
(on two frames it agreed with SANPO's other depth, from the ZED camera, to within 4 percent at the
median). The frames are from a chest camera (241 boxes) and a head camera (172) on people walking,
outdoors mostly, not from a phone; they are not copied into this repository.

Median error as a share of the true distance (share within 20 percent, share read more than 20
percent **too far**, the unsafe side):

| Boxes | n | Box height | Depth, indoor model | Depth, outdoor model | Nearer of box and indoor |
|---|---|---|---|---|---|
| all | 413 | 29% (32%, 38%) | 17% (56%, 15%) | 136% (2%, 94%) | 19% (52%, 6%) |
| 0 to 2 m | 15 | 27% (13%, 20%) | 53% (7%, 93%) | 253% (0%, 100%) | 27% (20%, 13%) |
| 2 to 4 m | 49 | 23% (43%, 18%) | 17% (55%, 43%) | 155% (0%, 100%) | 17% (51%, 8%) |
| 4 to 8 m | 114 | 19% (54%, 34%) | 10% (82%, 14%) | 151% (0%, 100%) | 13% (78%, 6%) |
| 8 m and more | 235 | 38% (20%, 44%) | 21% (47%, 5%) | 96% (4%, 90%) | 24% (41%, 5%) |
| person | 248 | 30% (28%, 38%) | 17% (55%, 23%) | 173% (2%, 94%) | 19% (52%, 7%) |
| car | 148 | 27% (36%, 37%) | 17% (55%, 5%) | 91% (3%, 95%) | 20% (51%, 5%) |
| cut by the frame | 70 | 36% (16%, 39%) | 17% (53%, 37%) | 153% (0%, 100%) | 24% (41%, 4%) |

- **The indoor model is better than box height from 2 m out**, for people and cars, and for boxes
  cut by the frame (17 against 36 percent).
- **Under 2 m it reads 1.5 times too far** (median), on 14 of the 15 boxes; box height does
  better there. Only 15 boxes, but that is the range where the chair stops.
- **The outdoor model reads 2 to 3.5 times too far** at every distance, outdoors. Over the bottom
  half of the picture it read 2.3 to 2.7 times SANPO's depth on three frames, at 266 and at 518
  pixels alike: it is not used.
- **Keeping the nearer of the two** loses little against the indoor model (19 against 17 percent)
  and reads more than 20 percent too far on 6 percent of the boxes, against 38 for box height
  alone and 15 for the depth alone. The demo now keeps the nearer (D12).
- The depth here is the model's own metres. The demo scales it to the floor first (D12 has why
  that is not measured here). Each frame took about 0.5 s on the laptop's CPU for each model.

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
**39 of 39 pass marks are met in Gazebo.**

### How a scenario is played

- A fresh simulation for each scenario: the route's world with the chair at the start of the run and
  the objects of the scenario (a box for the person). The chair settles first; the core is started
  then, with `config/core.yaml`, and the run begins with its first drive request (before every
  sensor has reported: a run can start with "Ground sensors not available, slowing down").
- The real sensors and nodes: the 3D camera (`depth_node`), the lidar, the IMU, the position and
  link nodes, the core. **The camera's ground classifier (#38) is not trained yet**, so a stand-in
  (`scenario_runner`) says what the scripted camera says (it names the ground, no distance) and is
  trusted less as the camera image gets dark: in proportion to the image's mean pixel value under
  60, a value chosen, not measured. It looks ahead from the camera (0.35 m ahead of the chair's
  centre) and knows the route's stretches, so it is right about the ground every time. The picture
  of stairs exists only in what the stand-in says.
- Faults are switched with the `set_fault` service (the camera is darkened for `dark`, the IMU stops
  for `imu_lost`); the user's commands are `UserCommand` messages at the times of the scenario.
- Distances are from the chair's front, as in the scripted world: the front wheels (0.47 m ahead
  of the centre) on wheels, the tracks' tip (1.1 m) on tracks, as the core is told in
  `config/core.yaml`. The ground truth gives the centre. On wheels the raised tracks reach 0.63 m
  further than the front wheels, so a box is that much nearer the tracks' tip than the table says.
- A mishap is the front on a step on wheels (stairs 22 to 22.7 m, curb 31 to 31.3 m), over the drop
  (38 m, or the chassis lower than -0.3 m), or the tracks' tip at a box's face. The tracks' tip
  counts on wheels too: raised, the tracks still reach 1.1 m ahead, about 0.4 m above the ground.
- A run that could not be played (the chair did not settle, the core did not answer, the time ran
  out) fails every mark and says why.

### Results

| Scenario | Pass mark | Scripted | Gazebo | Measured in Gazebo | Issue |
|---|---|---|---|---|---|
| `level` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `level` | Full speed on wheels | PASS | PASS | slowest 100% of top speed |  |
| `level` | Reaches the end of the route | PASS | PASS | reached 4.56 of 4.5 m |  |
| `level` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `ramp` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `ramp` | Slower on the ramp, on wheels | PASS | PASS | at most 30% of top speed on the ramp |  |
| `ramp` | Reaches the end of the route | PASS | PASS | reached 12.02 of 12 m |  |
| `ramp` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `stairs` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 1.96 m before the first step |  |
| `stairs` | Back on wheels once the ground is level | PASS | PASS | back on wheels at 5.80 m |  |
| `stairs` | Reaches the end of the route | PASS | PASS | reached 6.81 of 6.8 m |  |
| `stairs` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `curb` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 1.91 m before the first step |  |
| `curb` | Back on wheels once the ground is level | PASS | PASS | back on wheels at 6.02 m |  |
| `curb` | Reaches the end of the route | PASS | PASS | reached 6.23 of 6.2 m |  |
| `curb` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `stop` | Stop acted on within 1 s | PASS | PASS | stopped 0.20 s after the command |  |
| `stop` | Stays stopped until the user says go | PASS | PASS | held the stop |  |
| `stop` | Moving again within 1 s of go | PASS | PASS | moving 0.20 s after go |  |
| `stop` | Says "Stopped by you" | PASS | PASS | at 2.1 s |  |
| `stop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `drop` | Stops before the drop | PASS | PASS | stopped 0.58 m before the drop, after rolling 0.03 m |  |
| `drop` | Says "Drop ahead, stopping" | PASS | PASS | at 6.7 s |  |
| `drop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `manual_drop` | Follows the user in manual mode | PASS | PASS | manual from 0.3 s |  |
| `manual_drop` | Stops before the drop | PASS | PASS | stopped 0.81 m before the drop, after rolling 0.22 m |  |
| `manual_drop` | Says "Drop ahead, stopping" | PASS | PASS | at 2.0 s |  |
| `manual_drop` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `picture` | No track request on flat ground or ramps | PASS | PASS | never on tracks |  |
| `picture` | Slower on the way to the picture, on wheels | PASS | PASS | at most 30% of top speed on the way to the picture |  |
| `picture` | Reaches the end of the route | PASS | PASS | reached 9.03 of 9 m |  |
| `picture` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `dark` | Stops before the person | PASS | PASS | stopped 0.92 m before the person, after rolling 0.03 m |  |
| `dark` | Says "Obstacle ahead, stopping" | PASS | PASS | at 7.5 s |  |
| `dark` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |
| `imu_lost` | Track request at least 1.5 m before the first step | PASS | PASS | tracks requested 1.97 m before the first step |  |
| `imu_lost` | Stays on tracks while the tilt cannot be checked | PASS | PASS | stayed on tracks |  |
| `imu_lost` | Shows "On tracks until the ground is level" | PASS | PASS | at 18.2 s |  |
| `imu_lost` | No mishap: never on a step on wheels, over a drop or into an obstacle | PASS | PASS | none |  |

What is known about the missed marks:

- none

### The routes

| Scenario | Chair starts at | Run ends at | Differs from the scripted route |
|---|---|---|---|
| `level` | -1.5 m | 3 m | level ground for 4.5 m: after that the ramp at 8 m is in view of the 3D camera |
| `ramp` | 2 m | 14 m | the ramp is 4 m long (8 to 12 m) and rises 0.5 m, then the plateau |
| `stairs` | 18.2 m | 25 m | 3 steps of 17 cm in 0.66 m; the run ends 1 m before the stairs down |
| `curb` | 27 m | 33.2 m | the 12 cm curb is the edge of a 3 m platform; the end is 0.8 m before its end |
| `stop` | -1 m | 8 m | same marks, same limits |
| `drop` | 34.5 m | 38 m | the drop is at 38 m, 4 m after the curb; a drop is 1 m deep |
| `manual_drop` | 34.5 m | 38 m | in Gazebo nobody drives in manual mode: the speed stays at the last limit |
| `picture` | -1 m | 8 m | the picture is only in what the camera stand-in reports; nothing is in the world |
| `dark` | -1 m | 7 m | the person is a box 1.7 m high, its face at 6.8 m; the camera is darkened |
| `imu_lost` | 18.2 m | 25 m | same marks, same limits |

The Gazebo route is one course (`src/mana_sim/README.md`), so each scenario is a stretch of it.
Before the ramp at 8 m the 3D camera sees the ramp from 4 m away, so `level` is 4.5 m long. The
stairs down (26 m) and the drop (38 m) are never driven over. A drop is 1 m deep.

### Run it

```bash
colcon build && source install/setup.bash
ros2 run mana_core evaluate --world gazebo               # all ten (about 20 minutes)
ros2 run mana_core evaluate --world gazebo stairs curb   # some of them
ros2 run mana_core evaluate --world gazebo --rerun       # play again, also those with a trace
python3 -m mana_sim.gazebo_results gazebo_traces --update docs/results.md
```
<!-- end of gazebo results -->

## Zero-shot detectors on the SANPO test split (#51, #49)

Measured on 10 November 2026 with `tools/eval_detector.py`, on every SANPO test walk that has
masks (48 of the authors' 141 test sessions), one annotated frame in 10: **2,331 frames**. No
model was trained on SANPO; this is the baseline that the fine-tuned YOLO26s and YOLOv8s (#49)
have to beat. Each frame goes through `Detector.detect_image`, the call the ROS node and the phone
demo make. A box counts as found at IoU 0.5 with a SANPO box of the same class; the model's names
map to SANPO's by a fixed table (person to pedestrian, car, bus and truck to vehicle, door to
opening-door, pillar to pole). Precision and recall are given at two thresholds of the node:
**0.3**, the lowest confidence the detector keeps (and the one the demo's stairs use, #131), and
**0.5**, the lowest that becomes an obstacle (`min_confidence`, the core's `min_trust`). The
second is the one that stops the chair.
Times are the median per frame on an AMD Ryzen 7 7730U laptop CPU (16 GB), with PyTorch 2.15 and
Ultralytics 8.4.208.

SANPO's boxes come from its masks, so they include objects far down the street a few pixels
high. MANA reacts to what is near, so **near** keeps only boxes at least 10 % of the frame's
height (predictions on smaller boxes are left out, as COCO scores its size bands).

Near objects:

| Model (image size, ms per frame) | Class | Objects | AP50 | Precision at 0.3 | Recall at 0.3 | Precision at 0.5 | Recall at 0.5 |
|---|---|---:|---:|---:|---:|---:|---:|
| YOLO26n, COCO (416, 36 ms) | pedestrian | 2,993 | 0.74 | 0.85 | 0.67 | 0.91 | 0.54 |
| | vehicle | 1,438 | 0.67 | 0.83 | 0.55 | 0.94 | 0.40 |
| | stairs, door, pole | | not in COCO | | | | |
| YOLOE-26n, the demo's 42 names (480, 55 ms) | pedestrian | 2,993 | 0.73 | 0.85 | 0.67 | 0.91 | 0.49 |
| | vehicle | 1,438 | 0.62 | 0.82 | 0.51 | 0.90 | 0.29 |
| | stairs | 37 | 0.01 | 0.20 | 0.03 | 0.00 | 0.00 |
| | opening-door | 175 | 0.01 | 0.00 | 0.00 | 0.00 | 0.00 |
| | pole | 1,942 | 0.03 | 0.73 | 0.01 | 0.71 | 0.00 |
| YOLOE-26s, the demo's 42 names (480, 82 ms) | pedestrian | 2,993 | 0.78 | 0.80 | 0.78 | 0.87 | 0.71 |
| | vehicle | 1,438 | 0.69 | 0.80 | 0.54 | 0.90 | 0.37 |
| | stairs | 37 | 0.05 | 0.25 | 0.03 | 0.50 | 0.03 |
| | opening-door | 175 | 0.01 | 0.20 | 0.01 | 0.50 | 0.01 |
| | pole | 1,942 | 0.02 | 0.62 | 0.01 | 0.50 | 0.00 |

Over every object, far ones included, recall is much lower: people 0.41 to 0.54 at 0.3 and 0.29
to 0.46 at 0.5, vehicles 0.24 to 0.30 at 0.3 and 0.12 to 0.18 at 0.5. A precision of 0.00 means boxes of that
class reached the threshold and every one was wrong. The tool prints the full tables, both bands.

**Stairs per walk.** The test split has stairs in only 6 walks (65 frames, 76 staircases; see
`docs/datasets.md`). Both YOLOE models found stairs in 1 frame of the 65 at confidence 0.3,
in 1 walk of the 6. Run at confidence 0.01, YOLOE-26s gave no stairs box at all in 58 of the 65
frames; where it did, near the foot of a staircase that fills the frame, the box was right (IoU
0.77 to 0.98) but its confidence was 0.02 to 0.67. Many of SANPO's stairs are porch steps far
off or a single step at the edge of the frame, which MANA does not need, but clear stairs beside
the path were missed too.

What follows:

- **People near the chair are found zero-shot, but at the obstacle threshold many are missed.**
  At 0.5, YOLO26n (the node's default) reports about half the people (0.54) and 40 % of the
  vehicles near the chair, at 91 to 94 % precision. YOLOE-26s reports the most people, 71 %, at
  87 % precision and 82 ms a frame (about 12 frames a second on this CPU). Lowering the obstacle
  threshold to 0.3 would catch more (0.67 to 0.78 of people) at 80 to 85 % precision, so more
  false stops; that is the `min_trust` trade-off of #87 and #175, and it is one frame: on a walk
  towards a person the detector has many frames to find them in.
- **Stairs, doors and poles are not found zero-shot on SANPO.** The 6 of 12 stairs YOLOE-26s
  found on the iPhone frames in #131 were chosen with the stairs straight ahead; SANPO's walks are
  the harder, everyday case. Stairs must come from fine-tuning (#49, 45 training walks with
  stairs) and from the 3D camera, as the two-source rule already requires. SANPO's
  "opening-door" boxes are ordinary doors, closed ones too (checked by eye on four frames: shop
  doors, a glass door), so the door misses are real misses: doors set into a shop front at the side
  of the path, where the model sees a wall or a window.
- **Six walks are too few for a stairs figure against the 0.85 target**, before or after
  fine-tuning. Stairs from our own recordings (#89) are needed for that.

Run it:

```
python3 tools/fetch_sanpo.py --root datasets/sanpo-eval --train 6 --test -1 --every 10 --workers 16
python3 tools/sanpo_to_yolo.py --root datasets/sanpo-eval --out datasets/sanpo-eval-yolo --val 0.5
python3 tools/eval_detector.py --data datasets/sanpo-eval-yolo --weights yolo26n.pt --imgsz 416
python3 tools/eval_detector.py --data datasets/sanpo-eval-yolo --imgsz 480 \
    --weights yoloe-26n-mana.pt yoloe-26s-mana.pt   # built with demo/build_detector.py
```

## A first fine-tune on SANPO, on a laptop CPU (#49)

A trial run before the GPU runs of #49, to check the pipeline end to end and to see whether
SANPO's labels teach the classes the zero-shot models miss. YOLO26n (COCO weights) was fine-tuned
on 48 SANPO training walks: the 45 that show stairs (one annotated frame in 20) and 3 others
already on disk (one in 10). That is 1,166 training frames from 43 walks, with 378 stairs, and
145 validation frames from the other 5 walks, held out by walk. 20 epochs at image size 416,
batch 16, Ultralytics' default settings otherwise, 72 minutes on the laptop CPU above. It was then
scored exactly as the zero-shot models were, on the same 2,331 test frames, so the rows compare.

Near objects, against zero-shot YOLO26n (and YOLOE-26s for the classes COCO lacks):

| Model (image size, ms per frame) | Class | Objects | AP50 | Precision at 0.3 | Recall at 0.3 | Precision at 0.5 | Recall at 0.5 |
|---|---|---:|---:|---:|---:|---:|---:|
| YOLO26n fine-tuned, SANPO's 15 names (416, 35 ms) | pedestrian | 2,993 | 0.77 | 0.87 | 0.71 | 0.93 | 0.63 |
| | vehicle | 1,438 | 0.72 | 0.84 | 0.64 | 0.92 | 0.56 |
| | stairs | 37 | 0.14 | 0.06 | 0.19 | 0.11 | 0.16 |
| | opening-door | 175 | 0.04 | 0.14 | 0.03 | 0.43 | 0.02 |
| | pole | 1,942 | 0.10 | 0.33 | 0.09 | 0.43 | 0.04 |
| YOLO26n zero-shot (416, 36 ms) | pedestrian | 2,993 | 0.74 | 0.85 | 0.67 | 0.91 | 0.54 |
| | vehicle | 1,438 | 0.67 | 0.83 | 0.55 | 0.94 | 0.40 |
| YOLOE-26s zero-shot (480, 82 ms) | stairs | 37 | 0.05 | 0.25 | 0.03 | 0.50 | 0.03 |

**Stairs per walk.** The fine-tuned model found stairs in 7 of the 65 test frames with stairs, in
2 of the 6 walks (YOLOE-26s: 1 frame, 1 walk). Counting near staircases only, it found 7 of 37
frames, with a mean recall per walk of 0.24 over 5 walks.

What follows:

- **Fine-tuning helps the obstacles that stop the chair, at no cost in time.** At the obstacle
  threshold 0.5, people near the chair go from 0.54 to 0.63 recall and vehicles from 0.40 to
  0.56, with precision staying at 0.92 to 0.93; the model is the same size, 35 ms a frame.
- **Stairs are found more often but not trusted.** Recall at 0.3 rises from 0.03 to 0.19, but
  only 6 % of the stairs boxes at 0.3 are right (11 % at 0.5), so most of the time it reports
  stairs where there are none. 378 training stairs and 20 CPU epochs are not enough. On the
  chair a stairs box from the camera alone is not acted on, since stairs need the 3D camera too
  (the two-source rule), but a detector this noisy would make that check carry all the weight.
  It is still far from the 0.85 target.
- **Doors and poles barely move** (recall 0.03 and 0.09 at 0.3). Poles are thin and often far;
  SANPO's door boxes are mostly doors set into shop fronts. Both need more data than this run had.
- The GPU runs of #49 (YOLO26s and YOLOv8s, image size 640, more epochs, every frame of the
  stairs walks) are the real test; this table is the floor they start from. The fine-tuned
  weights are not yet the node's default.

Run it (after the fetch above):

```
python3 tools/fetch_sanpo.py --root datasets/sanpo-eval --every 20 \
    --only datasets/sanpo-masks/sessions_with_stairs.txt   # list from --sessions stairs
python3 tools/sanpo_to_yolo.py --root datasets/sanpo-eval --out datasets/sanpo-stairs-yolo
yolo detect train data=datasets/sanpo-stairs-yolo/data.yaml model=yolo26n.pt imgsz=416 \
    epochs=20 batch=16 device=cpu seed=0
python3 tools/eval_detector.py --data datasets/sanpo-stairs-yolo --imgsz 416 \
    --weights runs/detect/train/weights/best.pt
```
