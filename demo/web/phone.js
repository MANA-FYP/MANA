// MANA phone page: a sensor input only. It streams the phone's real sensors to the laptop; the
// decision and its voice output are on the laptop.
// Nothing is simulated here. A sensor the browser cannot read is shown as "N/A" with the reason.

const grid = document.getElementById("grid");
const conn = document.getElementById("conn");

let ws = null;
let bluetoothOn = null;    // true / false, or null when the browser cannot tell
const declaredOff = {};    // radio tiles the user switched off on the phone (a page cannot switch a radio)
let audioRate = 48000;
let laptopHasVoiceModel = false;   // when the laptop recognises speech itself, the phone streams audio

// The data path is the USB cable (adb reverse), so the page is opened as localhost.
document.getElementById("path").textContent = "data path: USB cable";

// ---- connection

function connect() {
  ws = new WebSocket("ws://" + location.host + "/ws/phone");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    conn.textContent = "connected";
    conn.className = "chip";
    send({ type: "hello", caps: capabilities(), audio_rate: audioRate });
  };
  ws.onclose = () => {
    conn.textContent = "offline";
    conn.className = "chip off";
    setTimeout(connect, 1500);
  };
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "pong") onPong(m.t);
    if (m.type === "state") onState(m);
  };
}

function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}

function sendBinary(tag, payload) {
  if (!ws || ws.readyState !== 1 || ws.bufferedAmount > 400000) return;  // never queue up old data
  const out = new Uint8Array(1 + payload.byteLength);
  out[0] = tag;
  out.set(new Uint8Array(payload), 1);
  ws.send(out);
}

// ---- what this browser has

function capabilities() {
  const md = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const secure = window.isSecureContext;
  const needs = (ok, why) => ({ available: ok, why: ok ? "" : why });
  const https = "Needs a secure page";
  return {
    camera: needs(md && secure, secure ? "No camera access" : https),
    motion: needs("DeviceMotionEvent" in window && secure, secure ? "No motion access" : https),
    gps: needs("geolocation" in navigator && secure, secure ? "No location access" : https),
    microphone: needs(md && secure, secure ? "No microphone access" : https),
    wifi: needs(!!(navigator.connection && "type" in navigator.connection), "No network info"),
    bluetooth: needs("bluetooth" in navigator, "No Bluetooth access"),
  };
}

// ---- tiles come from the server; they are updated in place, so only a changed tile changes

function onState(m) {
  updateTiles(grid, m.sensors);
  showSaid(m.heard);
  laptopHasVoiceModel = !!(m.models && m.models.speech);
  // the mode buttons show which one is on
  document.querySelectorAll("[data-mode]").forEach((b) => {
    const on = (b.dataset.mode === "manual") === !!m.manual;
    b.classList.toggle("on", on);
  });
}

// ---- the message strip: the same message the laptop says, one at a time, the most urgent first

const say = document.getElementById("say");
let shown = 0;
let sayTimer = null;
function showSaid(heard) {
  if (!heard || !heard.length) return;
  const last = heard[heard.length - 1];
  if (last.n === shown) return;   // the same message: keep its timer
  shown = last.n;
  say.textContent = last.text;
  say.className = "say " + last.priority.toLowerCase();
  clearTimeout(sayTimer);
  sayTimer = setTimeout(() => {   // an old message fades, it is not kept for ever
    say.textContent = "Waiting for MANA";
    say.className = "say";
  }, 8000);
}

// ---- heartbeat: the round trip of the data path

const sentAt = {};
function heartbeat() {
  const t = Math.round(performance.now());
  sentAt[t] = true;
  send({ type: "ping", t });
}
function onPong(t) {
  if (!(t in sentAt)) return;
  delete sentAt[t];
  send({ type: "rtt", ms: Math.round(performance.now()) - t });
}
setInterval(heartbeat, 1000);

// ---- radios: what the browser says about Wi-Fi and Bluetooth, once a second

// Asked again every second: the change event is not reliable in every Chrome version.
async function readBluetooth() {
  if (!("bluetooth" in navigator) || !navigator.bluetooth.getAvailability) return;
  try { bluetoothOn = await navigator.bluetooth.getAvailability(); } catch (_) { bluetoothOn = null; }
}

async function reportRadios() {
  await readBluetooth();
  const c = navigator.connection;
  let wifi = c && typeof c.type === "string" ? c.type === "wifi" : null;
  let bluetooth = bluetoothOn;
  if (declaredOff.wifi) wifi = false;           // the user switched it off with a tap
  if (declaredOff.bluetooth) bluetooth = false;
  send({ type: "radio", wifi, bluetooth });
}
setInterval(reportRadios, 1000);

// ---- sensors: a tap on a tile switches that sensor on, another tap switches it off.
// Each start function returns a function that stops it again.

let lastMotion = 0;
async function startMotion() {
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    const r = await DeviceMotionEvent.requestPermission();  // iPhone asks here
    if (r !== "granted") return null;
  }
  const onMotion = (e) => {
    const now = performance.now();
    const a = e.accelerationIncludingGravity;
    if (!a || a.x === null || now - lastMotion < 33) return;  // about 30 readings a second
    lastMotion = now;
    send({ type: "motion", ax: a.x, ay: a.y, az: a.z });
  };
  window.addEventListener("devicemotion", onMotion);
  return () => window.removeEventListener("devicemotion", onMotion);
}

function startGps() {
  if (!("geolocation" in navigator)) return null;
  const id = navigator.geolocation.watchPosition(
    (p) => send({ type: "gps", accuracy: p.coords.accuracy }),
    () => send({ type: "gps", accuracy: null }),
    { enableHighAccuracy: true, maximumAge: 1000, timeout: 10000 });
  return () => navigator.geolocation.clearWatch(id);
}

// a small JPEG about five times a second
async function startCamera() {
  const stream = await navigator.mediaDevices.getUserMedia(
    { video: { facingMode: "environment", width: { ideal: 640 }, height: { ideal: 480 } } });
  const video = document.createElement("video");
  video.playsInline = true;
  video.muted = true;
  video.srcObject = stream;
  await video.play();
  const canvas = document.createElement("canvas");
  const timer = setInterval(() => {
    if (!video.videoWidth) return;
    canvas.width = 480;
    canvas.height = Math.round(480 * video.videoHeight / video.videoWidth);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((b) => b && b.arrayBuffer().then((buf) => sendBinary(1, buf)), "image/jpeg", 0.6);
  }, 200);
  return () => { clearInterval(timer); stream.getTracks().forEach((t) => t.stop()); };
}

// raw audio for the laptop's recogniser, and the browser's own recogniser
async function startMicrophone() {
  // Android Chrome cannot run its own recogniser and a second audio capture at the same time, so
  // one of them is used: the laptop's model when it has one, otherwise the browser's recogniser.
  if (!laptopHasVoiceModel) return startBrowserRecognition();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  audioRate = ctx.sampleRate;
  send({ type: "hello", caps: capabilities(), audio_rate: audioRate });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e) => {
    sendBinary(2, new Float32Array(e.inputBuffer.getChannelData(0)).buffer);
  };
  src.connect(proc);
  proc.connect(ctx.destination);
  const beat = setInterval(() => send({ type: "mic", state: "listening" }), 1000);
  return () => {
    clearInterval(beat);
    proc.onaudioprocess = null;
    proc.disconnect();
    src.disconnect();
    stream.getTracks().forEach((t) => t.stop());
    ctx.close();
  };
}

function startBrowserRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) {
    send({ type: "mic", state: "unsupported" });
    return null;
  }
  const rec = new SR();
  let on = true;
  let fatal = false;
  const beat = setInterval(() => { if (!fatal) send({ type: "mic", state: "listening" }); }, 1000);
  rec.continuous = true;
  rec.interimResults = true;   // the laptop shows the words while they are being spoken
  rec.lang = "en-US";
  rec.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const alt = e.results[i][0];
      if (!e.results[i].isFinal) {
        send({ type: "heard", text: alt.transcript, final: false });
        continue;
      }
      send({ type: "heard", text: alt.transcript, final: true });
      alt.transcript.trim().toLowerCase().split(/\s+/).forEach((w) =>
        send({ type: "word", word: w, confidence: alt.confidence || 0.8 }));
    }
  };
  rec.onerror = (e) => {
    // "no-speech" and "aborted" are normal pauses; anything else is a reason to show on the tile
    if (e.error === "no-speech" || e.error === "aborted") return;
    fatal = e.error === "not-allowed" || e.error === "service-not-allowed";
    send({ type: "mic", state: e.error });
  };
  rec.onend = () => { if (on && !fatal) setTimeout(() => { try { rec.start(); } catch (_) {} }, 300); };
  try { rec.start(); } catch (_) {}
  return () => { on = false; clearInterval(beat); try { rec.stop(); } catch (_) {} };
}

// ---- start, tap a tile to switch a sensor on or off, buttons

const STARTERS = { camera: startCamera, motion: startMotion, gps: startGps, microphone: startMicrophone };
const stoppers = {};   // sensor id -> function that stops it, while it is running

async function switchOn(id) {
  if (stoppers[id] || !STARTERS[id]) return;
  const stop = await STARTERS[id]();
  if (stop) {
    stoppers[id] = stop;
    send({ type: "sensor", id, on: true });
  }
}

function switchOff(id) {
  const stop = stoppers[id];
  if (!stop) return;
  delete stoppers[id];
  stop();
  send({ type: "sensor", id, on: false });   // so the decision layer answers at once
}

async function startAll() {
  for (const id of Object.keys(STARTERS)) {
    try { await switchOn(id); } catch (_) { /* a refused permission just leaves the tile idle */ }
  }
}

if (location.hash === "#preview") document.getElementById("start").classList.add("hidden");  // design review

document.getElementById("startBtn").onclick = async () => {
  document.getElementById("start").classList.add("hidden");
  await startAll();
};
grid.addEventListener("click", async (e) => {
  const tile = e.target.closest(".tile");
  const id = tile && tile.dataset.id;
  if (!id) return;
  if (id === "wifi" || id === "bluetooth") {   // radios: a tap declares it off, another tap clears it
    if (tile.classList.contains("unavailable") && !declaredOff[id]) return;
    declaredOff[id] = !declaredOff[id];
    send({ type: "sensor", id, on: !declaredOff[id] });
    reportRadios();
    return;
  }
  if (!STARTERS[id]) return;
  if (tile.classList.contains("unavailable") && id !== "microphone") return;   // the mic can be retried
  try {
    if (stoppers[id]) switchOff(id);
    else await switchOn(id);
  } catch (_) {}
});

document.querySelectorAll("[data-button]").forEach((b) => {
  b.onclick = () => send({ type: "button", name: b.dataset.button });
});

document.addEventListener("contextmenu", (e) => e.preventDefault());

connect();
