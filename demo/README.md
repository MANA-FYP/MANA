# MANA phone demo

The decision layer driven by **real data from a phone**, shown on a laptop dashboard. No ROS 2 and
no Gazebo. Nothing in the demo is simulated: a sensor the phone's browser can read is used, and a
sensor it cannot read is shown as **N/A** with the reason.

```
phone browser  ----USB cable---->  laptop server  -->  laptop browser (dashboard)
 sensors, camera frames, audio       middleware +        inputs, middleware,
 sensor tiles, command  <-command-   decision layer      decision rules, camera
```

Both pages are one screen each, so neither needs scrolling. The dashboard shows the inputs on the
left, the camera in the middle, and on the right the decision as a sentence with a written log of
every decision (stop and go, Wi-Fi to Bluetooth, GPS to indoor map, a sensor going on or off,
obstacles), what the user said into the microphone, and a small middleware box. The laptop speaks
each decision.

## Run it

```bash
pip install -r demo/requirements.txt
python -m demo.server                         # dashboard at http://localhost:8000/dashboard
```

### Connect the phone: USB cable (Android)

A cable means the phone's Wi-Fi and Bluetooth can be switched off during the demo without losing
the data, so you can show the alert link changing.

1. On the phone: Settings, About phone, tap *Build number* 7 times; then Developer options, turn
   on *USB debugging*. Plug the phone in and accept the question on the phone.
2. On the laptop (needs `adb` from the Android platform tools):

   ```bash
   python -m demo.usb
   ```
3. On the phone open in Chrome the phone address the server printed: **http://localhost:8000/phone?k=...**
   (the key, below).

`localhost` counts as a secure page, so the phone gives the camera, motion, location and
microphone with no certificate.

### Connect the phone: Wi-Fi, no cable (Android 11 and later)

The same tunnel, over Wi-Fi: the page is still on `localhost`, so it keeps the camera and the
sensors. The phone and the laptop must be on the same network; the laptop's own hotspot works,
with no internet.

1. On the phone: Developer options, *Wireless debugging*, turn it on.
2. The first time on a laptop, tap *Pair device with pairing code* and give the pairing address
   and the code it shows, with the address shown on the Wireless debugging page itself:

   ```bash
   python -m demo.usb --pair 192.168.1.23:37011 482915 --wifi 192.168.1.23:41235
   ```

   After that, `python -m demo.usb --wifi 192.168.1.23:41235` is enough. Android may change that
   port when wireless debugging is turned off and on again.
3. On the phone open in Chrome the phone address the server printed:
   **http://localhost:8000/phone?k=...** (the key, below).
4. Turn *Wireless debugging* off after the demo: pairing gives the laptop the same control over
   the phone (adb) as the cable.

The dashboard shows the round trip to the phone under *Inputs from the phone*. Compare it with the
cable before a demo. The phone's Wi-Fi then carries the data, so use the cable to show the alert
link changing (switching Wi-Fi off would cut the demo).

### The key

The server can drive the device, so it answers only requests that carry its key (`demo/access.py`,
#178). The key is made once and kept in `certs/access-key.txt` (not in git), and the server prints
every address with it (`.../phone?k=...`). A page opened with it keeps the key in a cookie and
reloads without it, so the key does not stay in the address bar or in screenshots. Share an address
with its key with no one; delete `certs/access-key.txt` for a new key. `demo.camera`, `demo.replay`
and `tools/phone_e2e.py` read the key from that file.

### Connect the phone over the network: no cable, no adb

Chrome gives the camera, the motion sensors and GPS only to a secure page: `localhost` (the cable),
or https. Two ways to an https page:

**Same Wi-Fi (the fastest):** the laptop serves https itself.

```bash
python -m demo.server --https          # prints the phone's address, https://192.168.1.2:8000/phone?k=...
```

Open that address on the phone and, the first time, tap *Advanced*, then *Proceed*: the certificate
is the laptop's own (`demo/certs.py`, kept in `certs/`, not in git). The data stays on the local
network. If the server lists more than one address, the first is the Wi-Fi's; the others can be
virtual adapters (WSL, VirtualBox) that the phone cannot reach.

**Anywhere with internet:** a Cloudflare quick tunnel gives the laptop a public https address, so
the phone can be on mobile data, with no warning.

```bash
python -m demo.server                  # as usual
python -m demo.tunnel                  # prints https://<random words>.trycloudflare.com/phone?k=...
```

It needs Cloudflare's `cloudflared`, from Cloudflare's release page. Every message goes through
Cloudflare, so the round trip is longer than on the same Wi-Fi: from Pakistan the tunnel goes
through Singapore, and our phone's round trip was about 230 to 250 ms. Watch the dashboard on the
laptop at the `localhost` address the server printed, not through the tunnel, or every frame makes
that trip twice. Anyone with the tunnel's address and its key can drive the device and see the
camera: share it with no one, and stop the tunnel (Ctrl+C) when the demo ends. The tunnel does not
start before the server has made its key.

Keep the phone page open on the screen. Chrome slows a page in the background or with the screen
off to a frame a second or less, so the page asks to keep the screen on. On a slow link the page
drops old camera frames and voice rather than sending them late.

The dashboard shows the round trip to the phone under *Inputs from the phone*, for any of these ways.

### Or any other camera

The vision part can be tested without the phone (#132). `demo.camera` sends frames from the
laptop's webcam, a USB or IP camera, or a video file to the server, the way the phone page does:

```bash
python -m demo.camera --pitch 10                  # the laptop's webcam, 10 degrees down
python -m demo.camera --source 1 --pitch 25       # a USB camera
python -m demo.camera --source rtsp://192.168.1.20/stream --pitch 30   # an IP camera
python -m demo.camera --source walk.mp4 --pitch 8 --loop    # a video file
```

Such a camera has no motion sensor, so `--pitch` says how far below level it looks, and that tilt
is sent as the phone's gravity reading would be. It has no default: give the tilt, or
`--no-pitch` to send none (stairs then come without a distance). Needs `opencv-python`.

Set `--pitch` to the camera's real tilt, within a few degrees. Stairs up or down comes from the
depth, the camera's height and its tilt, and a wrong tilt turns stairs up into stairs down.
- **Real iPhone frames, each with its own tilt (5 to 9 degrees down):** stairs up were read as up
  on all 6 frames with a stairs box, from 8 degrees too little to 4 degrees too much. One frame
  read down at 8 degrees too much.
- **The stairs video, which only goes up, played with `demo.camera`:** `--pitch 5` gave 10 of 10
  stairs reports up. `--pitch 12` gave 4 up and 8 down; 20 and 30 gave nearly all down.

The phone measures its own tilt, so this applies to cameras without a motion sensor. Fitting the
tilt from the floor in the depth was tried and is not good enough: on level real frames it was up to
17 degrees off.

### Record a walk and play it again

A walk can be kept and run through MANA again, the same messages at the same pace (#89):

```bash
python -m demo.server --record recordings/stairs-1      # use the phone as usual
python -m demo.replay recordings/stairs-1               # later, into a running server
python -m demo.replay recordings/stairs-1 --speed 4     # four times as fast
```

The folder has `session.jsonl`, one line per message from the phone with its time, each camera
frame as a JPEG in `frames/`, and `meta.json` with when the walk began (UTC), which stays right
when the folder is copied. The microphone's raw sound is not kept; the words heard are.
A folder that already holds a recording is refused, so a restart cannot overwrite one.
Steps are counted on the phone's own clock, so a faster replay walks the same path. A recorded
40 s walk (1,324 messages, 207 frames, 4.8 MB) played back into a fresh server ended on the same
step count and GPS position as when it was live. The frames show faces: `recordings/` is not in
git, and a recording is kept private unless the faces are blurred first.

**Field accuracy of a walk (#133).** Record a walk whose truth you know, then score it:

```bash
python -m demo.server --record recordings/loop-1          # walk a closed loop, count your steps
python -m demo.field recordings/loop-1 --loop --steps 212 --place "corridor, floor 2"
python -m demo.field recordings/road-1 --distance 120 --steps 160 --place "campus road"
```

`--loop`: the walk ended where it started, so the gap between the track's end and start is its
error. `--distance`: the real length (a tape, or a route measured on a map). `--steps`: the steps
you counted. It prints the steps and the distance against those, the gap, and a row for the table
in `docs/results.md`. Do one loop indoors (steps alone, no GPS) and one outdoors (GPS). The six walks
that give the field numbers, with what to note on the spot, are in `docs/field-walks.md`.

## The phone screen

Eight tiles in two columns, then Automatic and Manual, and the STOP and GO buttons. Tapping an
active sensor tile switches that sensor off and MANA answers at once in the decision box and by
voice (GPS off moves the position to the indoor map); tapping it again switches it on. A web page
cannot switch Wi-Fi or Bluetooth itself, so tapping those tiles declares them off for MANA. The phone is a sensor input only: the
decision and its spoken message are on the laptop dashboard (Chrome needs one click on the page
before it will speak; the Voice button turns it off). Text sizes follow the screen height, so
nothing scrolls.

The strip under the tiles shows the message the laptop is saying now, the same one, in the same
order (see "What the user hears" below).

| State | Meaning |
|---|---|
| **Active** | the phone is streaming it now, with its live value and a trust bar |
| **Tap** | the browser has it, but nothing is arriving yet (permission not given) |
| **N/A** | a web page cannot read it, with the reason |

## What the user hears

The decision layer makes a message whenever something changes: a decision, the alert link, the
position source, a sensor going on or off. Said as they come, an old line could be spoken before a
new stop. They go through one queue (`mana_core.messages`), so the laptop and the phone show and
say one message at a time:

- the most urgent goes first (a stop, then warnings, then information); the same priority goes
  oldest first;
- a message waits until the one before it has been said; a stop does not wait for a lower message
  and replaces the waiting ones, and it is answered every time it is pressed;
- a message older than 6 s is dropped, and the same decision is not said twice in a row.

Start the server with `--directions` for a smart cane or glasses host: it also says what is
ahead ("Steps up ahead, 2 metres.", "Drop ahead, less than a metre. Stop.", "Person ahead, 1
metre."), one direction at a time, only from reports the core trusts.

## The caregiver view

A caregiver (the secondary user, UC-9) follows the user from their own phone at
**http://localhost:8000/caregiver**. It shows whether MANA is moving, slowing down, stopped or
driven by the user in manual mode, what it is doing in one sentence, the mode, the speed limit,
where the position comes from, which radio carries alerts, and the last eight events (decisions,
and sensors and links coming and going), newest first. Sentences written to the user are reworded
for a helper ("Stopped by the user. They are in control."). It updates every 0.1 s from the same
state as the dashboard.

It is **read-only**: the page has no controls, and the server ignores anything sent on its socket
(`/ws/caregiver`), so it cannot stop or drive the chair even by hand. It leaves out the camera
image and the raw sensor stream, which a helper does not need. If the user's phone stops sending
for 3 s, the view says MANA is offline and asks the caregiver to check on them, and it shows the
last known values under **Last known**. `/api/status` gives the same view once as JSON.

For a caregiver on another phone on the same Wi-Fi, start the server with `--host 0.0.0.0` and
open `http://<laptop IP>:8000/caregiver`. That also opens the dashboard, which **does** have
STOP and GO, to that network, so do it only on a network you trust.

## What is real

| Tile | In the demo | What the decision layer does with it |
|---|---|---|
| Camera | real frames and light level | light level sets the trust of camera reports; frames go to the detector |
| Motion | real tilt, vibration, steps and compass heading | level or not, measured from how the phone is held in its first half second (switch Motion off and on to take it again); shaking counts as rough ground on the device, so it changes to tracks, and never on foot; steps and heading move the position |
| GPS | real position and accuracy | worse than 15 m for 3 s leaves GPS, better than 8 m for 5 s returns; a good fix corrects the position |
| Microphone | real audio and the browser's recogniser | the words stop, go, on, off, up, down become commands |
| Wi-Fi | the network type the browser reports (Android Chrome); switched off shows Not available | gone for 2 s means alerts would move to Bluetooth |
| Bluetooth | what the browser says about the Bluetooth adapter (asked every second). Chrome does not always follow the Settings switch, so tap the tile to declare it off | Wi-Fi gone and Bluetooth on means alerts use Bluetooth; both off means no link |
| Depth | **N/A**, browsers do not expose depth sensors | nothing |
| Lidar | **N/A**, browsers do not expose lidar | nothing |

The alert link is a **decision**: a web page cannot open a Bluetooth connection to the chair, so
the dashboard shows which radio the alerts *would* use. The data of the demo itself travels over the
USB cable.

## Position and path travelled

The dashboard's *Position and path* box shows where the person is and the path they walked, in
metres from where they started, north up, with the distance, the steps, the heading and the speed
(`demo/navigation.py`, #129). It needs no internet and no Wi-Fi: the phone reaches the laptop over
the USB cable, and GPS works without a network.

- **Steps.** The phone sends its acceleration about 30 times a second. A step is a rise and fall
  of its size, at a walking pace; faster shaking is not a step. The length of each step comes from
  how hard it swings (Weinberg), between 0.3 and 1 m.
- **Heading.** The compass from the phone's absolute orientation (Android Chrome), or the heading
  an iPhone gives. Held upright for the camera, it is where the back camera looks.
- **GPS.** Each step moves the position along the heading; a GPS fix better than 25 m corrects it
  (a Kalman filter: a step adds uncertainty, a fix of accuracy *a* counts with variance *a*²).
  A fix far from where the steps say is refused as a jump, unless three in a row agree.
- **Indoors**, or with GPS lost or switched off on the phone, the position carries on from the
  steps, and the path turns from blue (GPS) to orange (steps). The first good fix ties the path to
  latitude and longitude.

**Street map.** Tick *Street map*, and once a GPS fix has tied the path to the earth and the
laptop has internet, the path is drawn on the OpenStreetMap streets around it, at the same scale
(#130). It is off at first because each map tile is fetched from openstreetmap.org, which then
learns roughly where the person is. The laptop keeps each tile it fetched (`--tile-cache`,
default `tile-cache/`, not in git; a tile older than a week is fetched again when online), so a
place seen once with internet has its street map without it too (#161). A tile never seen stays
the plain grid without internet; the box works the same either way, and a missing tile is tried
again every 30 s.

**Marked places and the way back.** Say "mark this place" into the phone (or press *Mark this
place* on the dashboard), walk on, then say "where is the mark" (or *Way back*). MANA leads back
along the path walked, since indoors the straight line goes through walls (#157). It gives the
first leg as a clock face, 12 o'clock straight ahead, as orientation and mobility training gives
directions, then the turn after it: "Back along your path, the mark is 26 metres: 12 metres at
6 o'clock, then turn to 9 o'clock for 15 metres." Asking again on the way gives the next leg from
there. Loops in the walk (into a room and out again) are left out, and a bend under 25 degrees is
not a turn. Without a compass it gives compass directions. If the path back to the mark is no
longer kept (a very long walk), it gives the straight line instead: "The mark is 14 metres away,
at 6 o'clock." The mark shows as a flag on the path.

**A destination picked on the map.** On the live page, click a place on the map (a drag still
moves the map). A green flag marks it, and the phone says "Destination set. The destination is
30 metres away, at 12 o'clock." It says the way again after each 15 m walked, and sooner (but no
more than once in 10 s) when the direction moves two hours round, as after a wrong turn. Within
3 m it says "You have arrived at the destination." and clears it. Say "where is the destination"
to hear it at any time; the X on the map clears it. It is the straight line, not a route: streets
and walls are not known.

**Starting again.** The anticlockwise-arrow button on the live map, after a confirm, clears the path,
the steps, the marks and the destination, and the messages and decisions so far, and the phone
says "Starting again from here." The track starts at the person's place, tied to the map at the
next GPS fix. The sensors, the core, the link and the way the phone faces are kept, and a stop
still waiting to be said is still said.

**The indoor screen (`/indoor`).** A second screen beside `/live`, on the same server and the same
phone: which floor, the height travelled, the newest map and the laptop's Wi-Fi.
- **Which floor.** A web page gets no barometer (Chrome has no web API for one), so the floor comes
  from what the page can read. A lift: the phone, held still, feels the lift speed up and slow down,
  and the vertical acceleration integrated twice is the height; a lift ends at rest, so the speed
  left at the end is taken out as drift (a phone tilted on the way). Stairs: the camera's detector
  (`--weights yoloe-26s-mana.pt` names stairs) sees stairs up or down within 1.5 m, and each step
  taken while it still sees them (up to a second after) is a riser (0.17 m); three quarters of a
  floor's risers is a floor. Steps out of its sight, past a staircase or along a landing, do not
  count. The detector misses stairs close up, where they fill the picture (the stairs row in
  `docs/results.md`), so a climb can come out short: a missed floor shows as "less than a floor",
  never a floor made up. Motion only measures changes, so the floor is "?" until there is a
  reason: walking in from outside (GPS good, then lost at the door) sets the ground floor, as an
  assumption the page states; the user says it ("floor two"); or the supervisor taps it. Until then the floors moved
  since the start are counted. GPS altitude is shown beside it only as a reference: a phone's GPS
  height is often 10 m or more off indoors.
- **The newest map.** The satellite layer is Esri World Imagery, whose current release is the
  newest it has; the map shows the date the picture at that point was taken and by what satellite
  (`/api/imagery`, from Esri's imagery metadata). For the FAST campus it is 28 Aug 2025, 0.46 m a
  pixel. Tiles are kept a week, then fetched again.
- **Wi-Fi.** The laptop's own Wi-Fi signal, read twice a second. Windows gives only the RSSI, in
  whole dBm, refreshed rarely (it did not change once in 20 s here), which is too coarse to sense
  people. Wi-Fi sensing needs the channel state (CSI), from hardware such as two ESP32 boards: the
  Wi-Fi screen below.
- Tested on synthetic lift rides (up one floor, down two), a bump, walking, a stairs flight of 19
  steps and a few steps, and a lift taken right after a walk (`tests/test_demo_floors.py`). Not yet
  measured on real lifts and stairs.

**The Wi-Fi screen (`/wifi`): people from Wi-Fi.** Whether someone is in a room, and in which
zone, from how their body changes the Wi-Fi signal between two ESP32 boards, with no camera.
- The receiving ESP32 runs esp-csi's console firmware and prints one `CSI_DATA` line per packet on
  USB, about 200 a second: the amplitude of 56 subcarriers. `demo/csi.py` cuts them into windows of
  one second and gives each window to a small linear model (`demo/csi_model/wifi_csi_office.npz`), run
  with numpy. The screen shows the verdict and the model's share for each label, the 56
  subcarriers over the last 10 s, the movement against the empty room's level, and how the model
  scored on sessions it never saw.
- With no ESP32 yet, `--csi-replay` plays recorded sessions of two ESP32-C6 boards in an office
  (the Office Localization set, CC BY 4.0) at their own pace: the room empty, then a person in
  zone 1, 2 and 5, 20 s each, or one picked on the screen. They are the authors' test sessions, not
  the ones the model was fitted on, so the screen marks each verdict right or wrong as it plays.
  ```bash
  python tools/wifi_csi.py --data datasets/office_csi --fetch       # the recordings, about 950 MB
  python -m demo.server --csi-replay datasets/office_csi            # then open /wifi
  python -m demo.server --csi-serial COM5     # or a live ESP32 (pip install pyserial)
  ```
  The baud rate (`--csi-baud`, 115200 by default) matters only through a USB-to-UART bridge. On the
  ESP32-C6's own USB port it is ignored. A row is about 440 bytes, so 200 a second is about 88 KB/s.
  115200 baud carries about 11.5 KB/s, about 26 rows a second, so through a bridge set the board
  and `--csi-baud` to 921600 or more, or the flicker no longer matches what the model was fitted on.
- The model holds for the room and the placement of the boards it was fitted in. A new place needs
  its own recordings (a few minutes empty and with a person in each zone) and `tools/wifi_csi.py`
  run on them. A person standing still is found by the shape of the subcarriers, not by movement:
  in two of the three zones the movement stayed at the empty room's level.
- Not yet an input to the decision core. The use proposed: a person sensed by Wi-Fi but not seen by
  the camera (round a corner, behind a door) slows the device and is said; one source, so it never
  stops it.

**Saving a walk.** *GPX* saves the walk for any map app (latitude and longitude, so only after a
GPS fix), with the marks as waypoints. *GeoJSON* always works: in longitude and latitude after a
fix, otherwise in metres from the start (it says so).

Limits: the compass is disturbed near steel and motors, and step counting measures a person
walking. On a device with wheels the steps would come from wheel odometry instead (#78).

## Depth from the camera image

`python -m demo.server --depth` adds a depth model (Depth Anything V2, metric, small; Apache-2.0)
that runs on the laptop's CPU, about half a second a frame on its own (more while the detector
runs). The first run downloads it (about 100 MB) from Hugging Face; after that it works offline.
With the detector it gives the camera what it could not measure before (#131):

- **Distances in metres**: to each obstacle the detector finds (the depth in the middle of its
  box, or the box height's estimate when that is nearer: under 2 m the depth read people 1.5
  times too far on real walks, `docs/decisions.md` D12), and to the nearest thing standing in the path, whatever it is (a wall, a door, a pole).
  Stairs up rise into that from their second or third step, so while the detector sees stairs up,
  something from where they start to 1.5 m past it is taken to be the stairs, not an obstacle
  (#159). The stairs rule handles them, and the dashboard says "the stairs rise at ...".
- **Stairs, where and which way**: when the detector (the open-vocabulary YOLOE model from
  `demo/build_detector.py`) boxes stairs, the stairs are where the box's bottom edge meets the
  floor (camera height and tilt), and up or down by whether the depth in the box is above or
  below the floor. They go to the core as the camera's ground report.
- **The ground read from the shape of the depth** (level, ramp, curb, steps, drop, with the same
  reader as the 3D camera) is shown on the dashboard but **not sent to the core** unless the
  server has `--depth-ground`. On real phone frames it took level floors for drops
  (`docs/results.md`, "Camera depth and stairs on real phone frames"); stairs from the detector
  had no false alarm there.

The model's own metres can be 10 to 20 percent off, so the depth is scaled first: the phone's
motion sensors give the camera's tilt, the person holds it at a known height (`--camera-height`,
default 1.2 m), and level floor at the bottom of the picture must lie that far below the camera.

- **Start on level floor.** The scale is learned from the first three frames whose bottom rows
  are level floor ("learning the scale: point at level floor"), and then held. Level floor means
  the bottom rows lie on a plane at the phone's own tilt: on a plane 1/depth is a straight line in
  the image row, whatever the model's scale, so its slope gives the floor's tilt. The
  floor must show at the bottom of the picture within 4 m: a phone at chest height held level or
  tilted down does; tilted up, the dashboard says "tilt the camera down to the floor".
- **Why it is held, not measured on every frame:** an edge closer than the bottom of the picture
  (standing at a kerb or the top of stairs) would be scaled up to the person's level and read as
  flat. With the held scale the reader still sees it: the floor in view is off the person's
  level, so there is an edge at 0 m. When a frame's own floor scale differs from the held one by
  more than 15 percent, the bottom of the picture is not where level floor would be: farther is
  a drop at 0 m (stop), nearer is unknown ground (slow).
- The model's scale changes between scenes (a corridor, then outdoors). When the frames keep
  differing for four steps, the person has walked past where an edge would be, and the scale is
  learned again.

Distances come from the depth only once a scale is held; otherwise the box-height estimate is
kept.

It is the same camera as the detector, so for the decision core it is the camera, not a second
sensor: stairs it sees close ahead stop, farther off they slow, and changing to tracks still needs
a second sensor. The model on the indoor data (the default) or
`--depth depth-anything/Depth-Anything-V2-Metric-Outdoor-Small-hf` outdoors; the scale from the
floor makes the choice matter less. How close the distances come to a tape measure is #133.

## Optional models

The server starts without any model and says which ones are missing.

| Model | Install | What it adds |
|---|---|---|
| Object detector (COCO) | `pip install ultralytics`, then `--weights yolo26n.pt` (or `yolo26s.pt`, slower and finds more) | the 80 COCO classes: person, chair, bottle, laptop and so on; obstacles get an **estimated** distance |
| Open-vocabulary detector | `python -m demo.build_detector` once, with internet, then `--weights yoloe-26n-mana.pt --imgsz 480` | YOLOE-26 told to look for the 42 objects listed in `demo/build_detector.py`, such as doors, stairs, poles, walking sticks and fire extinguishers. The nano model at 480 is about twice as fast on a laptop CPU as the small one (`--base yoloe-26s-seg.pt --out yoloe-26s-mana.pt`), which finds more small objects |
| Offline voice commands | `pip install vosk`, download a small English model, `--vosk-model <folder>` | the ten command words, offline, and the phrases: "mark this place", "where is the destination", "floor two" (or "second floor", "ground floor", "basement"), which sets the floor on the indoor screen. In a sentence only "stop" acts, so "where do I go" does not start the device; that holds for the browser's recogniser too. Vosk's numbers in `docs/results.md` (#185) were measured with a grammar of the ten words; for the phrases the demo's grammar has 48 entries, so its false activations may differ until it is measured again. A floor said is taken as true, whoever says it: the microphone cannot tell the user from a bystander, and the stairs or lift count in progress starts again from it |
| Stairs classifier | `pip install onnxruntime`, `--stairs-model <file.onnx>` | level or stairs from the camera. **A trained model is not included**: train one on StairNet with `tools/train_ground.py` (#38). |
| Sirens and horns | `pip install torch torchvision torchaudio`, a checkout of [EfficientAT](https://github.com/fschmid56/EfficientAT) with `resources/mn10_as_mAP_471.pt`, then `--sounds <checkout>`, with `--vosk-model` for voice commands | a siren or a horn in the last 10 s of the phone's microphone, said once and shown on the live page. The phone then streams its microphone at 32 kHz, and Android Chrome cannot run its own speech recognition beside that stream, so without `--vosk-model` voice commands are off (the server says so). On UrbanSound8K: sirens F1 0.86, horns F1 0.74 (docs/results.md, #47). The thresholds (0.22, 0.08) were set on clips of 4 s or less padded with silence; on a running 10 s window of street sound they are untested, so its false alarms may differ. The kept sound is forgotten when the phone disconnects |
| Depth from the camera | `pip install torch transformers`, then `--depth` | distances in metres from the camera image, and where stairs the detector sees are and which way they go (see above) |

## Known limits

- **Without a ground model the chair reports "Ground sensors not available, slowing down".**
  The decision layer needs ground reports (level, stairs, curb), and a plain phone does not
  provide them. `--depth` fills this gap (see above), and so will the stairs classifier once a
  model is trained.
- **The COCO detector has no stairs or curb class.** It finds obstacles only. A step is confirmed
  when two different sensors agree. The classifier and the detector both use the camera image, so
  they count as one sensor: on a phone without depth, stairs only slow the chair down.
- **Without `--depth`, distances from the camera are estimates**, made from the size of the box
  and a typical object height. Calibrate with `vision.calibrate_focal`.
- **Drops and step height need depth.** With `--depth` they come from the camera image (see
  above). Without it, they are verified by the scripted scenarios (`python -m mana_core.evaluate`,
  or `/api/evidence` on the server), which are an automated test and not live data.
- **Wi-Fi detection needs Android Chrome.** Other browsers cannot see the network type, and then
  the tile shows N/A and the alert link shows "unknown" rather than a guess.
- **The browser's speech recognition is an online service.** Chrome sends the audio to Google, so
  list it in the tool and API disclosure. The offline recogniser does not need the internet.

## Tests

```bash
python -m pytest tests          # the server tests need aiohttp and are skipped without it
```

**End to end, with no phone.** `tools/phone_e2e.py` opens the real phone page in headless Chrome
with a video file as its camera, and the tilt, compass and GPS set as a phone held 12 degrees down
by a person walking north. It prints what the dashboard shows every few seconds and keeps
screenshots:

```bash
python -m demo.server --depth --weights yoloe-26s-mana.pt --imgsz 480 --on-foot
python tools/phone_e2e.py --video stairs.mp4 --seconds 60 --out e2e
python tools/phone_e2e.py --video stairs.mp4 --offline     # the dashboard with no internet
```
