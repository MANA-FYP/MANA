// MANA Indoor: which floor, the height travelled, the newest map and the laptop's Wi-Fi.
"use strict";
const $ = (id) => document.getElementById(id);
let ws = null, snap = null, layer = "satellite", labels = true, placesOn = false, pins = [];
const tiles = new Map();
let frameSeq = -1, loading = false, imageryAt = null;

const ICON = {
  buildings: '<path d="M4 21V5l8-3v19M12 21h8V9l-8-3M8 8v.01M8 12v.01M8 16v.01M16 12v.01M16 16v.01"/>',
  map: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2-6-2Zm0 0v14m6-12v14"/>',
  trend: '<path d="M3 17l6-6 4 4 8-8M15 7h6v6"/>',
  camera: '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
  list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
  wifi: '<path d="M2 9a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01"/>',
};
document.querySelectorAll("[data-icon]").forEach((el) => {
  el.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' + ICON[el.dataset.icon] + "</svg>";
});
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SRC = { lift: "--lift", stairs: "--up", hand: "--hand", gps: "--gps" };

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss" : "ws") + "://" + location.host + "/ws/dashboard");
  ws.onmessage = (e) => { const m = JSON.parse(e.data); if (m.type === "snapshot") { snap = m; render(m); } };
  ws.onclose = () => setTimeout(connect, 1500);
}
function send(msg) { if (ws && ws.readyState === 1) ws.send(JSON.stringify(msg)); }
function fit(cv) {
  const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width)), h = Math.max(1, Math.round(r.height));
  if (cv.width !== w * dpr || cv.height !== h * dpr) { cv.width = w * dpr; cv.height = h * dpr; }
  const ctx = cv.getContext("2d"); ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return [ctx, w, h];
}

function render(s) {
  const f = s.floors || {};
  $("linkDot").classList.toggle("on", !!s.phone_online);
  $("linkText").textContent = s.phone_online ? "Phone connected" : "Waiting for the phone";
  const mode = f.outdoors ? "outdoors" : (f.mode || "still");
  const MODE = { still: "Still", walking: "Walking", lift: "In a lift", "stairs up": "On stairs, going up", "stairs down": "On stairs, going down", outdoors: "Outdoors (GPS)" };
  $("modeChip").textContent = MODE[mode] || mode; $("modeChip").dataset.m = mode;
  drawFloor(f);
  drawBuilding(f);
  drawHeight(f);
  drawEvents(f.events || []);
  drawMap(s.position && s.position.track);
  drawWifi(s.wifi_laptop);
  if (s.frame && s.frame.seq !== frameSeq) loadFrame(s.frame);
}

// ---- the floor
function drawFloor(f) {
  const num = $("floorNum");
  num.textContent = f.outdoors && !f.known ? "☀" : (f.label || "?");
  num.classList.toggle("unknown", !f.known && !f.outdoors);
  const last = (f.events || []).slice(-1)[0];
  if (f.outdoors && !f.known) { $("floorWhy").textContent = "Outdoors"; $("floorSub").textContent = "Going in sets the ground floor"; }
  else if (!f.known) {
    $("floorWhy").textContent = "Not known yet";
    $("floorSub").textContent = (f.moved ? (f.moved > 0 ? "+" : "") + f.moved + " floor" + (Math.abs(f.moved) > 1 ? "s" : "") + " since the start. " : "") + "Say “floor two”, tap Set floor, or walk in from outside";
  } else {
    $("floorWhy").textContent = f.label === "G" ? "Ground floor" : (f.label.startsWith("B") ? "Basement " + f.label.slice(1) : "Floor " + f.label);
    $("floorSub").textContent = last ? last.text : "";
  }
}

function drawBuilding(f) {
  const svg = $("building");
  const base = f.known ? f.floor : f.moved || 0;
  const rows = [base + 2, base + 1, base, base - 1, base - 2];
  const H = 80, top = 10, up = css("--up"), lift = css("--lift"), ind = css("--indigo");
  const name = (n) => {
    if (!f.known) { const d = n - (f.moved || 0) + (f.moved || 0) - 0; return n === 0 ? "start" : (n > 0 ? "+" : "") + n; }
    return n === 0 ? "G" : n < 0 ? "B" + -n : String(n);
  };
  let out = '<rect x="58" y="4" width="250" height="410" rx="10" fill="#f6f7fc" stroke="#dfe3f0"/>';
  rows.forEach((n, i) => {
    const y = top + i * H, cur = n === base;
    out += `<rect x="64" y="${y + 4}" width="238" height="${H - 8}" rx="8" fill="${cur ? "#ecebff" : "#fff"}" stroke="${cur ? ind : "#e4e7f2"}" stroke-width="${cur ? 2.5 : 1}"/>`;
    out += `<text x="44" y="${y + H / 2 + 6}" text-anchor="end" font-size="17" font-weight="${cur ? 800 : 600}" fill="${cur ? ind : "#8b93ad"}" font-family="Inter Tight, sans-serif">${name(n)}</text>`;
    // stairs on the left of each floor
    const sx = 76, sy = y + H - 12, on = cur && (f.mode || "").startsWith("stairs");
    let d = `M${sx} ${sy}`;
    for (let k = 0; k < 5; k++) d += ` h10 v-10`;
    out += `<path d="${d}" fill="none" stroke="${on ? up : "#b8bfd4"}" stroke-width="${on ? 3.5 : 2}"/>`;
  });
  // the lift shaft on the right, the car at the current floor
  out += `<rect x="262" y="${top + 4}" width="34" height="${5 * H - 8}" rx="6" fill="none" stroke="#d3d8e8" stroke-dasharray="4 4"/>`;
  const carY = top + 2 * H + 14, inLift = f.mode === "lift";
  out += `<rect x="266" y="${carY}" width="26" height="${H - 28}" rx="5" fill="${inLift ? lift : "#c9cde0"}"/>`;
  if (inLift) out += `<text x="279" y="${carY - 6}" text-anchor="middle" font-size="14" fill="${lift}" font-weight="800">${(f.height_m || 0) >= (f.moved || 0) * (f.floor_h || 3.2) ? "▲" : "▼"}</text>`;
  // the person
  const px = 190, py = top + 2 * H + H / 2;
  out += `<circle cx="${px}" cy="${py - 16}" r="8" fill="${ind}"/><path d="M${px} ${py - 6} v20 M${px - 12} ${py + 2} h24 M${px} ${py + 14} l-9 14 M${px} ${py + 14} l9 14" stroke="${ind}" stroke-width="5" stroke-linecap="round"/>`;
  if (!f.known && !f.outdoors) out += `<text x="183" y="${top + 4 * H + 52}" text-anchor="middle" font-size="12.5" fill="#8b93ad">floors counted from where it started</text>`;
  svg.innerHTML = out;
}

// ---- the height, from the motion sensors
function drawHeight(f) {
  const [ctx, w, h] = fit($("hCv"));
  ctx.clearRect(0, 0, w, h);
  const tr = f.trace || [];
  const fh = f.floor_h || 3.2;
  $("hNow").textContent = (f.height_m >= 0 ? "+" : "") + (f.height_m || 0).toFixed(1) + " m from the start";
  const g = f.gps_altitude;
  $("gpsAlt").textContent = g ? `GPS altitude ${g.m} m (±${g.accuracy_m ?? "?"} m), ${g.above_start_m >= 0 ? "+" : ""}${g.above_start_m} m from the start` : "GPS altitude: none (indoors or not sent)";
  const padL = 40, padB = 18, plotH = h - padB - 6;
  const tEnd = tr.length ? tr[tr.length - 1][0] : 60, tStart = tEnd - 60;
  const hs = tr.map((p) => p[2]);
  let lo = Math.min(-fh * 0.5, ...hs), hi = Math.max(fh * 1.5, ...hs);
  lo = Math.floor(lo / fh) * fh; hi = Math.ceil(hi / fh) * fh;
  const X = (t) => padL + (t - tStart) / 60 * (w - padL - 8), Y = (v) => 6 + (hi - v) / (hi - lo) * plotH;
  ctx.font = "11px Inter, sans-serif"; ctx.fillStyle = "#8b93ad"; ctx.strokeStyle = "#e6e9f3"; ctx.lineWidth = 1;
  for (let v = lo; v <= hi + 1e-6; v += fh) {
    ctx.beginPath(); ctx.setLineDash([4, 4]); ctx.moveTo(padL, Y(v)); ctx.lineTo(w - 8, Y(v)); ctx.stroke(); ctx.setLineDash([]);
    const n = Math.round(v / fh);
    ctx.fillText(n === 0 ? "start" : (n > 0 ? "+" : "") + n + " fl", 2, Y(v) + 4);
  }
  // vertical acceleration as thin bars around the bottom line
  const mid = h - padB - 14;
  ctx.fillStyle = "#c9cde0";
  tr.forEach(([t, a]) => { const x = X(t), bh = Math.max(-12, Math.min(12, a * 10)); ctx.fillRect(x, mid - Math.max(bh, 0), 2, Math.abs(bh) || 1); });
  ctx.strokeStyle = css("--indigo"); ctx.lineWidth = 3; ctx.lineJoin = "round"; ctx.beginPath();
  tr.forEach(([t, , v], i) => (i ? ctx.lineTo(X(t), Y(v)) : ctx.moveTo(X(t), Y(v))));
  ctx.stroke();
  ctx.fillStyle = "#8b93ad"; ctx.fillText("60 s ago", padL, h - 3); ctx.textAlign = "right"; ctx.fillText("now", w - 8, h - 3); ctx.textAlign = "left";
}

function drawEvents(ev) {
  const ol = $("events");
  if (!ev.length) { ol.innerHTML = '<li class="empty">Stairs climbed, lift rides and settings appear here</li>'; return; }
  const now = snap ? snap.time : 0;
  ol.innerHTML = ev.slice().reverse().map((e) =>
    `<li><i style="background:${css(SRC[e.source] || "--indigo")}"></i><span>${esc(e.text)}</span><small>${e.source}</small></li>`).join("");
}
const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

// ---- the map: the newest satellite imagery with place names over it, when it was taken, and the
// places around (places.js) once Places is on
Places.onChange = () => snap && drawMap(snap.position.track);
function tile(url) {
  let im = tiles.get(url);
  if (!im) { im = new Image(); im.onload = () => snap && drawMap(snap.position.track); im.src = url; tiles.set(url, im); }
  return im.complete && im.naturalWidth ? im : null;
}
function mercator(lat, lon, z) {
  const n = 256 * 2 ** z, r = lat * Math.PI / 180;
  return [(lon + 180) / 360 * n, (1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2 * n];
}
function drawMap(t) {
  const [ctx, w, h] = fit($("mapCv"));
  ctx.fillStyle = "#dfe4ee"; ctx.fillRect(0, 0, w, h);
  pins = [];
  const credit = Places.credit(layer, labels, placesOn);
  if ($("credit").textContent !== credit) $("credit").textContent = credit;
  Places.legend($("plegend"), placesOn && !!(t && t.origin));
  if (!t || !t.origin) {
    $("mapHint").hidden = false; $("mapHint").textContent = "No GPS fix yet: the map needs one outdoors"; $("imagery").hidden = true;
    return;
  }
  const [lat0, lon0] = t.origin, k = 111320, cos = Math.cos(lat0 * Math.PI / 180);
  const ll = (x, y) => [lat0 + y / k, lon0 + x / (k * cos)];
  const pts = t.path.map((p) => ll(p[0], p[1])).concat([ll(t.x, t.y)]);
  const [plat, plon] = pts[pts.length - 1];
  let z = placesOn ? 18 : 19;   // with Places on, about 400 m across: the places asked for
  for (; z > 14; z--) {   // the whole path in view
    const ps = pts.map((p) => mercator(p[0], p[1], z));
    const xs = ps.map((p) => p[0]), ys = ps.map((p) => p[1]);
    if (Math.max(...xs) - Math.min(...xs) < w * 0.8 && Math.max(...ys) - Math.min(...ys) < h * 0.8) break;
  }
  const ps = pts.map((p) => mercator(p[0], p[1], z));
  const cx = (Math.max(...ps.map((p) => p[0])) + Math.min(...ps.map((p) => p[0]))) / 2;
  const cy = (Math.max(...ps.map((p) => p[1])) + Math.min(...ps.map((p) => p[1]))) / 2;
  const ox = cx - w / 2, oy = cy - h / 2;
  for (let tx = Math.floor(ox / 256); tx * 256 < ox + w; tx++) {
    for (let ty = Math.floor(oy / 256); ty * 256 < oy + h; ty++) {
      const url = layer === "satellite" ? `/tiles/satellite/${z}/${tx}/${ty}.jpg` : `/tiles/${z}/${tx}/${ty}.png`;
      const im = tile(url);
      if (im) ctx.drawImage(im, tx * 256 - ox, ty * 256 - oy, 256, 256);
    }
  }
  for (let tx = Math.floor(ox / 256); layer === "satellite" && labels && tx * 256 < ox + w; tx++) {
    for (let ty = Math.floor(oy / 256); ty * 256 < oy + h; ty++) {
      const im = tile(`/tiles/labels/${z}/${tx}/${ty}.png`);
      if (im) ctx.drawImage(im, tx * 256 - ox, ty * 256 - oy, 256, 256);
    }
  }
  ctx.lineJoin = ctx.lineCap = "round";
  const line = (width, colour) => { ctx.strokeStyle = colour; ctx.lineWidth = width; ctx.beginPath(); ps.forEach((p, i) => (i ? ctx.lineTo(p[0] - ox, p[1] - oy) : ctx.moveTo(p[0] - ox, p[1] - oy))); ctx.stroke(); };
  line(9, "rgba(255,255,255,.9)"); line(5, css("--indigo"));
  if (placesOn) {   // the places around the person, asked again once they moved ~100 m
    Places.want(plat, plon);
    pins = Places.draw(ctx, (lat, lon) => { const p = mercator(lat, lon, z); return [p[0] - ox, p[1] - oy]; }, w, h);
  }
  const me = ps[ps.length - 1];
  ctx.beginPath(); ctx.arc(me[0] - ox, me[1] - oy, 10, 0, 7); ctx.fillStyle = css("--indigo"); ctx.fill(); ctx.lineWidth = 3; ctx.strokeStyle = "#fff"; ctx.stroke();
  $("mapHint").hidden = false;
  $("mapHint").textContent = t.source === "gps" ? `GPS ±${Math.round(t.accuracy_m)} m` : "GPS lost: counting steps";
  if (!imageryAt || Math.abs(imageryAt[0] - plat) > 0.002 || Math.abs(imageryAt[1] - plon) > 0.002) loadImagery(plat, plon);
}
async function loadImagery(lat, lon) {
  imageryAt = [lat, lon];
  try {
    const r = await fetch(`/api/imagery?lat=${lat.toFixed(5)}&lon=${lon.toFixed(5)}`);
    if (r.status !== 200) throw new Error(r.status);
    const m = await r.json();
    $("imagery").innerHTML = `Satellite imagery taken <b>${esc(m.date || "date not given")}</b>${m.source ? " &middot; " + esc(m.source) : ""}${m.resolution_m ? " &middot; " + m.resolution_m + " m a pixel" : ""}`;
    $("imagery").hidden = layer !== "satellite";
  } catch (e) { $("imagery").hidden = true; }
}
$("layers").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.layer) layer = b.dataset.layer;
  else if (b.dataset.toggle === "labels") labels = !labels;
  else { placesOn = !placesOn; if (!placesOn) Places.card($("pcard"), null); }
  document.querySelectorAll("#layers [data-layer]").forEach((x) => x.classList.toggle("on", x.dataset.layer === layer));
  $("tLabels").classList.toggle("on", labels); $("tLabels").hidden = layer !== "satellite";
  $("tPlaces").classList.toggle("on", placesOn);
  $("imagery").hidden = layer !== "satellite" || !$("imagery").innerHTML;
  if (snap) drawMap(snap.position.track);
};

$("mapCv").onclick = (e) => {   // a tap on a pin shows the place
  const r = $("mapCv").getBoundingClientRect(), p = Places.hit(pins, e.clientX - r.left, e.clientY - r.top);
  if (p) Places.card($("pcard"), p);
};

// ---- the camera
async function loadFrame(fr) {
  if (loading) return;
  loading = true; frameSeq = fr.seq;
  try {
    const r = await fetch("/frame.jpg?seq=" + fr.seq, { cache: "no-store" });
    if (r.status !== 200) return;
    const bmp = await createImageBitmap(await r.blob());
    const cv = $("camCv");
    if (cv.width !== bmp.width || cv.height !== bmp.height) { cv.width = bmp.width; cv.height = bmp.height; }
    const ctx = cv.getContext("2d");
    ctx.drawImage(bmp, 0, 0);
    $("camWait").hidden = true;
    const sx = bmp.width / (fr.w || bmp.width), sy = bmp.height / (fr.h || bmp.height);
    let stairs = null;
    (fr.boxes || []).filter((b) => b.confidence >= 0.3).forEach((b) => {
      const isStairs = /stair/i.test(b.label);
      if (isStairs) stairs = b;
      const c = isStairs ? css("--up") : "#ffffff";
      ctx.strokeStyle = c; ctx.lineWidth = Math.max(2, bmp.width / 200);
      ctx.strokeRect(b.x1 * sx, b.y1 * sy, (b.x2 - b.x1) * sx, (b.y2 - b.y1) * sy);
      const text = b.label + (b.distance_m == null ? "" : " " + b.distance_m.toFixed(1) + " m");
      ctx.font = `600 ${Math.max(12, bmp.width / 40)}px Inter, sans-serif`;
      const tw = ctx.measureText(text).width + 10, th = Math.max(16, bmp.width / 30);
      ctx.fillStyle = c; ctx.fillRect(b.x1 * sx, b.y1 * sy - th, tw, th);
      ctx.fillStyle = isStairs ? "#fff" : "#141a33"; ctx.fillText(text, b.x1 * sx + 5, b.y1 * sy - th * 0.25);
    });
    const tag = $("stairsTag");
    tag.hidden = !stairs;
    if (stairs) tag.textContent = "Stairs" + (stairs.distance_m == null ? "" : " " + stairs.distance_m.toFixed(1) + " m") + " ahead";
  } finally { loading = false; }
}

// ---- the laptop's Wi-Fi
function drawWifi(wf) {
  const [ctx, w, h] = fit($("wCv"));
  ctx.clearRect(0, 0, w, h);
  if (!wf) { $("dbm").textContent = "–"; $("wNote").textContent = "No Wi-Fi adapter readable on this computer."; return; }
  $("dbm").textContent = wf.dbm == null ? "–" : wf.dbm + " dBm";
  const s = wf.samples || [];
  if (s.length > 1) {
    const t0 = s[0][0], t1 = s[s.length - 1][0] || t0 + 1, lo = -90, hi = -30;
    const X = (t) => 6 + (t - t0) / Math.max(1, t1 - t0) * (w - 12), Y = (v) => 6 + (hi - v) / (hi - lo) * (h - 12);
    ctx.strokeStyle = "#e6e9f3"; ctx.lineWidth = 1;
    [-40, -60, -80].forEach((v) => { ctx.beginPath(); ctx.moveTo(6, Y(v)); ctx.lineTo(w - 6, Y(v)); ctx.stroke(); ctx.fillStyle = "#8b93ad"; ctx.font = "10px Inter"; ctx.fillText(v + " dBm", 8, Y(v) - 3); });
    ctx.strokeStyle = css("--sky"); ctx.lineWidth = 2.5; ctx.beginPath();
    s.forEach(([t, v], i) => (i ? ctx.lineTo(X(t), Y(v)) : ctx.moveTo(X(t), Y(v)))); ctx.stroke();
  }
  $("wNote").innerHTML = `${wf.reads} reads in ${Math.round(wf.span_s)} s, the value changed <b>${wf.changes} time${wf.changes === 1 ? "" : "s"}</b>. ` +
    "Windows gives only the RSSI, refreshed rarely: too coarse to sense people. Wi-Fi sensing needs the channel state (CSI), e.g. from two ESP32 boards.";
}

// ---- controls
$("up").onclick = () => send({ type: "floor", set: (snap && snap.floors && snap.floors.known ? snap.floors.floor : 0) + 1 });
$("down").onclick = () => send({ type: "floor", set: (snap && snap.floors && snap.floors.known ? snap.floors.floor : 0) - 1 });
$("setFloor").onclick = () => send({ type: "floor", set: snap && snap.floors && snap.floors.known ? snap.floors.floor : 0 });
$("floorH").onchange = (e) => send({ type: "floor", height: parseFloat(e.target.value) });
addEventListener("resize", () => snap && render(snap));
connect();
