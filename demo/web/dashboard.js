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
    chip("Stairs model " + (m.classifier ? "on" : "off"), m.classifier ? "" : "off") +
    chip("Depth model " + (m.depth ? "on" : "off"), m.depth ? "" : "off");
  if (chips !== chipsKey) { chipsKey = chips; $("chips").innerHTML = chips; }

  updateTiles($("inputs"), s.sensors);
  drawTrack(s.position.track);
  onFoot = s.on_foot;
  setText($("carried"), onFoot ? "Carried on foot" : "On the device");
  showDepth(s.depth);

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

// Depth from the camera image: a small picture over the camera, and what was read from it.
const GROUND_TEXT = { flat: "level floor", ramp: "ramp", stairs_up: "steps up", stairs_down: "steps down",
  curb: "curb", drop: "drop" };
let depthSeq = 0;
function showDepth(d) {
  $("depthText").hidden = !d;
  if (!d) return;
  if (d.seq > depthSeq) { depthSeq = d.seq; $("depthImg").src = "/depth.jpg?seq=" + d.seq; $("depthImg").hidden = false; }
  let text;
  if (!d.ground) text = "Depth: no ground reading (" + d.why + ").";
  else {
    text = "Depth: " + GROUND_TEXT[d.ground] + (d.distance_m === null ? "" : " at " + d.distance_m.toFixed(1) + " m") +
      (d.step_m === null ? "" : ", " + Math.round(d.step_m * 100) + " cm") +
      (d.sent ? "." : " (shown only).");
    text += d.ahead_m === null ? " Path clear." : d.ahead_is_stairs
      ? " The stairs rise at " + d.ahead_m.toFixed(1) + " m (stairs, not an obstacle)."
      : " Something in the path at " + d.ahead_m.toFixed(1) + " m.";
  }
  setText($("depthText"), text + " Camera tilted " + (d.pitch_deg === null ? "?" : d.pitch_deg) + "°, " + Math.round(d.ms) + " ms a frame.");
}

// The path walked, north up, in metres from the start, scaled to fit, with a scale bar.
const SOURCE_COLOUR = { gps: "#2563eb", steps: "#d97706", none: "#94a3b8" };
const SOURCE_TEXT = { gps: "GPS", steps: "steps (no GPS)", none: "waiting for steps or GPS" };
let trackKey = "";
let lastTrack = null;

// With internet, the street map under the path: OpenStreetMap tiles, placed from where the track's
// x = y = 0 is (its origin, from the first GPS fix). Offline the tiles fail and the grid stays.
// Off until asked for: each tile request tells openstreetmap.org roughly where the person is.
const TILE = "https://tile.openstreetmap.org/";
const tiles = new Map();   // "z/x/y" -> its Image
function tile(key) {
  if (!tiles.has(key)) {
    const img = new Image();
    img.onload = () => { trackKey = ""; drawTrack(lastTrack); };
    // offline: try that tile again in 30 s, so the map comes back with the internet
    img.onerror = () => setTimeout(() => { tiles.delete(key); trackKey = ""; drawTrack(lastTrack); }, 30000);
    img.src = TILE + key + ".png";
    tiles.set(key, img);
  }
  const img = tiles.get(key);
  return img.complete && img.naturalWidth ? img : null;
}
function drawStreets(ctx, t, w, h, ox, oy, k) {
  if (!t.origin || !$("streets").checked) return false;
  const lat = t.origin[0] * Math.PI / 180, cos = Math.cos(lat);
  // the zoom whose tile pixel is no smaller than a canvas pixel, so tiles are only enlarged
  const z = Math.max(0, Math.min(19, Math.floor(Math.log2(156543.03 * cos * k))));
  const n = 2 ** z, s = k * 156543.03 * cos / n;   // canvas pixels per tile pixel
  // the origin in tile pixels (Web Mercator); ox, oy is where it is on the canvas
  const px = (t.origin[1] + 180) / 360 * 256 * n;
  const py = (1 - Math.log(Math.tan(lat) + 1 / cos) / Math.PI) / 2 * 256 * n;
  let drawn = false;
  for (let tx = Math.floor((px - ox / s) / 256); tx <= Math.floor((px + (w - ox) / s) / 256); tx++) {
    for (let ty = Math.floor((py - oy / s) / 256); ty <= Math.floor((py + (h - oy) / s) / 256); ty++) {
      if (ty < 0 || ty >= n) continue;
      const img = tile(z + "/" + (((tx % n) + n) % n) + "/" + ty);
      if (!img) continue;
      ctx.drawImage(img, ox + (tx * 256 - px) * s, oy + (ty * 256 - py) * s, 256 * s + 1, 256 * s + 1);
      drawn = true;
    }
  }
  return drawn;
}

function drawTrack(t) {
  if (!t) return;
  lastTrack = t;
  const key = JSON.stringify([t.x, t.y, t.heading, t.path.length, t.source, t.accuracy_m, t.marks,
    t.origin, $("streets").checked]);
  if (key === trackKey) return;
  trackKey = key;
  setText($("posText"), t.distance_m.toFixed(1) + " m walked, " + t.steps + " steps, " +
    (t.heading === null ? "no compass" : "heading " + t.heading + "°") + ", " + t.speed.toFixed(1) +
    " m/s, from " + SOURCE_TEXT[t.source] + (t.lat === null ? "" : " · " + t.lat.toFixed(6) + ", " + t.lon.toFixed(6)));
  const cv = $("path");
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  if (cv.width !== w || cv.height !== h) { cv.width = w; cv.height = h; }
  const ctx = cv.getContext("2d");
  ctx.clearRect(0, 0, w, h);
  const pts = t.path.concat([[t.x, t.y, t.source]]);
  const marks = Object.values(t.marks || {});
  const xs = pts.map((p) => p[0]).concat(marks.map((m) => m[0]));
  const ys = pts.map((p) => p[1]).concat(marks.map((m) => m[1]));
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(10, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 1.2;
  const k = Math.min(w, h) / span;   // pixels per metre
  const X = (x) => w / 2 + (x - cx) * k, Y = (y) => h / 2 - (y - cy) * k;
  // the street map when there is one, else a grid every 'step' metres; and the scale bar
  const streets = drawStreets(ctx, t, w, h, X(0), Y(0), k);
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((m) => m * k >= 40) || 1000;
  ctx.strokeStyle = "#e2e8f0"; ctx.lineWidth = 1;
  if (!streets) {
    for (let gx = Math.ceil((cx - w / 2 / k) / step) * step; X(gx) <= w; gx += step) { ctx.beginPath(); ctx.moveTo(X(gx), 0); ctx.lineTo(X(gx), h); ctx.stroke(); }
    for (let gy = Math.ceil((cy - h / 2 / k) / step) * step; Y(gy) >= 0; gy += step) { ctx.beginPath(); ctx.moveTo(0, Y(gy)); ctx.lineTo(w, Y(gy)); ctx.stroke(); }
  }
  ctx.font = "11px Segoe UI, Arial";
  if (streets) {   // the tiles' licence asks for this credit
    const credit = "© OpenStreetMap contributors";
    const cw = ctx.measureText(credit).width + 8;
    ctx.fillStyle = "rgba(255, 255, 255, 0.85)"; ctx.fillRect(w - cw, h - 16, cw, 16);
    ctx.fillStyle = "#334155"; ctx.fillText(credit, w - cw + 4, h - 4);
  }
  ctx.fillStyle = "#475569";
  ctx.fillRect(10, h - 12, step * k, 3); ctx.fillText(step + " m", 10, h - 16);
  ctx.fillText("N ↑", w - 28, 14);
  ctx.lineWidth = 3; ctx.lineJoin = "round";
  for (let i = 1; i < pts.length; i++) {
    ctx.strokeStyle = SOURCE_COLOUR[pts[i][2]] || SOURCE_COLOUR.none;
    ctx.beginPath(); ctx.moveTo(X(pts[i - 1][0]), Y(pts[i - 1][1])); ctx.lineTo(X(pts[i][0]), Y(pts[i][1])); ctx.stroke();
  }
  // marked places: a flag
  Object.entries(t.marks || {}).forEach(([name, [mx, my]]) => {
    ctx.fillStyle = "#7c3aed"; ctx.fillRect(X(mx) - 1, Y(my) - 14, 2, 14);
    ctx.beginPath(); ctx.moveTo(X(mx) + 1, Y(my) - 14); ctx.lineTo(X(mx) + 11, Y(my) - 10); ctx.lineTo(X(mx) + 1, Y(my) - 6); ctx.fill();
  });
  // the start, the uncertainty circle and where the person is now, with the heading
  ctx.fillStyle = "#16a34a"; ctx.beginPath(); ctx.arc(X(0), Y(0), 4, 0, 7); ctx.fill();
  ctx.fillStyle = "rgba(37, 99, 235, 0.12)"; ctx.beginPath(); ctx.arc(X(t.x), Y(t.y), Math.max(5, t.accuracy_m * k), 0, 7); ctx.fill();
  ctx.fillStyle = SOURCE_COLOUR[t.source]; ctx.beginPath(); ctx.arc(X(t.x), Y(t.y), 6, 0, 7); ctx.fill();
  if (t.heading !== null) {
    const a = t.heading * Math.PI / 180;
    ctx.strokeStyle = SOURCE_COLOUR[t.source]; ctx.lineWidth = 2; ctx.beginPath();
    ctx.moveTo(X(t.x), Y(t.y)); ctx.lineTo(X(t.x) + 16 * Math.sin(a), Y(t.y) - 16 * Math.cos(a)); ctx.stroke();
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

// how the phone is carried: on foot its steps are walking, on the device shaking is rough ground
let onFoot = false;
document.querySelectorAll("[data-place]").forEach((b) => {
  b.onclick = () => ws && ws.readyState === 1 && ws.send(JSON.stringify({ type: "place", do: b.dataset.place }));
});

$("carried").onclick = () => ws && ws.readyState === 1 &&
  ws.send(JSON.stringify({ type: "carried", on_foot: !onFoot }));

$("streets").onchange = () => drawTrack(lastTrack);

$("voice").onclick = () => {
  voiceOn = !voiceOn;
  $("voice").textContent = "Voice: " + (voiceOn ? "on" : "off");
  if (!voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();
};

connect();
