// MANA Live: the camera with what was detected, the decision as one sign, the sensors as icons.
// Everything comes from the dashboard socket's snapshots; each part redraws only when it changes.

const $ = (id) => document.getElementById(id);
const NEAR_M = 1.2, CLOSE_M = 2.5;   // a detection's colour: red within NEAR_M, amber within CLOSE_M
const MIN_CONF = 0.35;               // boxes less sure than this are not drawn
const SENSES = [
  ["camera", "camera", "Camera"], ["depth", "cube-focus", "Depth from the camera"], ["gps", "map-pin", "GPS"],
  ["motion", "person-simple-walk", "Motion and steps"], ["microphone", "microphone", "Microphone"],
  ["wifi", "wifi-high", "Wi-Fi"], ["bluetooth", "bluetooth", "Bluetooth"],
];
const SIGNS = {   // a shape for each decision, so colour is never the only cue
  stop: '<polygon class="shape" points="30,4 70,4 96,30 96,70 70,96 30,96 4,70 4,30"/><path class="mark" d="M32 50h36"/>',
  slow: '<polygon class="shape" points="50,6 96,90 4,90"/><path class="mark" d="M50 36v24M50 74v1"/>',
  go: '<circle class="shape" cx="50" cy="50" r="46"/><path class="mark" d="M40 30l22 20-22 20"/>',
  idle: '<circle class="shape" cx="50" cy="50" r="46"/><path class="mark" d="M34 50h4M48 50h4M62 50h4"/>',
};
const WORD = { stop: "Stop", slow: "Slow", go: "Go", idle: "Waiting" };
const MODE = { WHEELS: ["Wheels", "navigation-arrow"], TRACKS: ["Tracks", "footprints"], WALK: ["Walking", "person-simple-walk"] };

let ws = null, snap = null, voiceOn = true, lastHeard = null, lastFrame = 0, loading = false;
let boxes = [], frameSize = { w: 0, h: 0 }, depthSeq = 0;
const shown = {};   // last text or class put in each element, so nothing is rewritten needlessly

function set(el, prop, value) {
  const k = el.id + "." + prop;
  if (shown[k] === value) return;
  shown[k] = value;
  if (prop === "text") el.textContent = value;
  else if (prop === "html") el.innerHTML = value;
  else el.setAttribute(prop, value);
}

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss" : "ws") + "://" + location.host + "/ws/dashboard");
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === "snapshot") render(m); };
  ws.onclose = () => setTimeout(connect, 1500);
}
function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }

// the first clause of a message: "Obstacle ahead, stopping" -> "Obstacle ahead"
function short(text) { return (text || "").split(/[.,]/)[0].trim(); }

function distColour(d) {
  return d === null || d === undefined ? "var(--box)" : d <= NEAR_M ? "var(--stop)" : d <= CLOSE_M ? "var(--slow)" : "var(--box)";
}

function render(s) {
  snap = s;
  const byId = Object.fromEntries(s.sensors.map((x) => [x.id, x]));
  const live = s.sensors.some((x) => x.state === "active");

  // sensors: an icon each, with a dot; depth is the camera's depth model, live while it reads frames
  SENSES.forEach(([id, ic, label]) => {
    const li = $("s-" + id);
    let state = (byId[id] || {}).state || "unavailable", value = (byId[id] || {}).value || (byId[id] || {}).reason || "";
    if (id === "depth") {
      state = !s.models.depth ? "unavailable" : s.depth && byId.camera && byId.camera.state === "active" ? "active" : "idle";
      value = s.depth && s.depth.ms ? Math.round(s.depth.ms) + " ms a frame" : "";
    }
    set(li, "class", "sense " + state);
    set(li, "title", label + (value ? ": " + value : ""));
  });

  // the link: its round trip, and which radio carries the alerts
  const rtt = s.link.rtt_ms;
  const radio = s.link.state === "bluetooth" ? "bluetooth" : "wifi-high";
  set($("link"), "class", "link" + (s.link.state === "none" ? " bad" : ""));
  set($("link"), "html", icon(radio) + (rtt === null || rtt === undefined ? "" : Math.round(rtt) + " ms"));

  // the decision: one sign, one word, why in a few words, the mode and the speed limit
  const d = s.decision;
  const state = !live ? "idle" : d.stop ? "stop" : d.speed < 1 ? "slow" : "go";
  set($("view"), "data-state", state);
  set($("decision"), "data-state", state);
  set($("sign"), "html", SIGNS[state]);
  set($("word"), "text", WORD[state]);
  set($("why"), "text", !live ? "Open the phone page" : short(d.stop && d.fired === "user stop" ? d.sentence : d.reason));
  const mode = s.on_foot ? MODE.WALK : MODE[d.mode] || MODE.WHEELS;
  set($("mode"), "html", icon(mode[1]) + mode[0] + (s.manual ? " · manual" : ""));
  $("speedBar").style.width = Math.round((d.stop ? 0 : d.speed) * 100) + "%";
  set($("btnAuto"), "class", s.manual ? "" : "on");
  set($("btnManual"), "class", s.manual ? "on" : "");

  // the spoken message: the caption over the camera, and the laptop says it
  const said = s.heard || [];
  const newest = said.length ? said[said.length - 1] : null;
  set($("caption"), "text", newest && s.time - newest.t < 8 ? newest.text : "");
  speakNew(said);

  // what the user said
  const mic = byId.microphone;
  const input = s.inputs.length ? s.inputs[s.inputs.length - 1] : null;
  const fresh = input && s.time - input.t < 10;
  if (s.speaking) set($("heard"), "html", icon("ear") + "<q>" + esc(s.speaking) + "</q>");
  else if (fresh) set($("heard"), "html", icon("ear") + "<q>" + esc(input.text) + "</q>" + (input.command ? '<span class="cmd">' + esc(input.command) + "</span>" : ""));
  else set($("heard"), "html", icon("microphone") + (mic && mic.state === "active" ? "Say “stop”, “go” or “yes”" : "Microphone off"));
  set($("heard"), "class", "heard" + (s.speaking || fresh ? " live" : ""));

  // the nearest thing in the path, from depth
  const dp = s.depth;
  const ahead = dp && dp.ahead_m !== null && dp.ahead_m !== undefined ? dp.ahead_m : null;
  $("ahead").hidden = ahead === null || !live;
  if (ahead !== null) {
    set($("aheadM"), "text", ahead.toFixed(1));
    set($("aheadU"), "text", dp.ahead_is_stairs ? "m to the stairs" : "m ahead");
    $("ahead").style.setProperty("--a", dp.ahead_is_stairs ? "var(--info)" : distColour(ahead));
  }
  if (dp && dp.seq > depthSeq) {
    depthSeq = dp.seq;
    $("depth").src = "/depth.jpg?seq=" + dp.seq;
    $("depthBox").hidden = false;
  }

  // the camera
  const cam = byId.camera, camLive = !!cam && cam.state === "active";
  $("waiting").hidden = camLive && lastFrame > 0;
  if (!camLive) set($("waiting"), "html", icon("camera") + (lastFrame ? "Camera paused" : "Waiting for the phone camera"));
  $("cam").style.opacity = camLive ? "" : ".25";
  if (s.frame.seq > lastFrame) {
    boxes = s.frame.boxes;
    frameSize = { w: s.frame.w, h: s.frame.h };
    loadFrame(s.frame.seq);
  }

  drawMaps(s.position.track);
}

function esc(t) { return String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]); }

function speakNew(said) {
  const newest = said.length ? said[said.length - 1].n : 0;
  if (lastHeard === null) { lastHeard = newest; return; }   // what came before the page opened is not said
  if (newest <= lastHeard) return;
  const fresh = said.filter((e) => e.n > lastHeard);
  lastHeard = newest;
  if (!voiceOn || !("speechSynthesis" in window)) return;
  fresh.forEach((e) => {
    if (e.priority === "URGENT") speechSynthesis.cancel();   // a stop does not wait its turn
    if (e.speak) speechSynthesis.speak(new SpeechSynthesisUtterance(e.text));
  });
}

// ---- the camera: the frame, then the boxes. Boxes are quiet corner marks; the ones near enough
// to matter turn amber or red, with their distance.
async function loadFrame(seq) {
  if (loading) return;
  loading = true;
  try {
    const r = await fetch("/frame.jpg?seq=" + seq, { cache: "no-store" });
    if (r.status !== 200) return;
    const bmp = await createImageBitmap(await r.blob());
    const cv = $("cam");
    if (cv.width !== bmp.width || cv.height !== bmp.height) { cv.width = bmp.width; cv.height = bmp.height; }
    const ctx = cv.getContext("2d");
    ctx.drawImage(bmp, 0, 0);
    const sx = bmp.width / (frameSize.w || bmp.width), sy = bmp.height / (frameSize.h || bmp.height);
    const unit = bmp.width / 100;
    const shownBoxes = boxes.filter((b) => b.confidence >= MIN_CONF)
      .sort((a, b) => (b.distance_m ?? 99) - (a.distance_m ?? 99));   // nearest drawn last, on top
    shownBoxes.forEach((b) => {
      const x = b.x1 * sx, y = b.y1 * sy, w = (b.x2 - b.x1) * sx, h = (b.y2 - b.y1) * sy;
      const colour = getComputedStyle(document.body).getPropertyValue(distColour(b.distance_m).slice(4, -1)).trim();
      const near = b.distance_m !== null && b.distance_m <= CLOSE_M;
      corners(ctx, x, y, w, h, colour, near ? unit * 0.8 : unit * 0.45, Math.min(w, h) * 0.22);
      const text = b.label + (b.distance_m === null ? "" : "  " + b.distance_m.toFixed(1) + " m");
      ctx.font = "600 " + Math.round(unit * 2.6) + "px Barlow, Segoe UI, sans-serif";
      const tw = ctx.measureText(text).width + unit * 2.4, th = unit * 4.2;
      const ty = y - th - unit * 0.6 < 0 ? y + unit * 0.6 : y - th - unit * 0.6;
      ctx.fillStyle = "rgba(16, 22, 30, .82)";
      roundRect(ctx, x, ty, tw, th, unit);
      ctx.fillStyle = near ? colour : "#e8ecef";
      ctx.fillText(text, x + unit * 1.2, ty + th * 0.72);
    });
    lastFrame = seq;
    $("waiting").hidden = true;
  } finally {
    loading = false;
  }
}

function corners(ctx, x, y, w, h, colour, lw, len) {
  ctx.strokeStyle = colour; ctx.lineWidth = lw; ctx.lineCap = "round";
  ctx.beginPath();
  [[x, y, 1, 1], [x + w, y, -1, 1], [x, y + h, 1, -1], [x + w, y + h, -1, -1]].forEach(([cx, cy, dx, dy]) => {
    ctx.moveTo(cx + dx * len, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * len);
  });
  ctx.stroke();
}
function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath(); ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.fill();
}

// ---- the path walked, north up, scaled to fit; GPS blue, steps amber
let mapKey = "";
function drawMaps(t) {
  if (!t) return;
  const big = document.body.classList.contains("bigmap");
  const key = JSON.stringify([t.x, t.y, t.heading, t.path.length, t.source, big, innerWidth, innerHeight]);
  if (key === mapKey) return;
  mapKey = key;
  drawTrack($("map"), t);
  if (big) drawTrack($("bigmap"), t);
  set($("stats"), "html",
    "<span>" + icon("footprints") + t.steps + "</span><span>" + icon("person-simple-walk") + t.distance_m.toFixed(1) + " m</span>" +
    (t.heading === null ? "" : "<span>" + icon("compass") + compassWord(t.heading) + "</span>"));
}
function compassWord(h) { return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(h / 45) % 8]; }

function drawTrack(cv, t) {
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  const dpr = devicePixelRatio || 1;
  if (cv.width !== w * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const pts = t.path.concat([[t.x, t.y, t.source]]);
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
  const span = Math.max(8, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 1.3;
  const k = Math.min(w, h) / span;
  const X = (x) => w / 2 + (x - cx) * k, Y = (y) => h / 2 - (y - cy) * k;
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500].find((m) => m * k >= 36) || 1000;
  ctx.strokeStyle = "rgba(147, 160, 174, .14)"; ctx.lineWidth = 1;
  for (let gx = Math.ceil((cx - w / 2 / k) / step) * step; X(gx) <= w; gx += step) { ctx.beginPath(); ctx.moveTo(X(gx), 0); ctx.lineTo(X(gx), h); ctx.stroke(); }
  for (let gy = Math.ceil((cy - h / 2 / k) / step) * step; Y(gy) >= 0; gy += step) { ctx.beginPath(); ctx.moveTo(0, Y(gy)); ctx.lineTo(w, Y(gy)); ctx.stroke(); }
  ctx.lineWidth = 4; ctx.lineJoin = "round"; ctx.lineCap = "round";
  for (let i = 1; i < pts.length; i++) {
    ctx.strokeStyle = pts[i][2] === "steps" ? "#e8a33d" : "#5aa9e6";
    ctx.beginPath(); ctx.moveTo(X(pts[i - 1][0]), Y(pts[i - 1][1])); ctx.lineTo(X(pts[i][0]), Y(pts[i][1])); ctx.stroke();
  }
  ctx.fillStyle = "#2fa36b"; ctx.beginPath(); ctx.arc(X(0), Y(0), 5, 0, 7); ctx.fill();   // the start
  ctx.fillStyle = "rgba(90, 169, 230, .16)"; ctx.beginPath(); ctx.arc(X(t.x), Y(t.y), Math.min(Math.max(8, (t.accuracy_m || 0) * k), 28), 0, 7); ctx.fill();
  ctx.fillStyle = "#e8ecef"; ctx.beginPath(); ctx.arc(X(t.x), Y(t.y), 7, 0, 7); ctx.fill();
  if (t.heading !== null) {   // where the person faces
    const a = t.heading * Math.PI / 180, px = X(t.x), py = Y(t.y);
    ctx.fillStyle = "#5aa9e6"; ctx.beginPath();
    ctx.moveTo(px + 20 * Math.sin(a), py - 20 * Math.cos(a));
    ctx.lineTo(px + 9 * Math.sin(a + 2.4), py - 9 * Math.cos(a + 2.4));
    ctx.lineTo(px + 9 * Math.sin(a - 2.4), py - 9 * Math.cos(a - 2.4)); ctx.fill();
  }
  ctx.fillStyle = "#93a0ae"; ctx.font = "12px Barlow, Segoe UI, sans-serif";
  ctx.fillRect(w - 14 - step * k, 14, step * k, 3); ctx.fillText(step + " m", w - 14 - step * k, 32);
}

// ---- controls
function build() {
  $("senses").innerHTML = SENSES.map(([id, ic]) => '<li class="sense unavailable" id="s-' + id + '">' + icon(ic) + "</li>").join("");
  $("btnStop").innerHTML = icon("hand-palm") + "Stop";
  $("btnGo").innerHTML = icon("play") + "Go";
  $("btnAuto").innerHTML = icon("robot") + "Automatic";
  $("btnManual").innerHTML = icon("hand-pointing") + "Manual";
  $("details").innerHTML = icon("list-dashes");
  $("voice").innerHTML = icon("speaker-high");
  $("waiting").innerHTML = icon("camera") + "Waiting for the phone camera";
  $("heard").innerHTML = icon("microphone") + "Microphone off";
  $("sign").innerHTML = SIGNS.idle;
  document.querySelectorAll("[data-button]").forEach((b) => { b.onclick = () => send({ type: "button", name: b.dataset.button }); });
  $("voice").onclick = () => {
    voiceOn = !voiceOn;
    $("voice").innerHTML = icon(voiceOn ? "speaker-high" : "speaker-slash");
    $("voice").setAttribute("aria-pressed", String(voiceOn));
    if (!voiceOn && "speechSynthesis" in window) speechSynthesis.cancel();
  };
  $("mapBox").onclick = () => {   // the map, large in the camera's place, and back
    const big = document.body.classList.toggle("bigmap");
    $("bigmap").hidden = !big; $("cam").hidden = big;
    mapKey = "";
    if (snap) drawMaps(snap.position.track);
  };
  $("depthBox").onclick = () => $("depthBox").classList.toggle("off");
  addEventListener("resize", () => { mapKey = ""; if (snap) drawMaps(snap.position.track); });
}

build();
connect();
