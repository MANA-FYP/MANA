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
let lastHeard = null;
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
  // detections are shown, not spoken, so the box holds the last action instead
  const actions = log.filter((e) => e.kind !== "detection");
  const last = actions.length ? actions[actions.length - 1] : null;
  // while the user's stop holds, the box keeps saying so; newer lines still go to the log
  const shown = d.stop && d.fired === "user stop" ? d.sentence : last ? last.text : d.sentence;
  // the four lines before it stay visible underneath, newest first
  const earlier = log.filter((e) => e !== last).slice(-4).reverse().map((e) =>
    '<li><span class="when">' + e.t.toFixed(1) + "</span>" + e.text + "</li>").join("");
  const html = '<p class="main">' + shown + "</p>" + (earlier ? '<ul class="earlier">' + earlier + "</ul>" : "");
  if (html !== linesKey) {
    linesKey = html;
    $("mode").innerHTML = html;
  }

  // the voice says what the message queue lets out: one at a time, the most urgent first
  const said = s.heard || [];
  const newest = said.length ? said[said.length - 1].n : 0;
  if (lastHeard === null) {
    lastHeard = newest;   // what happened before the page opened is not spoken
  } else if (newest > lastHeard) {
    const fresh = said.filter((e) => e.n > lastHeard);
    lastHeard = newest;
    fresh.forEach((e) => {
      if (e.priority === "URGENT" && voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();   // a stop is not made to wait
      if (e.speak) speak(e.text);
    });
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
  // a frozen frame must not look live: dim it and say so when the camera stops sending
  const cam = s.sensors.find((x) => x.id === "camera");
  const camLive = !!cam && cam.state === "active";
  if (!camLive && lastFrame) setText($("camEmpty"), "No live camera: the camera is off or the phone is disconnected.");
  $("camEmpty").style.display = camLive && lastFrame ? "none" : "";
  $("cam").style.opacity = camLive ? "" : "0.25";
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
