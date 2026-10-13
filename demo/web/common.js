// Shared by the phone page and the dashboard: the icons and one function that draws a sensor tile.

const ICONS = {
  camera: '<path d="M4 7h3l1.5-2h7L17 7h3a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1V8a1 1 0 0 1 1-1z"/><circle cx="12" cy="13" r="3.6"/>',
  motion: '<rect x="8" y="3" width="8" height="18" rx="2"/><path d="M3 9l-1.5 3L3 15M21 9l1.5 3L21 15"/>',
  gps: '<path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.800-7 11-7 11z"/><circle cx="12" cy="10" r="2.6"/>',
  microphone: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
  link: '<path d="M2.500 9a14 14 0 0 1 19 0M5.500 12.500a9.500 9.500 0 0 1 13 0M8.700 16a5 5 0 0 1 6.600 0"/><circle cx="12" cy="19.300" r="1.100"/>',
  depth: '<path d="M12 2.500l8.500 4.500v10L12 21.500 3.500 17V7z"/><path d="M12 12l8.500-5M12 12L3.500 7M12 12v9.500"/>',
  lidar: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><path d="M12 12L18 6"/>',
  temperature: '<path d="M10 14.500V5a2 2 0 0 1 4 0v9.500a4 4 0 1 1-4 0z"/><path d="M12 9v7"/>',
};

const STATE_TEXT = { active: "Active", idle: "Tap to enable", unavailable: "Not available" };

function icon(id) {
  return '<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"' +
         ' stroke-linecap="round" stroke-linejoin="round">' + (ICONS[id] || "") + "</svg>";
}

// One tile. `s` is a sensor from the server: {id, label, state, reason, value, trust, rate_hz}.
function tileHTML(s, withBar) {
  const cls = s.state === "idle" ? "dark" : s.state;
  const bottom = s.state === "unavailable"
    ? '<div class="reason">' + (s.reason || "") + "</div>"
    : '<div class="value">' + (s.value || "&nbsp;") + "</div>" +
      (withBar && s.state === "active"
        ? '<div class="bar"><i style="width:' + Math.round((s.trust || 0) * 100) + '%"></i></div>' : "");
  return '<div class="tile ' + cls + '" data-id="' + s.id + '">' +
    icon(s.id) +
    "<div><div class=\"name\">" + s.label + "</div>" + bottom + "</div>" +
    '<span class="status">' + STATE_TEXT[s.state] + "</span></div>";
}
