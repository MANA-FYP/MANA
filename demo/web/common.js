// Shared by the phone page and the dashboard: the icons, and tiles that update in place.
// A tile is built once and only its text and class change, so nothing on the page redraws or
// blinks when the camera or any other sensor reports.

const ICONS = {
  camera: '<path d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.6"/>',
  motion: '<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M3 9l-1.5 3L3 15M21 9l1.5 3L21 15"/>',
  gps: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z"/><circle cx="12" cy="10" r="2.6"/>',
  microphone: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  wifi: '<path d="M2.5 9a14 14 0 0 1 19 0M5.5 12.5a9.5 9.5 0 0 1 13 0M8.7 16a5 5 0 0 1 6.6 0"/><circle cx="12" cy="19.3" r="1.1"/>',
  bluetooth: '<path d="M7 7.5l10 9-5 5V2.5l5 5-10 9"/>',
  depth: '<path d="M12 2.5l8.5 4.5v10L12 21.5 3.5 17V7z"/><path d="M12 12l8.5-5M12 12L3.5 7M12 12v9.5"/>',
  lidar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12L18 6"/>',
  temperature: '<path d="M10 14.5V5a2 2 0 0 1 4 0v9.5a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>',
};

const STATE_TEXT = { active: "Active", idle: "Tap", unavailable: "N/A" };

function iconSVG(id) {
  return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"' +
         ' stroke-linecap="round" stroke-linejoin="round">' + (ICONS[id] || "") + "</svg>";
}

function setText(el, text) {
  if (el.textContent !== text) el.textContent = text;
}

// Build the tiles once, in the order the server gives.
function buildTiles(container, sensors) {
  container.innerHTML = sensors.map((s) =>
    '<div class="tile unavailable" data-id="' + s.id + '">' + iconSVG(s.id) +
    '<div><div class="name"></div><div class="value"></div><div class="bar"><i></i></div></div>' +
    '<span class="status"></span></div>').join("");
}

// Change only what differs.
function updateTiles(container, sensors) {
  if (container.children.length !== sensors.length) buildTiles(container, sensors);
  sensors.forEach((s, i) => {
    const el = container.children[i];
    const cls = "tile " + s.state;
    if (el.className !== cls) el.className = cls;
    if (el.dataset.id !== s.id) el.dataset.id = s.id;
    setText(el.querySelector(".name"), s.label);
    setText(el.querySelector(".value"), s.state === "unavailable" ? (s.reason || "") : (s.value || ""));
    setText(el.querySelector(".status"), STATE_TEXT[s.state] || s.state);
    const w = s.state === "active" ? Math.round((s.trust || 0) * 100) + "%" : "0%";
    const bar = el.querySelector(".bar > i");
    if (bar.style.width !== w) bar.style.width = w;
  });
}
