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
3. On the phone open **http://localhost:8000/phone** in Chrome.

`localhost` counts as a secure page, so the phone gives the camera, motion, location and
microphone with no certificate.

## The phone screen

Eight tiles in two columns, then Automatic and Manual, and the STOP and GO buttons. Tapping an
active sensor tile switches that sensor off and MANA answers at once in the decision box and by
voice (GPS off moves the position to the indoor map); tapping it again switches it on. A web page
cannot switch Wi-Fi or Bluetooth itself, so tapping those tiles declares them off for MANA. The phone is a sensor input only: the
decision and its spoken message are on the laptop dashboard (Chrome needs one click on the page
before it will speak; the Voice button turns it off). Text sizes follow the screen height, so
nothing scrolls.

| State | Meaning |
|---|---|
| **Active** | the phone is streaming it now, with its live value and a trust bar |
| **Tap** | the browser has it, but nothing is arriving yet (permission not given) |
| **N/A** | a web page cannot read it, with the reason |

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
| Motion | real tilt and vibration | level or not; shaking counts as rough ground, so it changes to tracks |
| GPS | real position accuracy | worse than 15 m for 3 s leaves GPS, better than 8 m for 5 s returns |
| Microphone | real audio and the browser's recogniser | the words stop, go, on, off, up, down become commands |
| Wi-Fi | the network type the browser reports (Android Chrome); switched off shows Not available | gone for 2 s means alerts would move to Bluetooth |
| Bluetooth | what the browser says about the Bluetooth adapter (asked every second). Chrome does not always follow the Settings switch, so tap the tile to declare it off | Wi-Fi gone and Bluetooth on means alerts use Bluetooth; both off means no link |
| Depth | **N/A**, browsers do not expose depth sensors | nothing |
| Lidar | **N/A**, browsers do not expose lidar | nothing |

The alert link is a **decision**: a web page cannot open a Bluetooth connection to the chair, so
the dashboard shows which radio the alerts *would* use. The data of the demo itself travels over the
USB cable.

## Optional models

The server starts without any model and says which ones are missing.

| Model | Install | What it adds |
|---|---|---|
| Object detector (COCO) | `pip install ultralytics`, then `--weights yolo26n.pt` | obstacles from the camera: person, car, bicycle and so on, with an **estimated** distance |
| Offline voice commands | `pip install vosk`, download a small English model, `--vosk-model <folder>` | the ten command words, offline |
| Stairs classifier | `pip install onnxruntime`, `--stairs-model <file.onnx>` | level or stairs from the camera. **A trained model is not included.** |

## Known limits

- **Without a ground model the chair reports "Ground sensors not available, slowing down".**
  The decision layer needs ground reports (level, stairs, curb), and a plain phone does not
  provide them. The stairs classifier fills this gap once a model is trained.
- **The COCO detector has no stairs or curb class.** It finds obstacles only. A step is confirmed
  when two different sensors agree. The classifier and the detector both use the camera image, so
  they count as one sensor: on a phone without depth, stairs only slow the chair down.
- **Distances from the camera are estimates**, made from the size of the box and a typical
  object height. Calibrate with `vision.calibrate_focal`.
- **Drops and step height need depth.** They are verified by the scripted scenarios
  (`python -m mana_core.evaluate`, or `/api/evidence` on the server), which are an automated test
  and not live data.
- **Wi-Fi detection needs Android Chrome.** Other browsers cannot see the network type, and then
  the tile shows N/A and the alert link shows "unknown" rather than a guess.
- **The browser's speech recognition is an online service.** Chrome sends the audio to Google, so
  list it in the tool and API disclosure. The offline recogniser does not need the internet.

## Tests

```bash
python -m pytest tests          # the server tests need aiohttp and are skipped without it
```
