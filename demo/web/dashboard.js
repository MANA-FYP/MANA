// MANA dashboard: one screen. Everything updates in place, so a new camera frame or a new
// message only changes the part of the page it belongs to.

const $ = (id) => document.getElementById(id);
let ws = null;
let lastFrame = 0;
let loadingFrame = false;
let boxes = [];
let frameSize = { w: 0, h: 0 };
let chipsKey = "";
let streamKey = "";
let rulesKey = "";

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

const LINK_TEXT = {
  wifi: ["Alerts: Wi-Fi", ""],
  bluetooth: ["Alerts: Bluetooth (decision)", "warn"],
  none: ["Alerts: no link", "bad"],
  unknown: ["Alerts: link unknown", "off"],
};

function render(s) {
  // header chips: rebuilt only when their text changes
  const active = s.sensors.filter((x) => x.state === "active").length;
  const link = LINK_TEXT[s.link.state] || LINK_TEXT.unknown;
  const pos = s.position.source === "gps" ? chip("Position: GPS") : chip("Position: indoor map", "warn");
  const m = s.models;
  const chips =
    chip(active + " of " + s.sensors.length + " sensors active", active ? "" : "off") +
    chip(link[0], link[1]) + pos +
    chip("Detector " + (m.detector ? "on" : "off"), m.detector ? "" : "off") +
    chip("Voice model " + (m.speech ? "on" : "off"), m.speech ? "" : "off") +
    chip("Stairs model " + (m.classifier ? "on" : "off"), m.classifier ? "" : "off");
  if (chips !== chipsKey) { chipsKey = chips; $("chips").innerHTML = chips; }

  updateTiles($("inputs"), s.sensors);

  // middleware messages: newest first, replaced only when something new arrived
  const rows = s.messages.slice().reverse().slice(0, 7);
  const key = JSON.stringify(rows);
  if (key !== streamKey) {
    streamKey = key;
    $("stream").innerHTML = rows.map((r) =>
      "<tr><td>" + r.t.toFixed(1) + '</td><td class="src">' + r.source + "</td><td>" + r.value +
      "</td><td>" + Math.round(r.trust * 100) + " %</td></tr>").join("") ||
      '<tr><td colspan="4" style="color:#64748b">No messages yet. Tap start on the phone.</td></tr>';
  }

  // rules: built once, then only the lit one changes
  const d = s.decision;
  if (rulesKey !== s.rules.join("|")) {
    rulesKey = s.rules.join("|");
    $("rules").innerHTML = s.rules.map((r) => "<li>" + r + "</li>").join("");
  }
  [...$("rules").children].forEach((li, i) => {
    const fired = s.rules[i] === d.fired;
    if (li.classList.contains("fired") !== fired) li.classList.toggle("fired", fired);
  });

  const kind = d.stop ? "stopped" : d.mode === "TRACKS" ? "tracks" : d.speed < 1 ? "slow" : "wheels";
  const cls = "result " + kind;
  if ($("result").className !== cls) $("result").className = cls;
  setText($("mode"), d.stop ? "STOPPED" : d.mode);
  setText($("meta"), "speed " + Math.round(d.speed * 100) + " % · " + (s.manual ? "manual" : "automatic") +
    " · " + d.priority.toLowerCase());
  setText($("why"), d.reason);

  // position and link
  const radio = (v) => (v === null ? "unknown" : v ? "on" : "off");
  const kv = [
    ["Position source", s.position.source === "gps" ? "GPS" : "Indoor map"],
    ["GPS accuracy", s.position.accuracy_m === null ? "no fix" : "±" + Math.round(s.position.accuracy_m) + " m"],
    ["Wi-Fi", radio(s.link.wifi)],
    ["Bluetooth", radio(s.link.bluetooth)],
    ["Alerts would use", link[0].replace("Alerts: ", "")],
    ["Heartbeats missed", String(s.link.missed)],
  ];
  const html = kv.map((p) => "<dt>" + p[0] + "</dt><dd>" + p[1] + "</dd>").join("");
  if ($("kv").dataset.k !== html) { $("kv").dataset.k = html; $("kv").innerHTML = html; }

  // timing facts
  const lat = s.latency;
  const facts = [
    [s.link.rtt_ms === null ? "-" : Math.round(s.link.rtt_ms) + " ms", "phone to laptop"],
    [lat.detector_ms === undefined ? "-" : Math.round(lat.detector_ms) + " ms", "detector, one frame"],
    [lat.voice_or_button_to_stop_ms === undefined ? "-" : Math.round(lat.voice_or_button_to_stop_ms) + " ms",
     "stop to decision"],
    [Math.round(s.link.trust * 100) + " %", "data link trust"],
    [String(s.messages.length), "messages in the log"],
    [s.time.toFixed(0) + " s", "running"],
  ];
  const fhtml = facts.map((f) => '<div class="fact"><b>' + f[0] + "</b><span>" + f[1] + "</span></div>").join("");
  if ($("facts").dataset.k !== fhtml) { $("facts").dataset.k = fhtml; $("facts").innerHTML = fhtml; }

  if (!m.detector) setText($("cameraHint"), "The detector is not installed, so no boxes are drawn.");
  if (s.frame.seq > lastFrame) {
    boxes = s.frame.boxes;
    frameSize = { w: s.frame.w, h: s.frame.h };
    loadFrame(s.frame.seq);
  }
}

// The camera is drawn on one canvas that is never replaced, so only the camera area repaints.
async function loadFrame(seq) {
  if (loadingFrame) return;
  loadingFrame = true;
  try {
    const r = await fetch("/frame.jpg?seq=" + seq, { cache: "no-store" });
    if (r.status !== 200) return;
    const bmp = await createImageBitmap(await r.blob());
    const cv = $("cam");
    if (cv.width !== bmp.width || cv.height !== bmp.height) { cv.width = bmp.width; cv.height = bmp.height; }
    const ctx = cv.getContext("2d");
    ctx.drawImage(bmp, 0, 0);
    const sx = bmp.width / (frameSize.w || bmp.width);
    const sy = bmp.height / (frameSize.h || bmp.height);
    ctx.lineWidth = Math.max(2, bmp.width / 200);
    ctx.font = Math.max(12, Math.round(bmp.width / 38)) + "px Segoe UI, Arial";
    boxes.forEach((b) => {
      ctx.strokeStyle = "#10b981";
      ctx.strokeRect(b.x1 * sx, b.y1 * sy, (b.x2 - b.x1) * sx, (b.y2 - b.y1) * sy);
      const label = b.label + " " + Math.round(b.confidence * 100) + " %";
      const h = Math.round(bmp.width / 30);
      ctx.fillStyle = "#065f46";
      ctx.fillRect(b.x1 * sx, b.y1 * sy - h, ctx.measureText(label).width + 12, h);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, b.x1 * sx + 6, b.y1 * sy - 5);
    });
    $("camEmpty").style.display = "none";
    lastFrame = seq;
  } finally {
    loadingFrame = false;
  }
}

document.querySelectorAll("[data-button]").forEach((b) => {
  b.onclick = () => ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: "button", name: b.dataset.button }));
});

$("openEvidence").onclick = () => $("modal").classList.add("open");
$("closeEvidence").onclick = () => $("modal").classList.remove("open");
$("modal").onclick = (e) => { if (e.target === $("modal")) $("modal").classList.remove("open"); };

$("runEvidence").onclick = async () => {
  $("evidence").innerHTML = '<div class="hint">Running…</div>';
  const r = await (await fetch("/api/evidence")).json();
  $("evidence").innerHTML = '<span class="chip warn">' + r.label + "</span><table>" +
    r.scenarios.map((sc) =>
      '<tr><td colspan="3"><b>' + sc.title + '</b> · <span style="color:#64748b">' + sc.shows + "</span></td></tr>" +
      sc.marks.map((m) => '<tr><td class="' + (m.ok ? "pass" : "fail") + '">' + (m.ok ? "PASS" : "FAIL") +
        "</td><td>" + m.text + '</td><td style="color:#64748b">' + m.measured + "</td></tr>").join("")).join("") +
    "</table>";
};

connect();
