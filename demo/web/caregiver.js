// MANA caregiver view: shows what the server sends on /ws/caregiver, and sends nothing back.
// If no update arrives for a few seconds the page says so, so a frozen screen is never mistaken
// for a chair that is standing still.

const $ = (id) => document.getElementById(id);
const STATE_TEXT = { moving: "Moving", slow: "Slowing down", driving: "User driving", stopped: "Stopped", offline: "MANA offline" };
const LINK_CLASS = { wifi: "", bluetooth: "warn", none: "bad", unknown: "warn" };
const QUIET_MS = 3000;
let lastUpdate = 0;
let eventsKey = "";

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

function escapeHTML(text) {
  return String(text).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

function clock(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return m + ":" + String(s).padStart(2, "0");
}

function render(v) {
  lastUpdate = Date.now();
  const card = $("status");
  const cls = "card care-status " + v.state;
  if (card.className !== cls) card.className = cls;
  setText($("state"), STATE_TEXT[v.state] || v.state);
  setText($("sentence"), v.online ? v.status : "The user's phone is not sending. Check on them.");

  setText($("facts-title"), v.online ? "Now" : "Last known");
  setText($("mode"), v.mode + (v.manual ? " (manual)" : ""));
  setText($("speed"), v.online ? v.speed_percent + " %" : "-");
  const acc = v.position.accuracy_m;
  setText($("position"), v.position.source + (acc != null && v.position.source === "GPS" ? " (+/-" + Math.round(acc) + " m)" : ""));
  setText($("link"), v.link.text);
  $("link").className = LINK_CLASS[v.link.state] || "";

  const key = JSON.stringify(v.events);
  if (key !== eventsKey) {
    eventsKey = key;
    $("events").innerHTML = v.events.length
      ? v.events.map((a) => '<li><span class="t">' + clock(a.t) + "</span><span>" + escapeHTML(a.text) + "</span></li>").join("")
      : '<li class="empty">Nothing yet.</li>';
  }
}

function watchQuiet() {
  if (!lastUpdate) return setText($("updated"), "Connecting...");
  const quiet = Date.now() - lastUpdate > QUIET_MS;
  setText($("updated"), quiet ? "No update from the laptop for a while. Reconnecting..." : "Live");
  if (quiet) {
    setText($("facts-title"), "Last known");
    $("status").className = "card care-status offline";
    setText($("state"), "No connection");
  }
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(scheme + "://" + location.host + "/ws/caregiver");
  ws.onmessage = (e) => {
    const v = JSON.parse(e.data);
    if (v.type === "caregiver") render(v);
  };
  ws.onclose = () => setTimeout(connect, 1500);
}

connect();
setInterval(watchQuiet, 1000);
