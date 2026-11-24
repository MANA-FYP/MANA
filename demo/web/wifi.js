// MANA Wi-Fi sensing screen: polls /api/csi and draws the verdict, the subcarriers and the scores.
"use strict";

const NAMES = { empty: "Empty room", one: "Zone 1", two: "Zone 2", five: "Zone 5" };
const COLORS = { empty: "#64748b", one: "#22d3ee", two: "#fbbf24", five: "#a78bfa" };
const $ = (id) => document.getElementById(id);
const name = (k) => NAMES[k] || k;
let lastPackets = null, lastT = null, rate = 0, scoresDrawn = false, pickerDrawn = false;

// ---- waterfall colours: dark navy -> blue -> cyan -> amber -> pale yellow
const STOPS = [[11, 13, 42], [27, 31, 74], [29, 111, 163], [34, 211, 238], [251, 191, 36], [255, 243, 196]];
const LUT = Array.from({ length: 256 }, (_, v) => {
  const x = (v / 255) * (STOPS.length - 1), i = Math.min(STOPS.length - 2, Math.floor(x)), f = x - i;
  return STOPS[i].map((c, j) => Math.round(c + (STOPS[i + 1][j] - c) * f));
});

function fit(canvas) {
  const r = canvas.getBoundingClientRect(), d = window.devicePixelRatio || 1;
  const w = Math.max(1, Math.round(r.width * d)), h = Math.max(1, Math.round(r.height * d));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  return [w, h, d];
}

function drawWater(rows) {
  const c = $("water"), [w, h] = fit(c), g = c.getContext("2d");
  g.fillStyle = "#070b18"; g.fillRect(0, 0, w, h);
  if (!rows.length) return;
  const n = rows[0].length, img = g.createImageData(100, n);
  const off = 100 - rows.length;  // newest at the right
  rows.forEach((row, x) => row.forEach((v, y) => {
    const p = ((n - 1 - y) * 100 + x + off) * 4, col = LUT[v];
    img.data[p] = col[0]; img.data[p + 1] = col[1]; img.data[p + 2] = col[2]; img.data[p + 3] = 255;
  }));
  const tmp = document.createElement("canvas"); tmp.width = 100; tmp.height = n;
  tmp.getContext("2d").putImageData(img, 0, 0);
  g.imageSmoothingEnabled = false;
  g.drawImage(tmp, 0, 0, w, h);
}

function drawChart(hist, thr) {
  const c = $("chart"), [w, h, d] = fit(c), g = c.getContext("2d");
  g.clearRect(0, 0, w, h);
  const pad = 8 * d, top = Math.max(0.15, ...hist.map((v) => v.motion * 1.2));
  const X = (i) => pad + (i + (60 - hist.length)) / 59 * (w - 2 * pad);
  const Y = (v) => h - pad - (v / top) * (h - 2 * pad);
  g.strokeStyle = "rgba(35,48,86,.9)"; g.lineWidth = 1;
  for (let k = 1; k < 4; k++) { g.beginPath(); g.moveTo(0, h * k / 4); g.lineTo(w, h * k / 4); g.stroke(); }
  if (thr != null) {
    g.setLineDash([6 * d, 5 * d]); g.strokeStyle = "#fb7185"; g.lineWidth = 2 * d;
    g.beginPath(); g.moveTo(pad, Y(thr)); g.lineTo(w - pad, Y(thr)); g.stroke(); g.setLineDash([]);
  }
  if (hist.length < 2) return;
  g.strokeStyle = "#22d3ee"; g.lineWidth = 2.5 * d; g.lineJoin = "round"; g.beginPath();
  hist.forEach((v, i) => (i ? g.lineTo(X(i), Y(v.motion)) : g.moveTo(X(i), Y(v.motion))));
  g.stroke();
  g.fillStyle = "#9aa5c8"; g.font = `${11 * d}px JetBrains Mono, monospace`;
  g.fillText(top.toFixed(2), pad, pad + 10 * d);
}

function drawStrip(hist) {
  const cells = [];
  for (let i = 0; i < 60 - hist.length; i++) cells.push('<i style="background:transparent"></i>');
  hist.forEach((v) => cells.push(`<i title="${name(v.label)}" style="background:${COLORS[v.label] || "#334"}"></i>`));
  $("strip").innerHTML = cells.join("");
}

const PERSON = '<circle cx="32" cy="16" r="9" fill="currentColor"/><path d="M14 58c0-12 8-22 18-22s18 10 18 22z" fill="currentColor"/>';
const ROOM = '<rect x="8" y="10" width="48" height="44" rx="6" fill="none" stroke="currentColor" stroke-width="4" stroke-dasharray="6 5"/>';

function drawVerdict(v, labels) {
  const big = $("big");
  if (!v || !v.label) return;
  big.className = "big " + (v.present ? "present" : "empty");
  $("whoIcon").innerHTML = v.present ? PERSON : ROOM;
  $("whoIcon").style.color = v.present ? COLORS[v.label] : "#64748b";
  const p = v.scores[v.label];
  $("bigText").textContent = v.present ? "Someone is in the room" : "The room is empty";
  $("bigSub").textContent = (v.present ? `${name(v.label)}, ` : "") +
    `${Math.round(p * 100)} % sure` + (v.moving ? " · moving" : " · still");
  $("bars").innerHTML = labels.map((k) => {
    const s = v.scores[k] || 0;
    return `<div class="bar ${k === v.label ? "top" : ""}"><span>${name(k)}</span>` +
      `<div class="track"><div class="fill" style="width:${(s * 100).toFixed(1)}%;background:${COLORS[k]}"></div></div>` +
      `<span class="pct">${Math.round(s * 100)} %</span></div>`;
  }).join("");
  if (v.truth) {
    $("truth").hidden = false;
    $("truthText").textContent = `recorded with ${v.truth === "empty" ? "the room empty" : "a person in " + name(v.truth).toLowerCase()}`;
    const ok = v.truth === v.label;
    $("truthMark").className = "mark2 " + (ok ? "ok" : "no");
    $("truthMark").textContent = ok ? "Right" : "Wrong";
  }
}

function drawScores(sc) {
  if (scoresDrawn || !sc) return;
  scoresDrawn = true;
  const cls = (x) => (x >= 0.9 ? "good" : x >= 0.7 ? "mid" : "bad");
  const rows = Object.entries(sc.csi.rows).map(([k, r]) => {
    const truth = k.split("_")[0], a = r.share[truth];
    // the RSSI alone, with the CSI's own model (logistic) and a random forest, when both are there
    const rssi = [sc.rssi_only_logistic, sc.rssi_only].filter(Boolean).map((m) => m.rows[k].share[truth]);
    const label = truth === "empty" ? `Empty (${k.split("_")[1]})` : name(truth);
    return `<tr><td>${label}</td><td>${r.windows}</td><td class="${cls(a)}">${Math.round(a * 100)} %</td>` +
      rssi.map((b) => `<td class="${cls(b)}">${Math.round(b * 100)} %</td>`).join("") + "</tr>";
  });
  const heads = sc.rssi_only_logistic ? "<th>RSSI, same model</th><th>RSSI, forest</th>" : "<th>RSSI alone</th>";
  $("scoreTbl").innerHTML = "<tr><th>Session</th><th>Windows</th><th>Wi-Fi CSI right</th>" + heads + "</tr>" + rows.join("");
  const pct = (m) => (m.presence_accuracy * 100).toFixed(1) + " %";
  const rssiNote = sc.rssi_only_logistic
    ? `${pct(sc.rssi_only_logistic)} with RSSI alone (same model; ${pct(sc.rssi_only)} with a random forest)`
    : `${pct(sc.rssi_only)} with RSSI alone`;
  $("scoreNote").textContent = `Empty or not: ${pct(sc.csi)} right with CSI, ` +
    `${rssiNote}. ${sc.data}. Fitted on each label's first ` +
    "session, scored on later ones. One office, one placement of the boards: a new room needs its own calibration.";
}

function drawPicker(rep) {
  if (!rep) { $("picker").innerHTML = ""; return; }
  const want = rep.fixed;
  const btn = (k, label) => `<button data-k="${k}" class="${(want || "tour") === k ? "on" : ""}">${label}</button>`;
  $("picker").innerHTML = '<span class="lab">Recording to play</span>' + btn("tour", `Tour (${rep.turn_s} s each)`) +
    rep.labels.map((k) => btn(k, name(k))).join("");
  if (!pickerDrawn) {
    pickerDrawn = true;
    $("picker").addEventListener("click", async (e) => {
      const k = e.target.dataset && e.target.dataset.k;
      if (!k) return;
      await fetch("/api/csi/play", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: k === "tour" ? null : k }) });
    });
  }
}

function drawHow(v) {
  const body = v && v.present, col = body ? COLORS[v.label] : "#475077";
  $("howSvg").innerHTML = `
    <rect x="6" y="6" width="348" height="128" rx="12" fill="none" stroke="#233056" stroke-width="2"/>
    <text x="16" y="24" fill="#9aa5c8" font-size="11" font-family="Inter">room</text>
    <path d="M44 70 L316 70" stroke="#22d3ee" stroke-width="2" stroke-dasharray="5 4"/>
    <path d="M44 70 L180 8 L316 70" fill="none" stroke="#2b5d8a" stroke-width="2" stroke-dasharray="5 4"/>
    <path d="M44 70 L180 112 L316 70" fill="none" stroke="${col}" stroke-width="${body ? 3 : 2}" stroke-dasharray="5 4"/>
    <g transform="translate(166 86)" fill="${col}"><circle cx="14" cy="8" r="7"/><path d="M2 40c0-11 5-20 12-20s12 9 12 20z"/></g>
    <rect x="22" y="58" width="36" height="24" rx="5" fill="#162040" stroke="#22d3ee" stroke-width="2"/>
    <text x="40" y="74" fill="#e8ecff" font-size="10" font-weight="700" text-anchor="middle" font-family="Inter">TX</text>
    <rect x="302" y="58" width="36" height="24" rx="5" fill="#162040" stroke="#22d3ee" stroke-width="2"/>
    <text x="320" y="74" fill="#e8ecff" font-size="10" font-weight="700" text-anchor="middle" font-family="Inter">RX</text>
    <text x="40" y="98" fill="#9aa5c8" font-size="10" text-anchor="middle" font-family="Inter">ESP32</text>
    <text x="320" y="98" fill="#9aa5c8" font-size="10" text-anchor="middle" font-family="Inter">ESP32</text>
    <path d="M320 134 L320 154" stroke="#9aa5c8" stroke-width="2"/>
    <rect x="276" y="154" width="88" height="22" rx="5" fill="#162040" stroke="#233056"/>
    <text x="320" y="169" fill="#e8ecff" font-size="10" text-anchor="middle" font-family="Inter">USB → laptop</text>
    <text x="180" y="66" fill="#22d3ee" font-size="10" text-anchor="middle" font-family="Inter">direct</text>
    <text x="180" y="22" fill="#6b8fb8" font-size="10" text-anchor="middle" font-family="Inter">off the wall</text>
    <text x="226" y="122" fill="${body ? col : "#6b7499"}" font-size="10" font-family="Inter">off a person</text>`;
}

function off() {
  $("grid").innerHTML = '<div class="off"><div><b>Wi-Fi sensing is off.</b><br>Start the server with ' +
    "<code>--csi-replay datasets/office_csi</code> (recorded ESP32 sessions)<br>or " +
    "<code>--csi-serial COM5</code> (a live ESP32 with esp-csi).</div></div>";
}

async function tick() {
  let s;
  try {
    const r = await fetch("/api/csi", { cache: "no-store" });
    s = await r.json();
  } catch (e) { $("srcDot").className = "dot"; $("srcText").textContent = "Server not reachable"; return; }
  if (!s.on) { off(); return; }
  const now = performance.now() / 1000;
  if (lastPackets != null && now > lastT) rate = 0.7 * rate + 0.3 * ((s.packets - lastPackets) / (now - lastT));
  lastPackets = s.packets; lastT = now;
  $("srcDot").className = "dot" + (rate > 1 ? " on" : "");
  $("srcText").textContent = s.replay ? "Recorded ESP32 sessions, held out from the model" : s.source;
  $("rate").textContent = `${Math.max(0, Math.round(rate))} packets/s`;
  const labels = s.labels || ["empty", "one", "two", "five"];
  drawVerdict(s.last, labels);
  drawWater(s.waterfall || []);
  drawChart(s.history || [], s.threshold);
  drawStrip(s.history || []);
  drawHow(s.last);
  drawScores(s.scores);
  drawPicker(s.replay);
  if (s.replay) $("truthScore").textContent = `${s.replay.right} of ${s.replay.judged} windows`;
}

drawHow(null);
tick();
setInterval(tick, 250);
