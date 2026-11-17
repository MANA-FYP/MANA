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

function ago(sec) { return sec < 60 ? Math.max(0, Math.round(sec)) + " s" : Math.round(sec / 60) + " min"; }

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
  set($("pulse"), "class", "pulse" + (live ? " on" : ""));
  set($("pulse"), "text", live ? "Live" : "Waiting");
  set($("decision"), "data-state", state);
  set($("sign"), "html", SIGNS[state]);
  set($("word"), "text", WORD[state]);
  set($("why"), "text", !live ? "Open the phone page" : short(d.stop && d.fired === "user stop" ? d.sentence : d.reason));
  const mode = s.on_foot ? MODE.WALK : MODE[d.mode] || MODE.WHEELS;
  set($("mode"), "html", icon(mode[1]) + mode[0] + (s.manual ? " · manual" : ""));
  $("speedBar").style.width = Math.round((d.stop ? 0 : d.speed) * 100) + "%";
  set($("btnAuto"), "class", s.manual ? "" : "on");
  set($("btnManual"), "class", s.manual ? "on" : "");

  // the last decisions, newest first: a small sign, why, and how long ago
  const past = s.decisions.filter((e) => e.state).slice(-4).reverse();
  set($("history"), "html", past.map((e) =>
    '<li data-state="' + e.state + '"><svg viewBox="0 0 100 100" aria-label="' + WORD[e.state] + '">' + SIGNS[e.state] +
    "</svg><span>" + esc(short(e.why)) + "</span><time>" + ago(s.time - e.t) + "</time></li>").join(""));

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

  // a siren or a horn heard on the microphone
  const snd = (s.sounds || []).map((x) => (x.hazard === "siren" ? "Siren" : "Horn"));
  $("sound").hidden = !snd.length;
  set($("sound"), "html", icon("speaker-high") + snd.join(" · "));

  // the nearest thing in the path, from depth
  const dp = s.depth;
  const ahead = dp && dp.ahead_m !== null && dp.ahead_m !== undefined ? dp.ahead_m : null;
  $("ahead").hidden = ahead === null || !live;
  if (ahead !== null) {
    set($("aheadM"), "text", ahead.toFixed(1));
    set($("aheadU"), "text", dp.ahead_is_stairs ? "m to the stairs" : "m ahead");
    $("ahead").dataset.level = dp.ahead_is_stairs ? "info" : ahead <= NEAR_M ? "stop" : ahead <= CLOSE_M ? "slow" : "far";
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
      ctx.fillStyle = "rgba(255, 255, 255, .93)";
      roundRect(ctx, x, ty, tw, th, unit);
      if (near) { ctx.fillStyle = colour; ctx.fillRect(x, ty + unit * 0.6, unit * 0.6, th - unit * 1.2); }
      ctx.fillStyle = css("--text");
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

// ---- the map: the path walked, north up. Under it a grid, the street map or the satellite view,
// once a GPS fix has tied the path to the earth. Tiles come through the laptop, which keeps them,
// so a place seen online has its map offline too. Off until chosen: each tile asked for tells the
// map's server roughly where the person is.
const LAYERS = { grid: ["Grid", "grid-four"], streets: ["Streets", "map-trifold"], satellite: ["Satellite", "globe-hemisphere-west"] };
const CREDIT = { streets: "© OpenStreetMap contributors", satellite: "Imagery: Esri, Maxar, Earthstar Geographics" };
const view = { layer: "grid", zoom: 1, panX: 0, panY: 0 };   // zoom 1 fits the whole path; pan in metres
try { view.layer = localStorage.getItem("mana.layer") || "grid"; } catch (e) { /* storage off: the grid */ }
if (!LAYERS[view.layer]) view.layer = "grid";
const tiles = new Map();   // url -> its Image
let tilesLoaded = 0, mapKey = "", drawn = null;   // drawn: the last map's scale and centre

function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

function tile(url) {
  if (!tiles.has(url)) {
    const img = new Image();
    img.onload = () => { tilesLoaded++; redrawMap(); };
    img.onerror = () => setTimeout(() => { tiles.delete(url); redrawMap(); }, 30000);   // offline: again in 30 s
    img.src = url;
    tiles.set(url, img);
  }
  const img = tiles.get(url);
  return img.complete && img.naturalWidth ? img : null;
}

// the tiles under the path, Web Mercator, placed from the track's origin (x = y = 0) at ox, oy
function drawTiles(ctx, t, w, h, ox, oy, k) {
  if (view.layer === "grid" || !t.origin) return false;
  const lat = t.origin[0] * Math.PI / 180, cos = Math.cos(lat);
  const z = Math.max(0, Math.min(19, Math.floor(Math.log2(156543.03 * cos * k))));
  const n = 2 ** z, s = k * 156543.03 * cos / n;   // canvas pixels per tile pixel
  const px = (t.origin[1] + 180) / 360 * 256 * n;
  const py = (1 - Math.log(Math.tan(lat) + 1 / cos) / Math.PI) / 2 * 256 * n;
  let drawn = false;
  for (let tx = Math.floor((px - ox / s) / 256); tx <= Math.floor((px + (w - ox) / s) / 256); tx++) {
    for (let ty = Math.floor((py - oy / s) / 256); ty <= Math.floor((py + (h - oy) / s) / 256); ty++) {
      if (ty < 0 || ty >= n) continue;
      const xy = z + "/" + (((tx % n) + n) % n) + "/" + ty;
      const img = tile(view.layer === "satellite" ? "/tiles/satellite/" + xy + ".jpg" : "/tiles/" + xy + ".png");
      if (!img) continue;
      ctx.drawImage(img, ox + (tx * 256 - px) * s, oy + (ty * 256 - py) * s, 256 * s + 1, 256 * s + 1);
      drawn = true;
    }
  }
  return drawn;
}

function redrawMap() { mapKey = ""; if (snap) drawMaps(snap.position.track); }

function drawMaps(t) {
  if (!t) return;
  const cv = $("map");
  const key = JSON.stringify([t.x, t.y, t.heading, t.path.length, t.source, t.accuracy_m, t.marks, t.origin,
    view, tilesLoaded, cv.clientWidth, cv.clientHeight]);
  if (key !== mapKey) { mapKey = key; drawTrack(cv, t); }
  set($("stats"), "html",
    "<span>" + icon("footprints") + t.steps + "</span><span>" + icon("person-simple-walk") + t.distance_m.toFixed(1) + " m</span>" +
    (t.heading === null ? "" : "<span>" + icon("compass") + compassWord(t.heading) + "</span>"));
  $("clearDest").hidden = !(t.marks && t.marks.destination);
  const tiled = view.layer !== "grid";
  set($("credit"), "text", !tiled ? "" : t.origin ? CREDIT[view.layer] : "No GPS fix yet");
}
function compassWord(h) { return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round(h / 45) % 8]; }

function drawTrack(cv, t) {
  const w = cv.clientWidth, h = cv.clientHeight;
  if (!w || !h) return;
  const dpr = devicePixelRatio || 1;
  if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = css("--map-bg"); ctx.fillRect(0, 0, w, h);
  const pts = t.path.concat([[t.x, t.y, t.source]]);
  const marks = Object.entries(t.marks || {});
  const xs = pts.map((p) => p[0]).concat(marks.map(([, m]) => m[0]));
  const ys = pts.map((p) => p[1]).concat(marks.map(([, m]) => m[1]));
  // on streets or imagery at least about 60 m across, so the place can be recognised
  const least = view.layer !== "grid" && t.origin ? 60 : 8;
  const span = Math.max(least, Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * 1.3;
  const k = Math.min(w, h) / span * view.zoom;   // pixels per metre
  // zoomed out: the whole path; zoomed in: the person, moved by dragging
  const cx = (view.zoom > 1 ? t.x : (Math.min(...xs) + Math.max(...xs)) / 2) + view.panX;
  const cy = (view.zoom > 1 ? t.y : (Math.min(...ys) + Math.max(...ys)) / 2) + view.panY;
  const X = (x) => w / 2 + (x - cx) * k, Y = (y) => h / 2 - (y - cy) * k;
  drawn = { w, h, cx, cy, k };
  const tiled = drawTiles(ctx, t, w, h, X(0), Y(0), k);
  const step = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000].find((m) => m * k >= 40) || 2000;
  if (!tiled) {
    ctx.strokeStyle = css("--map-grid"); ctx.lineWidth = 1;
    for (let gx = Math.ceil((cx - w / 2 / k) / step) * step; X(gx) <= w; gx += step) { ctx.beginPath(); ctx.moveTo(X(gx), 0); ctx.lineTo(X(gx), h); ctx.stroke(); }
    for (let gy = Math.ceil((cy - h / 2 / k) / step) * step; Y(gy) >= 0; gy += step) { ctx.beginPath(); ctx.moveTo(0, Y(gy)); ctx.lineTo(w, Y(gy)); ctx.stroke(); }
  }
  // the path: a light casing under the line keeps it readable on streets and on imagery
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  const line = (width, colour) => {
    for (let i = 1; i < pts.length; i++) {
      ctx.strokeStyle = colour || (pts[i][2] === "steps" ? css("--slow") : css("--info"));
      ctx.lineWidth = width;
      ctx.beginPath(); ctx.moveTo(X(pts[i - 1][0]), Y(pts[i - 1][1])); ctx.lineTo(X(pts[i][0]), Y(pts[i][1])); ctx.stroke();
    }
  };
  line(8, "rgba(255, 255, 255, .9)"); line(4.5);
  const dot = (x, y, r, fill, ring) => {
    ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fillStyle = fill; ctx.fill();
    if (ring) { ctx.lineWidth = 2.5; ctx.strokeStyle = ring; ctx.stroke(); }
  };
  dot(X(0), Y(0), 6, css("--go"), "#fff");   // the start
  ctx.font = "600 12px " + css("--body");
  marks.forEach(([name, m]) => {   // marked places
    const mx = X(m[0]), my = Y(m[1]);
    const colour = css(name === "destination" ? "--go" : "--stop");
    ctx.fillStyle = colour; ctx.beginPath(); ctx.moveTo(mx, my); ctx.lineTo(mx - 7, my - 12); ctx.lineTo(mx + 7, my - 12); ctx.fill();
    dot(mx, my - 15, 7, colour, "#fff");
    ctx.fillStyle = css("--text"); ctx.fillText(name, mx + 11, my - 11);
  });
  const px = X(t.x), py = Y(t.y);
  ctx.beginPath(); ctx.arc(px, py, Math.min(Math.max(10, (t.accuracy_m || 0) * k), Math.min(w, h) / 6), 0, 7);
  ctx.fillStyle = "rgba(10, 108, 255, .14)"; ctx.fill(); ctx.lineWidth = 1; ctx.strokeStyle = "rgba(10, 108, 255, .35)"; ctx.stroke();
  if (t.heading !== null) {   // where the person faces: a cone, as map apps show it
    const a = (t.heading - 90) * Math.PI / 180;
    const g = ctx.createRadialGradient(px, py, 4, px, py, 46);
    g.addColorStop(0, "rgba(10, 108, 255, .45)"); g.addColorStop(1, "rgba(10, 108, 255, 0)");
    ctx.fillStyle = g; ctx.beginPath(); ctx.moveTo(px, py); ctx.arc(px, py, 46, a - 0.5, a + 0.5); ctx.closePath(); ctx.fill();
  }
  ctx.shadowColor = "rgba(0, 0, 0, .3)"; ctx.shadowBlur = 6;
  dot(px, py, 8, css("--info"), "#fff");
  ctx.shadowBlur = 0;
  // the scale bar, bottom left
  const sy = h - 34;
  ctx.fillStyle = "rgba(255, 255, 255, .88)";
  roundRect(ctx, 10, sy, Math.max(step * k, 30) + 16, 26, 7);
  ctx.fillStyle = css("--text"); ctx.fillRect(18, sy + 8, step * k, 3);
  ctx.fillRect(18, sy + 4, 2, 7); ctx.fillRect(18 + step * k - 2, sy + 4, 2, 7);
  ctx.font = "600 11px " + css("--body"); ctx.fillText(step >= 1000 ? step / 1000 + " km" : step + " m", 18, sy + 23);
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
  // the map: its layer, zoom, drag, back to the person, and the map in the camera's place
  $("layers").innerHTML = Object.entries(LAYERS).map(([id, [name, ic]]) =>
    '<button data-layer="' + id + '" title="' + name + '" aria-label="' + name + '">' + icon(ic) + "</button>").join("");
  const showLayer = () => document.querySelectorAll("[data-layer]").forEach((b) => {
    b.classList.toggle("on", b.dataset.layer === view.layer); b.setAttribute("aria-pressed", String(b.dataset.layer === view.layer));
  });
  showLayer();
  $("layers").onclick = (e) => {
    const b = e.target.closest("[data-layer]");
    if (!b) return;
    view.layer = b.dataset.layer;
    try { localStorage.setItem("mana.layer", view.layer); } catch (err) { /* not kept */ }
    showLayer(); redrawMap();
  };
  $("zoomIn").innerHTML = icon("plus"); $("zoomOut").innerHTML = icon("minus"); $("recentre").innerHTML = icon("crosshair-simple");
  $("zoomIn").onclick = () => { view.zoom = Math.min(64, view.zoom * 2); redrawMap(); };
  $("zoomOut").onclick = () => { view.zoom = Math.max(1, view.zoom / 2); if (view.zoom === 1) view.panX = view.panY = 0; redrawMap(); };
  $("recentre").onclick = () => { view.panX = view.panY = 0; redrawMap(); };
  $("clearDest").innerHTML = icon("x");
  $("clearDest").onclick = () => send({ type: "place", do: "clear" });
  $("map").addEventListener("wheel", (e) => { e.preventDefault(); (e.deltaY < 0 ? $("zoomIn") : $("zoomOut")).click(); }, { passive: false });
  // drag to move the map; a click (no drag) picks the place the person is guided to
  let drag = null;
  $("map").onpointerdown = (e) => { drag = { x: e.clientX, y: e.clientY, moved: 0 }; $("map").setPointerCapture(e.pointerId); };
  $("map").onpointermove = (e) => {
    if (!drag || !drawn) return;
    drag.moved += Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y);
    view.panX -= (e.clientX - drag.x) / drawn.k; view.panY += (e.clientY - drag.y) / drawn.k;
    drag.x = e.clientX; drag.y = e.clientY;
    redrawMap();
  };
  $("map").onpointerup = (e) => {
    if (drag && drag.moved < 6 && drawn) {
      const r = $("map").getBoundingClientRect(), d = drawn;
      const x = d.cx + (e.clientX - r.left - d.w / 2) / d.k, y = d.cy - (e.clientY - r.top - d.h / 2) / d.k;
      send({ type: "place", do: "destination", x: Math.round(x * 100) / 100, y: Math.round(y * 100) / 100 });
    }
    drag = null;
  };
  $("mapFull").innerHTML = icon("arrows-out");
  const swap = () => {   // the map and the camera change places, as a ground station's video and map do
    const a = $("view"), b = $("mapBox"), mark = document.createElement("i");
    a.replaceWith(mark); b.replaceWith(a); mark.replaceWith(b);
    const big = document.body.classList.toggle("bigmap");
    $("mapFull").innerHTML = icon(big ? "arrows-in" : "arrows-out");
    $("mapFull").title = big ? "Camera large again" : "Map large";
    redrawMap();
  };
  $("mapFull").onclick = swap;
  addEventListener("keydown", (e) => { if (e.key === "Escape" && document.body.classList.contains("bigmap")) swap(); });
  $("depthBox").onclick = () => $("depthBox").classList.toggle("off");
  addEventListener("resize", redrawMap);
}

build();
connect();
