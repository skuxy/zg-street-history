/* ============================================================
   Zagreb Street History — map.js
   MapLibre GL JS + FastAPI backend
   ============================================================ */

// API_BASE is set by config.js (loaded before this script)

// ---------------------------------------------------------------------------
// Map init
// ---------------------------------------------------------------------------

const map = new maplibregl.Map({
  container: "map",
  // OpenFreeMap "liberty" style — free vector tiles, no API key needed
  style: "https://tiles.openfreemap.org/styles/liberty",
  center: [15.982, 45.815],   // Zagreb
  zoom: 13,
  minZoom: 11,
  maxZoom: 19,
});

map.addControl(new maplibregl.NavigationControl(), "bottom-right");
map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

// ---------------------------------------------------------------------------
// Street layers (added after map style loads)
// ---------------------------------------------------------------------------

map.on("load", async () => {
  // Fetch streets GeoJSON (cached via ETag on server)
  let geojson;
  try {
    const res = await fetch(`${API_BASE}/api/streets`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    geojson = await res.json();
  } catch (e) {
    console.error("Failed to load streets:", e);
    return;
  }

  // Source
  map.addSource("streets", {
    type: "geojson",
    data: geojson,
    promoteId: "osm_id",   // use osm_id as feature state key
  });

  // Casing (glow effect for hovered street)
  map.addLayer({
    id: "streets-casing",
    type: "line",
    source: "streets",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": "#e8a838",
      "line-width": [
        "interpolate", ["linear"], ["zoom"],
        11, 3, 14, 6, 17, 12,
      ],
      "line-opacity": [
        "case",
        ["boolean", ["feature-state", "hover"], false], 0.55, 0,
      ],
      "line-blur": 4,
    },
  });

  // Main street line
  map.addLayer({
    id: "streets-line",
    type: "line",
    source: "streets",
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": [
        "case",
        ["boolean", ["feature-state", "hover"], false], "#e8a838", "#e8a83855",
      ],
      "line-width": [
        "interpolate", ["linear"], ["zoom"],
        11, 1, 14, 2.5, 17, 5,
      ],
    },
  });

  setupInteractions();
});

// ---------------------------------------------------------------------------
// Hover & click interactions
// ---------------------------------------------------------------------------

let hoveredId = null;
let popup = null;

function setupInteractions() {
  // Mouse enters a street
  map.on("mousemove", "streets-line", (e) => {
    if (!e.features.length) return;
    map.getCanvas().style.cursor = "pointer";

    const feature = e.features[0];
    const id = feature.id;   // promoted from osm_id

    if (id === hoveredId) return;

    // Clear previous hover
    if (hoveredId !== null) {
      map.setFeatureState({ source: "streets", id: hoveredId }, { hover: false });
    }
    hoveredId = id;
    map.setFeatureState({ source: "streets", id: hoveredId }, { hover: true });

    // Tooltip popup
    const name = feature.properties.name;
    if (popup) popup.remove();
    popup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 10,
      maxWidth: "240px",
    })
      .setLngLat(e.lngLat)
      .setHTML(`<strong>${escHtml(name)}</strong>`)
      .addTo(map);
  });

  // Mouse leaves streets layer
  map.on("mouseleave", "streets-line", () => {
    map.getCanvas().style.cursor = "";
    if (hoveredId !== null) {
      map.setFeatureState({ source: "streets", id: hoveredId }, { hover: false });
      hoveredId = null;
    }
    if (popup) { popup.remove(); popup = null; }
  });

  // Update popup position as mouse moves
  map.on("mousemove", "streets-line", (e) => {
    if (popup) popup.setLngLat(e.lngLat);
  });

  // Click → open panel
  map.on("click", "streets-line", (e) => {
    if (!e.features.length) return;
    const name = e.features[0].properties.name;
    openPanel(name);
  });
}

// ---------------------------------------------------------------------------
// Info panel
// ---------------------------------------------------------------------------

const panel      = document.getElementById("panel");
const closeBtn   = document.getElementById("panel-close");
const streetName = document.getElementById("street-name");
const wdLink     = document.getElementById("street-wikidata-link");
const loading    = document.getElementById("loading");
const noData     = document.getElementById("no-data");

const personSection = document.getElementById("person-section");
const personImage   = document.getElementById("person-image");
const personName    = document.getElementById("person-name");
const personDesc    = document.getElementById("person-desc");
const personSummary = document.getElementById("person-summary");
const personWikiHr  = document.getElementById("person-wiki-hr");
const personWikiEn  = document.getElementById("person-wiki-en");

const streetSection = document.getElementById("street-section");
const streetSummary = document.getElementById("street-summary");
const streetWikiHr  = document.getElementById("street-wiki-hr");
const streetWikiEn  = document.getElementById("street-wiki-en");

let currentName = null;
let wikiCache   = {};   // name → API response

closeBtn.addEventListener("click", closePanel);

function openPanel(name) {
  if (currentName === name) {
    // already showing — just make sure panel is visible
    panel.classList.remove("panel--hidden");
    return;
  }
  currentName = name;
  streetName.textContent = name;

  // Reset all sections
  setVisible(wdLink, false);
  setVisible(personSection, false);
  setVisible(streetSection, false);
  setVisible(noData, false);
  setVisible(loading, true);

  panel.classList.remove("panel--hidden");

  fetchWiki(name);
}

function closePanel() {
  panel.classList.add("panel--hidden");
  currentName = null;
}

async function fetchWiki(name) {
  if (wikiCache[name]) {
    renderWiki(wikiCache[name]);
    return;
  }
  try {
    const res = await fetch(`${API_BASE}/api/wiki?name=${encodeURIComponent(name)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    wikiCache[name] = data;
    renderWiki(data);
  } catch (e) {
    console.error("Wiki fetch failed:", e);
    setVisible(loading, false);
    setVisible(noData, true);
    noData.textContent = "Failed to load data. Is the backend running?";
  }
}

function renderWiki(data) {
  setVisible(loading, false);

  if (!data.found) {
    setVisible(noData, true);
    return;
  }

  // Wikidata badge
  if (data.wikidata_qid) {
    wdLink.href = `https://www.wikidata.org/wiki/${data.wikidata_qid}`;
    setVisible(wdLink, true);
  }

  // Named after
  const p = data.named_after;
  if (p) {
    personName.textContent = p.name || "";
    personDesc.textContent = p.description || "";

    if (p.image_url) {
      personImage.src = wikimediaThumb(p.image_url, 120);
      personImage.alt = p.name || "";
      setVisible(personImage, true);
    } else {
      setVisible(personImage, false);
    }

    const summary = p.summary_hr || p.summary_en || "";
    personSummary.textContent = summary;

    setLink(personWikiHr, p.wiki_url_hr, "Croatian Wikipedia");
    setLink(personWikiEn, p.wiki_url_en, "English Wikipedia");

    setVisible(personSection, true);
  }

  // Street article
  const s = data.street_article;
  if (s) {
    const summary = s.summary_hr || s.summary_en || "";
    streetSummary.textContent = summary;
    setLink(streetWikiHr, s.wiki_url_hr, "Croatian Wikipedia");
    setLink(streetWikiEn, s.wiki_url_en, "English Wikipedia");
    setVisible(streetSection, true);
  }

  // If nothing to show
  if (!p && !s) {
    setVisible(noData, true);
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function setVisible(el, visible) {
  el.style.display = visible ? "" : "none";
}

function setLink(el, url, label) {
  if (url) {
    el.href = url;
    setVisible(el, true);
  } else {
    setVisible(el, false);
  }
}

function escHtml(str) {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Convert Wikimedia Commons full image URL to a thumbnail URL. */
function wikimediaThumb(url, width = 120) {
  // https://upload.wikimedia.org/wikipedia/commons/a/ab/Filename.jpg
  // → https://upload.wikimedia.org/wikipedia/commons/thumb/a/ab/Filename.jpg/120px-Filename.jpg
  try {
    const u = new URL(url);
    const parts = u.pathname.split("/");
    // parts: ["", "wikipedia", "commons", "a", "ab", "Filename.jpg"]
    const filename = parts[parts.length - 1];
    const hash1 = parts[parts.length - 3];
    const hash2 = parts[parts.length - 2];
    return `https://upload.wikimedia.org/wikipedia/commons/thumb/${hash1}/${hash2}/${filename}/${width}px-${filename}`;
  } catch {
    return url;
  }
}
