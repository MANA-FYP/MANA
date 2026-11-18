// Places near the map (demo/places.py), for the live and indoor pages: a pin for each named place,
// coloured by its wheelchair tag in OpenStreetMap, a small mark for steps, kerbs and ramps, and a
// card on a tap. Asked only while the Places switch is on, and again only once the map has moved
// about 100 m (or a half answer is a minute old). Most places in Pakistan have no wheelchair tag:
// they are grey, "not tagged", never guessed.
"use strict";
const Places = (() => {
  const COLOUR = { yes: "#18794e", limited: "#b45309", no: "#c2252c", unknown: "#6b7280" };
  const WORD = { yes: "Wheelchair: yes", limited: "Wheelchair: limited", no: "Wheelchair: no", unknown: "Wheelchair: not tagged" };
  const DEG = 6371000 * Math.PI / 180;   // metres in a degree of latitude, as demo/navigation.py
  const st = { list: [], at: null, asked: 0, busy: false, whole: false, note: "", version: 0 };
  let onChange = () => {};
  const esc = (t) => String(t).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const away = (a, lat, lon) => Math.hypot((lat - a[0]) * DEG, (lon - a[1]) * DEG * Math.cos(lat * Math.PI / 180));

  function want(lat, lon) {
    if (st.busy || !isFinite(lat) || !isFinite(lon)) return;
    if (st.at && away(st.at, lat, lon) < 100 && (st.whole || Date.now() - st.asked < 60000)) return;
    load(lat, lon);
  }
  async function load(lat, lon) {
    Object.assign(st, { busy: true, at: [lat, lon], asked: Date.now(), note: "Finding places nearby…" });
    onChange();
    try {
      const r = await fetch("/api/places?lat=" + lat.toFixed(5) + "&lon=" + lon.toFixed(5));
      if (r.status !== 200) throw new Error(r.status);
      const m = await r.json();
      const named = m.places.filter((p) => !p.small).length, small = m.places.length - named;
      const tagged = m.places.filter((p) => !p.small && p.wheelchair !== "unknown").length;
      st.list = m.places; st.whole = m.sources.osm && m.sources.wikipedia;
      st.note = named + " places, " + tagged + " with a wheelchair tag" + (small ? ", " + small + " steps, kerbs or ramps" : "") +
        (m.offline ? " (the laptop's copy: offline)" : "") +
        (!m.sources.osm ? ". OpenStreetMap is busy: trying again" : !m.sources.wikipedia ? ". Wikipedia not reached" : "");
    } catch (e) {
      st.whole = false; st.note = "Places not reached: no internet, or the service is busy. Trying again in a minute";
    }
    st.busy = false; st.version++; onChange();
  }

  // the pins, with project(lat, lon) -> [x, y] on the canvas; returns what was drawn, for hit()
  function draw(ctx, project, w, h) {
    const drawn = [];
    const ring = (fill) => { ctx.fillStyle = fill; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = "#fff"; ctx.stroke(); };
    st.list.forEach((p) => {
      const [x, y] = project(p.lat, p.lon);
      if (x < -10 || y < -10 || x > w + 10 || y > h + 10) return;
      ctx.beginPath();
      if (p.kind === "steps") ctx.rect(x - 4.5, y - 4.5, 9, 9);   // steps: a square
      else if (p.small) { ctx.moveTo(x, y - 6); ctx.lineTo(x + 6, y); ctx.lineTo(x, y + 6); ctx.lineTo(x - 6, y); ctx.closePath(); }   // kerb, ramp: a diamond
      else ctx.arc(x, y, 7, 0, 7);
      ring(p.small ? "#1c2024" : COLOUR[p.wheelchair] || COLOUR.unknown);
      if (p.source === "wikipedia") { ctx.fillStyle = "#fff"; ctx.font = "700 9px sans-serif"; ctx.textAlign = "center"; ctx.fillText("W", x, y + 3.2); ctx.textAlign = "left"; }
      drawn.push([x, y, p]);
    });
    return drawn;
  }
  function hit(drawn, x, y) {
    let best = null, d = 18;
    (drawn || []).forEach(([px, py, p]) => { const e = Math.hypot(px - x, py - y); if (e < d) { d = e; best = p; } });
    return best;
  }
  // the photo's author and licence, linked to its file page on Commons; a missing one is left out
  function photoCredit(p) {
    const who = [p.photo_artist, p.photo_licence].filter(Boolean).map(esc).join(", ");
    const text = "Photo: " + (who || "Wikimedia Commons");
    return p.photo_file ? '<a href="https://commons.wikimedia.org/wiki/File:' + encodeURIComponent(p.photo_file) +
      '" target="_blank" rel="noopener">' + text + "</a>" : text;
  }
  function card(el, p) {
    if (!p) { el.hidden = true; el.innerHTML = ""; return; }
    const link = (url, text) => url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + text + "</a>" : text;
    el.innerHTML = '<button class="pc-x" title="Close" aria-label="Close">&times;</button>' +
      (p.photo ? '<img alt="" src="/api/photo?u=' + encodeURIComponent(p.photo) + '" onerror="this.remove()">' : "") +
      "<b>" + esc(p.name) + '</b><span class="pc-kind">' + esc(p.kind) + "</span>" +
      '<span class="pc-wc"><i style="background:' + (p.small ? "#1c2024" : COLOUR[p.wheelchair]) + '"></i>' + WORD[p.wheelchair] + "</span>" +
      (p.detail ? '<span class="pc-detail">' + esc(p.detail) + "</span>" : "") +
      "<small>" + (p.photo ? photoCredit(p) + " · " : "") +
      (p.source === "osm" ? link(p.page, "Map data © OpenStreetMap contributors") : link(p.page, "From Wikipedia")) + "</small>";
    el.querySelector(".pc-x").onclick = () => card(el, null);
    el.hidden = false;
  }
  function legend(el, on) {
    el.hidden = !on;
    if (!on) return;
    const dot = (c, t) => '<span><i style="background:' + c + '"></i>' + t + "</span>";
    const html = '<div class="pl-keys">' + dot(COLOUR.yes, "yes") + dot(COLOUR.limited, "limited") + dot(COLOUR.no, "no") +
      dot(COLOUR.unknown, "not tagged") + '<span><i class="sq"></i>steps</span><span><i class="dm"></i>kerb, ramp</span></div>' +
      '<div class="pl-note">' + esc(st.note) + "</div>";
    if (el.innerHTML !== html) el.innerHTML = html;
  }
  // the credits of every layer drawn
  function credit(layer, labels, places) {
    const c = [];
    if (layer === "satellite") c.push("Imagery: Esri, Maxar, Earthstar Geographics");
    if (layer === "satellite" && labels) c.push("Labels: Esri, HERE, Garmin, and the GIS user community");
    if (layer === "streets" || (layer === "satellite" && labels) || places) c.push("© OpenStreetMap contributors");
    if (places) c.push("Wikipedia");
    return c.join(" · ");
  }
  return { want, draw, hit, card, legend, credit, state: st, set onChange(f) { onChange = f; } };
})();
