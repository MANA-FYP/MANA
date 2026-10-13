// MANA phone page: streams the phone's real sensors to the laptop and shows what comes back.
// Nothing is simulated here. A sensor the browser cannot read is shown as "Not available".

const PHONE_ORDER = ["camera", "motion", "gps", "microphone", "link", "depth", "lidar", "temperature"];
const grid = document.getElementById("grid");
const conn = document.getElementById("conn");

let ws = null;
let started = {};          // which sensors this page has started
let lastReason = "";
let tiles = PHONE_ORDER.map((id) => ({
  id, label: { camera: "Camera", motion: "Motion", gps: "GPS", microphone: "Microphone", link: "Link",
               depth: "Depth camera", lidar: "Lidar", temperature: "Temperature" }[id],
  state: "idle", reason: "", value: "",
}));

function render() {
  grid.innerHTML = tiles.map((s) => tileHTML(s, false)).join("");
}
render();

// ---- connection

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(scheme + "://" + location.host + "/ws/phone");
  ws.binaryType = "arraybuffer";
  ws.onopen = () => {
    conn.textContent = "connected";
    conn.className = "conn on";
    send({ type: "hello", caps: capabilities(), audio_rate: audioRate });
  };
  ws.onclose = () => {
    conn.textContent = "offline";
    conn.className = "conn off";
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

let audioRate = 48000;

function capabilities() {
  const md = !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia);
  const secure = window.isSecureContext;
  const needs = (ok, why) => ({ available: ok, why: ok ? "" : why });
  return {
    camera: needs(md && secure, secure ? "No camera access in this browser" : "Needs an HTTPS page"),
    motion: needs("DeviceMotionEvent" in window && secure, secure ? "No motion sensor access" : "Needs an HTTPS page"),
    gps: needs("geolocation" in navigator && secure, secure ? "No location access" : "Needs an HTTPS page"),
    microphone: needs(md && secure, secure ? "No microphone access" : "Needs an HTTPS page"),
    link: { available: true },
  };
}

// ---- tile states come from the server; the phone only adds "tap to enable" help

function onState(m) {
  const byId = Object.fromEntries(m.sensors.map((s) => [s.id, s]));
  tiles = tiles.map((t) => byId[t.id] || t);
  render();
  const d = m.decision;
  const box = document.getElementById("device");
  const kind = d.stop ? "stopped" : d.mode === "TRACKS" ? "tracks" : d.speed < 1 ? "slow" : "wheels";
  box.className = "device " + kind;
  document.getElementById("mode").textContent =
    d.stop ? "STOPPED" : d.mode + " · " + Math.round(d.speed * 100) + " %";
  document.getElementById("why").textContent = d.reason;
  if (d.reason !== lastReason) {
    lastReason = d.reason;
    if (d.priority !== "INFO" && "speechSynthesis" in window) {
      speechSynthesis.speak(new SpeechSynthesisUtterance(d.reason));
    }
    if (d.stop && navigator.vibrate) navigator.vibrate(200);
  }
}

// ---- link: a heartbeat every second, and the round trip it measured

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

// ---- motion

let lastMotion = 0;
async function startMotion() {
  if (started.motion) return;
  if (typeof DeviceMotionEvent !== "undefined" && typeof DeviceMotionEvent.requestPermission === "function") {
    const r = await DeviceMotionEvent.requestPermission();  // iPhone asks here
    if (r !== "granted") return;
  }
  started.motion = true;
  window.addEventListener("devicemotion", (e) => {
    const now = performance.now();
    const a = e.accelerationIncludingGravity;
    if (!a || a.x === null || now - lastMotion < 33) return;  // about 30 readings a second
    lastMotion = now;
    send({ type: "motion", ax: a.x, ay: a.y, az: a.z });
  });
}

// ---- location

function startGps() {
  if (started.gps || !("geolocation" in navigator)) return;
  started.gps = true;
  navigator.geolocation.watchPosition(
    (p) => send({ type: "gps", accuracy: p.coords.accuracy }),
    () => send({ type: "gps", accuracy: null }),
    { enableHighAccuracy: true, maximumAge: 1000, timeout: 10000 });
}

// ---- camera: a small JPEG about five times a second

async function startCamera() {
  if (started.camera) return;
  const stream = await navigator.mediaDevices.getUserMedia(
    { video: { facingMode: "environment", width: { ideal: 640 }, height: { ideal: 480 } } });
  started.camera = true;
  const video = document.createElement("video");
  video.playsInline = true;
  video.muted = true;
  video.srcObject = stream;
  await video.play();
  const canvas = document.createElement("canvas");
  setInterval(() => {
    if (!video.videoWidth) return;
    canvas.width = 480;
    canvas.height = Math.round(480 * video.videoHeight / video.videoWidth);
    canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);
    canvas.toBlob((b) => b && b.arrayBuffer().then((buf) => sendBinary(1, buf)), "image/jpeg", 0.6);
  }, 200);
}

// ---- microphone: raw audio for the laptop's recogniser, and the browser's own recogniser

async function startMicrophone() {
  if (started.microphone) return;
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  started.microphone = true;
  const ctx = new (window.AudioContext || window.webkitAudioContext)();
  audioRate = ctx.sampleRate;
  send({ type: "hello", caps: capabilities(), audio_rate: audioRate });
  const src = ctx.createMediaStreamSource(stream);
  const proc = ctx.createScriptProcessor(4096, 1, 1);
  proc.onaudioprocess = (e) => {
    const data = e.inputBuffer.getChannelData(0);
    sendBinary(2, new Float32Array(data).buffer);
  };
  src.connect(proc);
  proc.connect(ctx.destination);
  startBrowserRecognition();
}

function startBrowserRecognition() {
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) return;
  const rec = new SR();
  rec.continuous = true;
  rec.interimResults = false;
  rec.lang = "en-US";
  rec.onresult = (e) => {
    for (let i = e.resultIndex; i < e.results.length; i++) {
      const alt = e.results[i][0];
      alt.transcript.trim().toLowerCase().split(/\s+/).forEach((w) =>
        send({ type: "word", word: w, confidence: alt.confidence || 0.8 }));
    }
  };
  rec.onend = () => { try { rec.start(); } catch (_) {} };
  try { rec.start(); } catch (_) {}
}

// ---- start, tap to enable, buttons

const STARTERS = { camera: startCamera, motion: startMotion, gps: startGps, microphone: startMicrophone };

async function startAll() {
  for (const id of Object.keys(STARTERS)) {
    try { await STARTERS[id](); } catch (_) { /* a refused permission just leaves the tile idle */ }
  }
}

if (location.hash === "#preview") document.getElementById("start").classList.add("hidden");  // for design review

document.getElementById("startBtn").onclick = async () => {
  document.getElementById("start").classList.add("hidden");
  await startAll();
};
grid.addEventListener("click", async (e) => {
  const tile = e.target.closest(".tile");
  const id = tile && tile.dataset.id;
  if (id && STARTERS[id]) {
    try { await STARTERS[id](); } catch (_) {}
  }
});
document.getElementById("stopBtn").onclick = () => send({ type: "button", name: "stop" });
document.getElementById("goBtn").onclick = () => send({ type: "button", name: "go" });

connect();
