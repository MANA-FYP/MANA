// MANA dashboard: shows the phone's sensors, the middleware messages and the decision layer.

const $ = (id) => document.getElementById(id);
let ws = null;
let lastFrame = -1;
let boxes = [];
let frameSize = { w: 0, h: 0 };

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(scheme + "://" + location.host + "/ws/dashboard");
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === "snapshot") render(m);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

function chip(text, kind) {
  return '<span class="chip ' + (kind || "") + '">' + text + "</span>";
}

function render(s) {
  const active = s.sensors.filter((x) => x.state === "active").length;
  const link = s.link.state === "wifi" ? chip("Link: Wi-Fi") : chip("Link: fallback (decision only)", "warn");
  const pos = s.position.source === "gps" ? chip("Position: GPS") : chip("Position: indoor map", "warn");
  const models = s.models;
  $("chips").innerHTML =
    chip(active + " of 8 sensors active", active ? "" : "off") + link + pos +
    chip("Detector: " + (models.detector ? "on" : "not installed"), models.detector ? "" : "off") +
    chip("Voice model: " + (models.speech ? "on" : "not installed"), models.speech ? "" : "off") +
    chip("Stairs model: " + (models.classifier ? "on" : "not installed"), models.classifier ? "" : "off");

  $("inputs").innerHTML = s.sensors.map((x) => tileHTML(x, true)).join("");

  $("stream").innerHTML = s.messages.slice().reverse().slice(0, 9).map((m) =>
    "<tr><td>" + m.t.toFixed(1) + '</td><td class="src">' + m.source + "</td><td>" + m.value +
    "</td><td>" + Math.round(m.trust * 100) + " %</td></tr>").join("") ||
    '<tr><td colspan="4" style="color:#64748b">No messages yet. Start the sensors on the phone.</td></tr>';

  $("rules").innerHTML = s.rules.map((r) =>
    '<li class="' + (r === s.decision.fired ? "fired" : "") + '">' + r + "</li>").join("");

  const d = s.decision;
  const kind = d.stop ? "stopped" : d.mode === "TRACKS" ? "tracks" : d.speed < 1 ? "slow" : "wheels";
  $("result").className = "result " + kind;
  $("mode").textContent = d.stop ? "STOPPED" : d.mode;
  $("meta").textContent = "speed " + Math.round(d.speed * 100) + " % · " + (s.manual ? "manual" : "automatic") +
    " · " + d.priority.toLowerCase();
  $("why").textContent = d.reason;

  const lat = s.latency;
  const facts = [
    [s.link.rtt_ms === null ? "-" : Math.round(s.link.rtt_ms) + " ms", "phone to laptop round trip"],
    [lat.detector_ms === undefined ? "-" : Math.round(lat.detector_ms) + " ms", "detector, one frame"],
    [lat.voice_or_button_to_stop_ms === undefined ? "-" : Math.round(lat.voice_or_button_to_stop_ms) + " ms",
     "stop command to decision"],
    [s.position.accuracy_m === null ? "no fix" : "±" + Math.round(s.position.accuracy_m) + " m", "GPS accuracy"],
    [String(s.link.missed), "heartbeats missed"],
    [Math.round(s.link.trust * 100) + " %", "link trust"],
  ];
  $("facts").innerHTML = facts.map((f) => '<div class="fact"><b>' + f[0] + "</b><span>" + f[1] + "</span></div>").join("");

  if (s.frame.seq !== lastFrame && s.frame.seq > 0) {
    lastFrame = s.frame.seq;
    boxes = s.frame.boxes;
    frameSize = { w: s.frame.w, h: s.frame.h };
    showFrame();
  }
  if (!models.detector) {
    $("cameraHint").textContent = "The detector is not installed on the laptop, so no boxes are drawn.";
  }
}

function showFrame() {
  const cam = $("camera");
  cam.innerHTML = '<img id="img" alt="camera frame"><canvas id="over"></canvas>';
  const img = $("img");
  img.onload = drawBoxes;
  img.src = "/frame.jpg?seq=" + lastFrame;
}

function drawBoxes() {
  const img = $("img");
  const cv = $("over");
  cv.width = img.clientWidth;
  cv.height = img.clientHeight;
  const ctx = cv.getContext("2d");
  const sx = cv.width / (frameSize.w || img.naturalWidth);
  const sy = cv.height / (frameSize.h || img.naturalHeight);
  ctx.lineWidth = 3;
  ctx.font = "14px Segoe UI, Arial";
  boxes.forEach((b) => {
    ctx.strokeStyle = "#10b981";
    ctx.strokeRect(b.x1 * sx, b.y1 * sy, (b.x2 - b.x1) * sx, (b.y2 - b.y1) * sy);
    const label = b.label + " " + Math.round(b.confidence * 100) + " %";
    ctx.fillStyle = "#065f46";
    ctx.fillRect(b.x1 * sx, b.y1 * sy - 20, ctx.measureText(label).width + 12, 20);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, b.x1 * sx + 6, b.y1 * sy - 5);
  });
}

document.querySelectorAll("[data-button]").forEach((b) => {
  b.onclick = () => ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: "button", name: b.dataset.button }));
});

$("runEvidence").onclick = async () => {
  $("evidence").innerHTML = '<div class="hint">Running…</div>';
  const r = await (await fetch("/api/evidence")).json();
  $("evidence").innerHTML = '<span class="chip warn label-test">' + r.label + "</span>" +
    "<table>" + r.scenarios.map((sc) =>
      "<tr><td colspan=\"3\"><b>" + sc.title + "</b> · <span style=\"color:#64748b\">" + sc.shows + "</span></td></tr>" +
      sc.marks.map((m) => "<tr><td class=\"" + (m.ok ? "pass" : "fail") + "\">" + (m.ok ? "PASS" : "FAIL") +
        "</td><td>" + m.text + "</td><td style=\"color:#64748b\">" + m.measured + "</td></tr>").join("")).join("") +
    "</table>";
};

connect();
