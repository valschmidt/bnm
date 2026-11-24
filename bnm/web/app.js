/* Notices to Mariners map: loads data/notices.geojson and renders it with Leaflet. */
(function () {
  "use strict";

  const DISTRICTS = {
    1: "D1 Northeast", 5: "D5 East", 7: "D7 Southeast", 8: "D8 Heartland", 9: "D9 Great Lakes",
    11: "D11 Southwest", 13: "D13 Northwest", 14: "D14 Oceania", 17: "D17 Arctic",
  };
  const PALETTE = ["#d62728", "#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#8c564b", "#e377c2",
    "#17becf", "#bcbd22", "#7f7f7f", "#393b79", "#ad494a", "#637939", "#8c6d31", "#843c39",
    "#7b4173", "#3182bd", "#e6550d", "#31a354", "#756bb1"];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const store = {
    get(k) { try { return JSON.parse(localStorage.getItem("bnm:" + k)); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem("bnm:" + k, JSON.stringify(v)); } catch (e) { /* ignore */ } },
  };

  // ------------------------------------------------------------------ map
  const map = L.map("map", { preferCanvas: true, worldCopyJump: true });
  // View comes from the URL (#zoom/lat/lon, shareable), else the last visit, else CONUS
  const fromHash = () => {
    const m = location.hash.match(/^#(\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)\/(-?\d+(?:\.\d+)?)/);
    return m && { z: +m[1], c: [+m[2], +m[3]] };
  };
  const saved = fromHash() || store.get("view");
  if (saved) map.setView(saved.c, saved.z); else map.setView([38, -95], 4);
  map.on("moveend", () => {
    const c = map.getCenter(), z = map.getZoom();
    store.set("view", { c, z });
    history.replaceState(null, "", `#${z}/${c.lat.toFixed(4)}/${c.lng.toFixed(4)}`);
  });
  window.addEventListener("hashchange", () => { const v = fromHash(); if (v) map.setView(v.c, v.z); });

  const osm = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  const enc = L.tileLayer.wms(
    "https://gis.charttools.noaa.gov/arcgis/rest/services/MCS/NOAAChartDisplay/MapServer/exts/MaritimeChartService/WMSServer",
    { layers: "0,1,2,3,4,5,6,7", format: "image/png", transparent: true, version: "1.3.0",
      maxZoom: 19, attribution: "NOAA ENC&reg;" });
  L.control.layers({ "OpenStreetMap": osm }, { "NOAA nautical chart (ENC)": enc }, { collapsed: true }).addTo(map);
  L.control.scale({ imperial: true, metric: true }).addTo(map);

  const clusters = L.markerClusterGroup({ chunkedLoading: true, maxClusterRadius: 45, showCoverageOnHover: false });
  const shapes = L.featureGroup();
  map.addLayer(shapes).addLayer(clusters);

  // ------------------------------------------------------------------ state
  let features = [];
  const colorOf = {};
  const state = {
    q: "", district: "", date: "",
    cats: new Set(), sources: new Set(),
  };

  function fmtDate(s) { return s ? s.slice(0, 10) : ""; }

  function popupHtml(p) {
    const dates = [p.begin && "from " + fmtDate(p.begin), p.end && "to " + fmtDate(p.end)].filter(Boolean).join(" ");
    const meta = [p.source + " · " + p.label, DISTRICTS[p.district] || (p.district ? "D" + p.district : ""),
      p.waterway, dates, p.llnr && "LLNR " + p.llnr, p.bnm && "BNM " + p.bnm].filter(Boolean);
    return `<div class="popup"><h3>${esc(p.title)}</h3><div class="meta">${meta.map(esc).join(" · ")}</div>` +
      `<pre>${esc(p.text)}</pre>` +
      (p.url ? `<div class="meta" style="margin-top:6px"><a href="${esc(p.url)}" target="_blank" rel="noopener">Source</a></div>` : "") +
      `</div>`;
  }

  function matches(f) {
    const p = f.properties;
    if (!state.cats.has(p.category) || !state.sources.has(p.source)) return false;
    if (state.district && String(p.district) !== state.district) return false;
    if (state.date) {
      const d = state.date;
      if (p.begin && fmtDate(p.begin) > d) return false;
      if (p.end && fmtDate(p.end) < d) return false;
    }
    if (state.q) {
      const hay = f._hay || (f._hay = [p.title, p.text, p.waterway, p.llnr, p.bnm, p.label].join(" ").toLowerCase());
      for (const w of state.q.split(/\s+/)) if (w && !hay.includes(w)) return false;
    }
    return true;
  }

  function render() {
    clusters.clearLayers();
    shapes.clearLayers();
    const pts = [];
    let n = 0;
    for (const f of features) {
      if (!matches(f)) continue;
      n++;
      const p = f.properties, color = colorOf[p.category];
      const t = f.geometry.type;
      if (t === "Point") {
        const [x, y] = f.geometry.coordinates;
        pts.push(L.circleMarker([y, x], { radius: 6, color: "#222", weight: 1, fillColor: color, fillOpacity: 0.9 })
          .bindPopup(() => popupHtml(p), { maxWidth: 360 }));
      } else {
        // Region-sized areas (e.g. district-wide MSIBs) get a faint fill so they don't hide the map
        if (f._big === undefined) {
          const b = L.geoJSON(f).getBounds();
          f._big = b.isValid() && (b.getNorth() - b.getSouth()) * (b.getEast() - b.getWest()) > 25;
        }
        L.geoJSON(f, {
          style: { color, weight: f._big ? 1.5 : 2, fillOpacity: f._big ? 0.03 : 0.15, dashArray: f._big ? "6 4" : null },
          pointToLayer: (_, ll) => L.circleMarker(ll, { radius: 5, color, fillOpacity: 0.8 }),
        }).bindPopup(() => popupHtml(p), { maxWidth: 360 }).addTo(shapes);
      }
    }
    clusters.addLayers(pts);
    $("count").textContent = n.toLocaleString();
    store.set("filters", { q: state.q, district: state.district, cats: [...state.cats], sources: [...state.sources] });
  }

  function checkbox(container, value, text, count, color, checked, onChange) {
    const lab = document.createElement("label");
    lab.className = "cb";
    lab.innerHTML = `<input type="checkbox" ${checked ? "checked" : ""}>` +
      (color ? `<span class="sw" style="background:${color}"></span>` : "") +
      `<span>${esc(text)}</span><span class="n">${count}</span>`;
    lab.querySelector("input").addEventListener("change", (e) => onChange(value, e.target.checked));
    container.appendChild(lab);
  }

  function buildControls() {
    const saved = store.get("filters") || {};
    const cats = {}, sources = {}, districts = {};
    for (const f of features) {
      const p = f.properties;
      (cats[p.category] = cats[p.category] || { label: p.label, n: 0 }).n++;
      sources[p.source] = (sources[p.source] || 0) + 1;
      if (p.district != null) districts[p.district] = (districts[p.district] || 0) + 1;
    }
    const catKeys = Object.keys(cats).sort((a, b) => cats[a].label.localeCompare(cats[b].label));
    catKeys.forEach((k, i) => { colorOf[k] = PALETTE[i % PALETTE.length]; });

    const savedCats = saved.cats ? new Set(saved.cats) : null;
    const savedSrc = saved.sources ? new Set(saved.sources) : null;
    const toggle = (set) => (v, on) => { on ? set.add(v) : set.delete(v); render(); };

    $("cats").innerHTML = "";
    for (const k of catKeys) {
      const on = !savedCats || savedCats.has(k);
      if (on) state.cats.add(k);
      checkbox($("cats"), k, cats[k].label, cats[k].n, colorOf[k], on, toggle(state.cats));
    }
    $("sources").innerHTML = "";
    for (const s of Object.keys(sources).sort()) {
      const on = !savedSrc || savedSrc.has(s);
      if (on) state.sources.add(s);
      checkbox($("sources"), s, s, sources[s], null, on, toggle(state.sources));
    }
    for (const d of Object.keys(districts).sort((a, b) => a - b)) {
      const o = document.createElement("option");
      o.value = d;
      o.textContent = `${DISTRICTS[d] || "D" + d} (${districts[d]})`;
      $("district").appendChild(o);
    }
    if (saved.district) { state.district = saved.district; $("district").value = saved.district; }
    if (saved.q) { state.q = saved.q; $("q").value = saved.q; }
  }

  function setAllCats(on) {
    document.querySelectorAll("#cats input").forEach((cb) => { cb.checked = on; });
    state.cats = on ? new Set(Object.keys(colorOf)) : new Set();
    render();
  }

  let timer;
  $("q").addEventListener("input", (e) => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = e.target.value.trim().toLowerCase(); render(); }, 200);
  });
  $("district").addEventListener("change", (e) => { state.district = e.target.value; render(); });
  $("date").addEventListener("change", (e) => { state.date = e.target.value; render(); });
  $("all").addEventListener("click", () => setAllCats(true));
  $("none").addEventListener("click", () => setAllCats(false));
  $("toggle").addEventListener("click", () => $("panel").classList.toggle("collapsed"));

  // ------------------------------------------------------------------ load
  const bust = "?t=" + Math.floor(Date.now() / 600000); // refresh at most every 10 min
  fetch("data/manifest.json" + bust).then((r) => r.json()).then((m) => {
    $("updated").textContent = "updated " + new Date(m.generated).toLocaleString();
    const bad = Object.entries(m.sources || {}).filter(([, s]) => !s.ok || (s.errors && s.errors.length));
    if (bad.length) {
      $("errors").textContent = "Some data may be out of date: " +
        bad.map(([k, s]) => `${k.toUpperCase()}${s.ok ? " (partial)" : " (last good " + fmtDate(s.updated) + ")"}`).join(", ");
    }
    const unmapped = (((m.sources || {}).bnm || {}).stats || {}).unlocated || [];
    if (unmapped.length) {
      $("unmapped").innerHTML = unmapped.map((s) => `<li>${esc(s)}</li>`).join("");
      $("unmapped-box").hidden = false;
    }
  }).catch(() => { $("updated").textContent = "update time unknown"; });

  fetch("data/notices.geojson" + bust).then((r) => r.json()).then((fc) => {
    features = fc.features.filter((f) => f.geometry);
    buildControls();
    render();
  }).catch((e) => { $("errors").textContent = "Could not load notices: " + e; });
})();
