# Field walks with the phone

Six short walks give the real-world numbers that #89, #129, #131 and #133 are waiting for. The
tools are ready: `python -m demo.server --record` keeps everything the phone sends, and
`python -m demo.field` scores a walk against what is known about it. One session of about an hour
covers all six. The results go in `docs/results.md`.

## Before you go

- The laptop and an Android phone with Chrome, joined by a USB cable (`demo/README.md`, *Connect
  the phone*). With the cable the phone's Wi-Fi can be off: walks 1 and 5 need that.
- A tape measure (5 m or more), and paper for what the walk does not record.
- The detector weights `yolo26n.pt`, and for walk 3 `yoloe-26n-mana.pt` (built once with
  `python -m demo.build_detector`, which needs the internet). For walks 3 and 4 the depth model
  downloads once, about 100 MB, so start the server with `--depth` before leaving the internet.
- Each walk is recorded into its own new folder (a folder that holds a recording is refused).
  **Recordings show faces and hold GPS positions: they stay in `recordings/`, which is not in git.
  Before a frame from one goes into a report or a slide, the faces are blurred, the GPS is left
  out, and anyone recorded is asked first (`docs/datasets.md`).**
- Carry the phone as it would sit on the device: held still, camera forward, about 1.2 m up
  (`--camera-height` if not). Every walk here is on foot, so each command has `--on-foot`.

## The walks

| # | Walk | Start the server with | Note down on the spot | Feeds |
|---|---|---|---|---|
| 1 | **Indoor loop**: a corridor loop of 50 to 100 m, ending where it began. Phone Wi-Fi off, no GPS indoors | `--on-foot --record recordings/loop-1` | the steps you take (count them) and the place | #129, #133 |
| 2 | **Outdoor route**: 100 to 200 m on a campus road, its length measured on a map or with a wheel | `--on-foot --record recordings/road-1` | the route's length, the steps, the place | #129, #133 |
| 3 | **Stairs**: walk from about 6 m away towards stairs going up, stop at the first step; then the same towards stairs going down, stopping at the top | `--on-foot --depth --weights yoloe-26n-mana.pt --imgsz 480 --record recordings/stairs-1` | tape marks at 4, 3, 2 and 1 m from the first step; the time you pass each, from a watch | #131, #89 |
| 4 | **A person ahead**: a person who has agreed to be recorded (ask first) stands still; stop for 5 s at 5, 3, 2 and 1 m from them, measured with the tape | `--on-foot --depth --record recordings/person-1` | that they agreed, the distance at each stop, and what the dashboard said there (the obstacle distance) | #131, #88 (D12, #172) |
| 5 | **Alert link**: during a short walk, switch Wi-Fi off and tap the page's Wi-Fi tile off (the page cannot see the radio itself, `demo/README.md`); then the same for Bluetooth; then both back on, radios and tiles | `--on-foot --record recordings/link-1` | the times you switched each | #89 |
| 6 | **Dark corridor**: a corridor with its lights on, then off, walked slowly | `--on-foot --record recordings/dark-1` | where the lights went off | #87 (light factor), #89 |

## Scoring

```bash
python -m demo.field recordings/loop-1 --loop --steps 212 --place "corridor, floor 2"
python -m demo.field recordings/road-1 --distance 150 --steps 210 --place "campus road"
python -m demo.replay recordings/stairs-1      # into a running server, to see it again
```

- Walks 1 and 2: `demo.field` prints the steps and the distance against yours, the gap from end to
  start for a loop, and a row for the table in `docs/results.md` (#133). The date comes from the
  recording, not from the day it is scored.
- Walks 3 and 4: play the recording into a server with the same options and compare what the
  dashboard says (the stairs and where they are, the obstacle distance) with the tape marks. For
  walk 4: on SANPO's walks the distance kept (the nearer of box height and depth, D12 in #172)
  read more than 20 percent too far on 6 percent of the boxes; see whether the phone does as well.
- Walks 5 and 6: the replay shows when the link changed, and the trust of the camera's reports
  (in the dashboard's message list) in the dark; compare with your times.

## What each walk answers

- **#129 and #133**: how far off the step count and the tracked distance are, indoors on steps
  alone and outdoors with GPS, and how far a closed loop ends from its start.
- **#131 and #88**: whether stairs are found and placed right on real frames, and how near the
  camera's obstacle distance is to the tape (box height against depth, D12 in #172).
- **#87**: whether the light factor lowers the camera's trust where it should, on real dark frames.
- **#89**: real recordings, replayed through MANA, for every one of the above.
