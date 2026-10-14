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
let lastDecision = null;
let linesKey = "";
let inputsKey = "";
let voiceOn = true;   // the spoken message comes from the laptop, not the phone

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

function speak(text) {
  if (!voiceOn || !("speechSynthesis" in window)) return;
  speechSynthesis.speak(new SpeechSynthesisUtterance(text));   // queued: one never cuts off another
}

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
  const rows = s.messages.slice().reverse().slice(0, 4);
  const key = JSON.stringify(rows);
  if (key !== streamKey) {
    streamKey = key;
    $("stream").innerHTML = rows.map((r) =>
      "<tr><td>" + r.t.toFixed(1) + '</td><td class="src">' + r.source + "</td><td>" + r.value +
      "</td><td>" + Math.round(r.trust * 100) + " %</td></tr>").join("") ||
      '<tr><td colspan="4" style="color:#64748b">No messages yet. Tap start on the phone.</td></tr>';
  }

  const d = s.decision;
  const kind = d.stop ? "stopped" : d.mode === "TRACKS" ? "tracks" : d.speed < 1 ? "slow" : "wheels";
  const cls = "result " + kind;
  if ($("result").className !== cls) $("result").className = cls;
  // the action box shows only the last action MANA took, the same one the voice says
  const log = s.decisions;
  const last = log.length ? log[log.length - 1] : null;
  const shown = last ? last.text : d.sentence;
  if (shown !== linesKey) {
    linesKey = shown;
    $("mode").innerHTML = "<p class=\"main\">" + shown + "</p>";
  }

  // the voice says each new action, in order; a new one waits for the one still being spoken
  const newest = last ? last.n : 0;
  if (lastDecision === null) {
    lastDecision = newest;   // what happened before the page opened is not spoken
  } else if (newest > lastDecision) {
    const fresh = log.filter((e) => e.n > lastDecision);
    lastDecision = newest;
    fresh.forEach((e) => speak(e.text));
  }

  // box 4: only what the user says into the microphone
  const mic = s.sensors.find((x) => x.id === "microphone");
  const heard = s.inputs.slice().reverse().slice(0, 3);
  const ikey = JSON.stringify([heard, s.speaking, mic && mic.state]);
  if (ikey !== inputsKey) {
    inputsKey = ikey;
    const none = mic && mic.state === "active" ? "Listening. Say stop, go, manual or automatic."
      : mic && mic.state === "unavailable" ? "Microphone: " + (mic.reason || "not available").toLowerCase() + "."
      : "The microphone is not on. Tap the Microphone tile on the phone.";
    const live = s.speaking ? '<li class="live"><span class="when">now</span><span class="said">&ldquo;' +
      s.speaking + "&rdquo;</span></li>" : "";
    $("userInputs").innerHTML = live + heard.map((i) =>
      '<li><span class="when">' + i.t.toFixed(1) + '</span><span class="said">&ldquo;' + i.text + "&rdquo;</span>" +
      (i.command ? '<span class="via">' + i.command + "</span>" : "") + "</li>").join("") ||
      (live ? "" : '<li class="none">' + none + "</li>");
  }

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

$("voice").onclick = () => {
  voiceOn = !voiceOn;
  $("voice").textContent = "Voice: " + (voiceOn ? "on" : "off");
  if (!voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();
};

connect();
