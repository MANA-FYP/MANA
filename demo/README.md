# MANA phone demo

The decision layer driven by **real data from a phone**, shown on a laptop dashboard. No ROS 2 and
no Gazebo. Nothing in the demo is simulated: a sensor the phone's browser can read is used, and a
sensor it cannot read is shown as **Not available**.

```
phone browser  --WebSocket over Wi-Fi-->  laptop server  -->  laptop browser (dashboard)
 sensors, camera frames, audio             middleware +       inputs, middleware,
 sensor tiles, command  <--command--       decision layer     decision rules, camera
```

## Run it

```bash
pip install -r demo/requirements.txt
python -m demo.server                         # dashboard at http://localhost:8000/dashboard
```

**The phone page needs HTTPS.** Phone browsers only give the camera, microphone, motion and
location to secure pages. Make a certificate once (Git for Windows includes `openssl`), then start
the server with it:

```bash
openssl req -x509 -newkey rsa:2048 -nodes -keyout key.pem -out cert.pem -days 30 \
  -subj "/CN=mana-demo" -addext "subjectAltName=IP:<the laptop's address>"
python -m demo.server --port 8443 --cert cert.pem --key key.pem
```

1. Put the phone and the laptop on the same Wi-Fi (a phone hotspot is the safest).
2. Open the **phone** address the server prints, for example `https://192.168.0.12:8443/phone`.
   Accept the certificate warning once.
3. Tap **Start sensors** and allow motion, location, camera and microphone.
4. Open `https://localhost:8443/dashboard` on the laptop.

`*.pem` files are ignored by git. Never commit a private key.

## The phone screen

Eight tiles in two columns and four rows, one per sensor.

| State | Meaning |
|---|---|
| **Active** | the phone is streaming it now, with its live value |
| **Tap to enable** | the browser has it, but nothing is arriving yet (permission not given) |
| **Not available** | a web page cannot read it, with the reason |

The strip at the bottom shows the command from the decision layer and has STOP and GO buttons.

## What is real

| Sensor | In the demo | What the decision layer does with it |
|---|---|---|
| Camera | real frames and light level | light level sets the trust of camera reports; frames go to the detector |
| Motion | real tilt and vibration | level or not; shaking counts as rough ground, so it changes to tracks |
| GPS | real position accuracy | worse than 15 m for 3 s leaves GPS, better than 8 m for 5 s returns |
| Microphone | real audio and the browser's recogniser | the words stop, go, on, off, up, down become commands |
| Link | real heartbeat delay | two missed heartbeats leave Wi-Fi; stale data slows the chair down |
| Depth camera | **not available** | browsers do not expose depth sensors |
| Lidar | **not available** | browsers do not expose lidar |
| Temperature | **not available** | browsers do not expose a temperature sensor |

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
  when two different sources agree, so stairs need the classifier and a second source.
- **Distances from the camera are estimates**, made from the size of the box and a typical
  object height. Calibrate with `vision.calibrate_focal`.
- **Drops and step height need depth.** They are verified by the scripted scenarios on the
  **Test evidence** card, which is labelled as an automated test and not live data.
- **The fallback link is a decision only.** A web page cannot use Bluetooth, so the demo shows
  that the rule would switch.
- **The browser's speech recognition is an online service.** Chrome sends the audio to Google, so
  list it in the tool and API disclosure. The offline recogniser does not need the internet.

## Tests

```bash
python -m pytest tests          # the server tests need aiohttp and are skipped without it
```
