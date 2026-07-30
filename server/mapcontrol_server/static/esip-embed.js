/* ─────────────────────────────────────────────────────────────────────────
   ESIP Default Outfit — the optional built-in management UI ("dressed" mode).

   PHILOSOPHY (read this before editing)
   -------------------------------------
   The ESIP map is, by design, NAKED: a chrome-less canvas that emits
   interaction events into the void and accepts commands through a public
   contract. This file is a *reference consumer* of that exact contract — it
   uses nothing privileged. If something the outfit wants isn't reachable
   through `window.ESIPMap` or the public REST API, that's a hole in the
   contract, not a license to reach inside the map. Keeping the default UI
   honest this way is how we prove the contract is good enough for anyone to
   vibe-code their own UI against (that's "bespoke" / tier 3).

   It is loaded ONLY when the map is served with ?ui=default. The naked map
   (?ui=none) never loads this file.

   The public contract it relies on (provided by the inline map shell):
     window.ESIPMap = {
       config:   { mapId, userSession, baseUrl, theme },
       getBasemaps(): { id: { label, kind, thumbnail, group, ... } },
       getCurrentBasemap(): string,
       getRegistry(): { asset_id: { layerIds, visible, bounds } },
       actions: {
         setVisibility(assetId, visible),
         deleteAsset(assetId),
         setOpacity(assetId, opacity01),
         moveLayer(assetId, 'top'|'bottom'|'up'|'down'),
         zoomToAssets([assetId]),
         setBasemap(id),
       },
     }
   Plus window CustomEvents, all namespaced `esip:`:
     esip:ready, esip:assetschanged,
     esip:asset_hover {asset_id,name,asset_type,point},
     esip:asset_hover_end {asset_id},
     esip:asset_click {asset_id,name,asset_type,point},
     esip:map_click {lon,lat}
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  // ─── Icons (inline SVG, currentColor) ──────────────────────────────────
  var ICON = {
    gear: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>',
    close: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
    eyeOff: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    target: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/><line x1="12" y1="2" x2="12" y2="5"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="2" y1="12" x2="5" y2="12"/><line x1="19" y1="12" x2="22" y2="12"/></svg>',
    polygon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l8 5-3 9H7L4 8z"/></svg>',
    path: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M4 19c4-1 4-9 8-10s4 8 8 7"/></svg>',
    point: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="5"/></svg>',
    raster: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M3 15h18M9 3v18M15 3v18"/></svg>',
    pencil: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>',
  };

  // ─── Group classification ───────────────────────────────────────────────
  // The "make distinctions for sure" rule: every layer is sorted into one of
  // these buckets, and each bucket gets its own affordances.
  var GROUPS = [
    { key: "analysis", label: "Analysis Layers" },
    { key: "drawn", label: "Drawn" },
    { key: "basemap", label: "Basemap" },
  ];

  function groupForType(assetType) {
    var t = (assetType || "").toLowerCase();
    if (t.indexOf("drawn") === 0) return "drawn";
    return "analysis";
  }

  function iconForType(assetType) {
    var t = (assetType || "").toLowerCase();
    if (t.indexOf("geotiff") === 0 || t.indexOf("tile") !== -1 || t.indexOf("raster") !== -1) return ICON.raster;
    if (t.indexOf("drawn") === 0) return ICON.pencil;
    if (t.indexOf("path") !== -1 || t.indexOf("line") !== -1) return ICON.path;
    if (t.indexOf("point") !== -1) return ICON.point;
    return ICON.polygon;
  }

  // ─── State ──────────────────────────────────────────────────────────────
  var M = null; // window.ESIPMap contract
  var assetCache = []; // last-known Asset[] from REST (public API)
  var assetById = {}; // asset_id -> Asset
  var expandedId = null; // which row is expanded (click-to-focus)
  var els = {}; // cached DOM refs

  // ─── Tiny helpers ───────────────────────────────────────────────────────
  function el(tag, cls, html) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  // Minimal, safe markdown → HTML (bold, italic, inline code, links, breaks).
  // Deliberately tiny: producer-supplied card_md should stay short. Escapes
  // first, so no raw HTML injection from asset metadata.
  function miniMarkdown(src) {
    var s = esc(src);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, function (_, txt, url) {
      return '<a href="' + url + '" target="_blank" rel="noopener">' + txt + "</a>";
    });
    s = s.replace(/\n/g, "<br>");
    return s;
  }

  // ─── Geometry stats (computed client-side from public geojson) ──────────
  function parseGeo(g) {
    if (!g) return null;
    if (typeof g === "string") { try { return JSON.parse(g); } catch (e) { return null; } }
    return g;
  }
  function geomOf(gj) {
    if (!gj) return null;
    if (gj.type === "Feature") return gj.geometry;
    if (gj.type === "FeatureCollection") return (gj.features && gj.features[0] && gj.features[0].geometry) || null;
    return gj;
  }
  // Spherical polygon area (m²) via the shoelace-on-sphere approximation.
  function ringAreaM2(ring) {
    var R = 6378137, area = 0;
    for (var i = 0, n = ring.length; i < n; i++) {
      var p1 = ring[i], p2 = ring[(i + 1) % n];
      area += (p2[0] - p1[0]) * Math.PI / 180 *
        (2 + Math.sin(p1[1] * Math.PI / 180) + Math.sin(p2[1] * Math.PI / 180));
    }
    return Math.abs(area * R * R / 2);
  }
  function lineLengthM(coords) {
    var R = 6371000, total = 0;
    for (var i = 1; i < coords.length; i++) {
      var a = coords[i - 1], b = coords[i];
      var dLat = (b[1] - a[1]) * Math.PI / 180, dLon = (b[0] - a[0]) * Math.PI / 180;
      var la1 = a[1] * Math.PI / 180, la2 = b[1] * Math.PI / 180;
      var h = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(la1) * Math.cos(la2) * Math.sin(dLon / 2) * Math.sin(dLon / 2);
      total += 2 * R * Math.asin(Math.sqrt(h));
    }
    return total;
  }
  function fmtArea(m2) {
    if (m2 >= 1e6) return (m2 / 1e6).toFixed(m2 >= 1e7 ? 0 : 1) + " km²";
    return Math.round(m2).toLocaleString() + " m²";
  }
  function fmtLen(m) {
    if (m >= 1000) return (m / 1000).toFixed(m >= 10000 ? 0 : 1) + " km";
    return Math.round(m) + " m";
  }
  // One free, human-readable stat from geometry. Returns "" if none applies.
  function autoStat(asset) {
    var gj = parseGeo(asset.geojson);
    var geom = geomOf(gj);
    if (!geom) return "";
    try {
      if (geom.type === "Polygon") return fmtArea(ringAreaM2(geom.coordinates[0]));
      if (geom.type === "MultiPolygon") {
        var a = 0; geom.coordinates.forEach(function (poly) { a += ringAreaM2(poly[0]); });
        return fmtArea(a);
      }
      if (geom.type === "LineString") return fmtLen(lineLengthM(geom.coordinates));
      if (geom.type === "MultiLineString") {
        var l = 0; geom.coordinates.forEach(function (ls) { l += lineLengthM(ls); });
        return fmtLen(l);
      }
      if (geom.type === "Point") return geom.coordinates[1].toFixed(3) + ", " + geom.coordinates[0].toFixed(3);
    } catch (e) { /* malformed geometry — no stat */ }
    return "";
  }

  // ─── Card content resolution (producer overrides → auto fallback) ───────
  // Precedence: metadata.card_md / metadata.thumbnail win; else auto-generate
  // from name + type + style swatch + one geometry stat + description. The
  // card is therefore NEVER empty even when a producer set nothing — which is
  // the whole point: it proves the bare contract is humane on its own.
  function cardModel(asset) {
    var meta = asset.metadata || {};
    var extra = meta.extra || {};
    var style = asset.style || {};
    var name = asset.name || meta.title || ("Untitled " + prettyType(asset.asset_type));
    var thumb = meta.thumbnail || extra.thumbnail || rasterThumb(asset);
    var swatchColor = style.fill_color || style.stroke_color || null;
    var md = meta.card_md || extra.card_md || meta.description || "";
    var stat = autoStat(asset);
    return {
      name: name,
      type: prettyType(asset.asset_type),
      thumb: thumb,
      swatch: swatchColor,
      icon: iconForType(asset.asset_type),
      md: md,
      stat: stat,
    };
  }
  function prettyType(t) {
    t = (t || "layer").toLowerCase().replace(/^drawn_/, "drawn ").replace(/_/g, " ");
    if (t.indexOf("geotiff") === 0) return "raster";
    return t;
  }
  // Rasters already have a server-rendered PNG overlay — reuse it as the card
  // visual. (Public file route; no new server work.)
  function rasterThumb(asset) {
    var t = (asset.asset_type || "").toLowerCase();
    if (t.indexOf("geotiff") === 0) {
      return M.config.baseUrl + "/api/files/" + asset.asset_id + ".png";
    }
    return null;
  }

  // ─── REST: fetch the authoritative asset list (public API) ──────────────
  function refreshAssets() {
    var url = M.config.baseUrl + "/api/maps/" + M.config.mapId + "/assets";
    return fetch(url)
      .then(function (r) { return r.ok ? r.json() : []; })
      .then(function (list) {
        assetCache = Array.isArray(list) ? list : [];
        assetById = {};
        assetCache.forEach(function (a) { assetById[a.asset_id] = a; });
        renderLayers();
      })
      .catch(function () { /* offline / race — keep last cache */ });
  }

  // Local view of visibility comes from the live registry (per-session view
  // state), falling back to the asset's persisted `visible`.
  function isVisible(asset) {
    var reg = M.getRegistry ? M.getRegistry() : {};
    var r = reg[asset.asset_id];
    if (r && typeof r.visible === "boolean") return r.visible;
    return asset.visible !== false;
  }

  // ═══════════════════════════════════════════════════════════════════════
  //  DOM construction
  // ═══════════════════════════════════════════════════════════════════════
  function build() {
    var root = el("div", "esip-ui");
    root.setAttribute("data-esip-root", "1");

    // Gear (the single always-present affordance — lets a standalone user
    // open the panel; embedders that want true-zero-chrome use ?ui=none).
    var gear = el("button", "esip-gear");
    gear.title = "Map controls";
    gear.innerHTML = ICON.gear;
    gear.addEventListener("click", function () { togglePanel(true); });
    els.gear = gear;

    // Panel shell
    var panel = el("div", "esip-panel");
    panel.hidden = true;
    var head = el("div", "esip-panel-head");
    head.appendChild(el("div", "esip-panel-title", "Map"));
    var closeBtn = el("button", "esip-iconbtn");
    closeBtn.title = "Collapse";
    closeBtn.innerHTML = ICON.close;
    closeBtn.addEventListener("click", function () { togglePanel(false); });
    head.appendChild(closeBtn);
    panel.appendChild(head);

    // Tabs
    var tabs = el("div", "esip-tabs");
    var layersTab = mkTab("Layers", true);
    var settingsTab = mkTab("Settings", false);
    tabs.appendChild(layersTab);
    tabs.appendChild(settingsTab);
    panel.appendChild(tabs);

    var layersPanel = el("div", "esip-tabpanel");
    var settingsPanel = el("div", "esip-tabpanel");
    settingsPanel.hidden = true;
    panel.appendChild(layersPanel);
    panel.appendChild(settingsPanel);

    function selectTab(which) {
      var isLayers = which === "layers";
      layersTab.setAttribute("aria-selected", String(isLayers));
      settingsTab.setAttribute("aria-selected", String(!isLayers));
      layersPanel.hidden = !isLayers;
      settingsPanel.hidden = isLayers;
    }
    layersTab.addEventListener("click", function () { selectTab("layers"); });
    settingsTab.addEventListener("click", function () { selectTab("settings"); });

    els.panel = panel;
    els.layersPanel = layersPanel;
    els.settingsPanel = settingsPanel;

    // Hover card
    var card = el("div", "esip-hovercard");
    card.hidden = true;
    els.card = card;

    document.body.appendChild(gear);
    document.body.appendChild(panel);
    document.body.appendChild(card);

    renderSettings();
  }

  function mkTab(label, selected) {
    var t = el("button", "esip-tab", label);
    t.setAttribute("role", "tab");
    t.setAttribute("aria-selected", String(!!selected));
    return t;
  }

  function togglePanel(open) {
    if (open) {
      els.panel.hidden = false;
      els.gear.style.display = "none";
      refreshAssets();
    } else {
      els.panel.hidden = true;
      els.gear.style.display = "";
    }
  }

  // ─── Layers tab render ──────────────────────────────────────────────────
  function renderLayers() {
    if (!els.layersPanel) return;
    var container = els.layersPanel;
    clear(container);

    var buckets = { analysis: [], drawn: [], basemap: [] };
    assetCache.forEach(function (a) { buckets[groupForType(a.asset_type)].push(a); });

    GROUPS.forEach(function (g) {
      if (g.key === "basemap") { container.appendChild(renderBasemapGroup()); return; }
      var items = buckets[g.key];
      var group = el("div", "esip-group");
      var label = el("div", "esip-group-label");
      label.appendChild(el("span", null, g.label));
      label.appendChild(el("span", "esip-group-count", String(items.length)));
      group.appendChild(label);
      if (items.length === 0) {
        group.appendChild(el("div", "esip-empty", "Nothing here yet."));
      } else {
        items.forEach(function (a) { group.appendChild(renderRow(a)); });
      }
      container.appendChild(group);
    });
  }

  function renderRow(asset) {
    var model = cardModel(asset);
    var row = el("div", "esip-row");
    if (asset.asset_id === expandedId) row.classList.add("esip-active");

    var main = el("div", "esip-row-main");

    // Swatch / thumbnail visual
    var swatch = el("div", "esip-swatch");
    if (model.thumb) swatch.style.backgroundImage = 'url("' + model.thumb + '")';
    else if (model.swatch) swatch.style.background = model.swatch;
    else swatch.innerHTML = '<div style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;opacity:.5">' + model.icon + "</div>";
    main.appendChild(swatch);

    // Text (click → focus/expand)
    var text = el("div", "esip-row-text");
    text.appendChild(el("div", "esip-row-name", esc(model.name)));
    var sub = model.type + (model.stat ? " · " + model.stat : "");
    text.appendChild(el("div", "esip-row-sub", esc(sub)));
    text.addEventListener("click", function () { toggleExpand(asset.asset_id); });
    main.appendChild(text);

    // Per-row actions
    var actions = el("div", "esip-row-actions");
    var visible = isVisible(asset);
    var eye = el("button", "esip-iconbtn" + (visible ? "" : " esip-eye-off"));
    eye.title = visible ? "Hide" : "Show";
    eye.innerHTML = visible ? ICON.eye : ICON.eyeOff;
    eye.addEventListener("click", function (e) {
      e.stopPropagation();
      M.actions.setVisibility(asset.asset_id, !visible);
      renderLayers();
    });
    actions.appendChild(eye);

    var zoom = el("button", "esip-iconbtn");
    zoom.title = "Zoom to";
    zoom.innerHTML = ICON.target;
    zoom.addEventListener("click", function (e) {
      e.stopPropagation();
      M.actions.zoomToAssets([asset.asset_id]);
    });
    actions.appendChild(zoom);

    var del = el("button", "esip-iconbtn esip-danger");
    del.title = "Delete";
    del.innerHTML = ICON.trash;
    del.addEventListener("click", function (e) {
      e.stopPropagation();
      M.actions.deleteAsset(asset.asset_id);
      // optimistic: drop locally; broadcast will reconcile
      assetCache = assetCache.filter(function (x) { return x.asset_id !== asset.asset_id; });
      delete assetById[asset.asset_id];
      renderLayers();
    });
    actions.appendChild(del);

    main.appendChild(actions);
    row.appendChild(main);

    // Expanded detail (click-to-focus, no modal)
    var detail = el("div", "esip-row-detail");
    detail.hidden = asset.asset_id !== expandedId;
    if (model.md) detail.appendChild(el("div", "esip-detail-md", miniMarkdown(model.md)));
    var opRow = el("div", "esip-opacity-row");
    opRow.appendChild(el("label", null, "Opacity"));
    var slider = el("input");
    slider.type = "range"; slider.min = "0"; slider.max = "100"; slider.value = "100";
    slider.addEventListener("input", function () {
      M.actions.setOpacity(asset.asset_id, Number(slider.value) / 100);
    });
    opRow.appendChild(slider);
    detail.appendChild(opRow);
    row.appendChild(detail);

    return row;
  }

  function toggleExpand(assetId) {
    expandedId = expandedId === assetId ? null : assetId;
    renderLayers();
  }

  function renderBasemapGroup() {
    var group = el("div", "esip-group");
    var label = el("div", "esip-group-label");
    label.appendChild(el("span", null, "Basemap"));
    group.appendChild(label);
    var basemaps = M.getBasemaps ? M.getBasemaps() : {};
    var current = M.getCurrentBasemap ? M.getCurrentBasemap() : null;
    var ids = Object.keys(basemaps);
    if (ids.length === 0) {
      group.appendChild(el("div", "esip-empty", "No basemaps configured."));
      return group;
    }
    ids.forEach(function (id) {
      var entry = basemaps[id];
      var rowLabel = el("label", "esip-basemap");
      var radio = el("input");
      radio.type = "radio"; radio.name = "esip-basemap"; radio.checked = id === current;
      radio.addEventListener("change", function () { M.actions.setBasemap(id); });
      rowLabel.appendChild(radio);
      var thumb = el("span", "esip-basemap-thumb");
      if (entry.thumbnail) thumb.style.backgroundImage = 'url("' + entry.thumbnail + '")';
      rowLabel.appendChild(thumb);
      rowLabel.appendChild(el("span", "esip-row-name", esc(entry.label || id)));
      group.appendChild(rowLabel);
    });
    return group;
  }

  // ─── Settings tab render ────────────────────────────────────────────────
  function renderSettings() {
    var p = els.settingsPanel;
    clear(p);

    // The "Default UI" toggle. Turning it off reloads into naked mode
    // (?ui=none) — the humane, in-map path back to bare. Note the lovely
    // recursion: this toggle is itself chrome, so naked is reached by
    // *navigating away from* the chrome, not by hiding it in place.
    p.appendChild(mkSetting(
      "Default UI",
      "Show the built-in layer panel & hover cards. Turn off for a bare map.",
      true,
      function (on) {
        var u = new URL(window.location.href);
        u.searchParams.set("ui", on ? "default" : "none");
        window.location.href = u.toString();
      }
    ));

    // Theme echo (read-only convenience; theme is a shell concern via ?theme)
    var themeWrap = el("div", "esip-setting");
    var tt = el("div", "esip-setting-text");
    tt.appendChild(el("div", "esip-setting-title", "Theme"));
    tt.appendChild(el("div", "esip-setting-desc", "Set via the embedding page (?theme=light|dark)."));
    themeWrap.appendChild(tt);
    themeWrap.appendChild(el("div", "esip-setting-title", esc((M.config.theme || "light"))));
    p.appendChild(themeWrap);
  }

  function mkSetting(title, desc, checked, onChange) {
    var wrap = el("div", "esip-setting");
    var text = el("div", "esip-setting-text");
    text.appendChild(el("div", "esip-setting-title", title));
    text.appendChild(el("div", "esip-setting-desc", desc));
    wrap.appendChild(text);
    var sw = el("label", "esip-switch");
    var input = el("input");
    input.type = "checkbox"; input.checked = checked;
    input.addEventListener("change", function () { onChange(input.checked); });
    sw.appendChild(input);
    sw.appendChild(el("span", "esip-switch-slider"));
    wrap.appendChild(sw);
    return wrap;
  }

  // ─── Hover card (peek) ──────────────────────────────────────────────────
  var hideTimer = null;
  function showCard(detail) {
    var asset = assetById[detail.asset_id];
    // If the asset isn't in our cache yet (race), synthesize a minimal model
    // from the hover payload so the card is still useful.
    var model = asset ? cardModel(asset) : {
      name: detail.name || "Untitled",
      type: prettyType(detail.asset_type),
      thumb: null, swatch: null, icon: iconForType(detail.asset_type), md: "", stat: "",
    };
    var card = els.card;
    clear(card);

    var visual = el("div", "esip-hovercard-visual");
    if (model.thumb) visual.style.backgroundImage = 'url("' + model.thumb + '")';
    else if (model.swatch) visual.style.background = model.swatch;
    else visual.innerHTML = model.icon;
    card.appendChild(visual);

    var body = el("div", "esip-hovercard-body");
    body.appendChild(el("div", "esip-hovercard-type", esc(model.type)));
    body.appendChild(el("div", "esip-hovercard-title", esc(model.name)));
    if (model.stat) body.appendChild(el("div", "esip-hovercard-stat", esc(model.stat)));
    if (model.md) body.appendChild(el("div", "esip-hovercard-desc", miniMarkdown(model.md)));
    card.appendChild(body);

    positionCard(detail.point);
    card.hidden = false;
    // next frame → fade in
    requestAnimationFrame(function () { card.classList.add("esip-show"); });
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
  }

  function positionCard(point) {
    var card = els.card;
    var x = (point && point.x != null) ? point.x : 20;
    var y = (point && point.y != null) ? point.y : 20;
    // Default: below-right of cursor; flip if near the viewport edge.
    card.style.left = "0px"; card.style.top = "0px";
    var w = 280, h = 100;
    var left = x + 16, top = y + 16;
    if (left + w > window.innerWidth) left = x - w - 16;
    if (top + h > window.innerHeight) top = y - h - 16;
    card.style.left = Math.max(8, left) + "px";
    card.style.top = Math.max(8, top) + "px";
  }

  function hideCard() {
    var card = els.card;
    card.classList.remove("esip-show");
    hideTimer = setTimeout(function () { card.hidden = true; }, 120);
  }

  // ─── Wire the public contract events ────────────────────────────────────
  function wire() {
    window.addEventListener("esip:assetschanged", function () { refreshAssets(); });
    window.addEventListener("esip:asset_hover", function (e) { showCard(e.detail || {}); });
    window.addEventListener("esip:asset_hover_end", function () { hideCard(); });
    window.addEventListener("esip:asset_click", function (e) {
      var d = e.detail || {};
      if (!d.asset_id) return;
      // Click = focus in the panel (no modal): open panel, switch to Layers,
      // expand + scroll to the row.
      togglePanel(true);
      expandedId = d.asset_id;
      refreshAssets().then(function () {
        renderLayers();
        var rows = els.layersPanel.querySelectorAll(".esip-row.esip-active");
        if (rows[0] && rows[0].scrollIntoView) rows[0].scrollIntoView({ block: "nearest" });
      });
    });
  }

  // ─── Boot ───────────────────────────────────────────────────────────────
  function boot() {
    M = window.ESIPMap;
    if (!M || !M.config) { console.warn("[ESIP outfit] window.ESIPMap not ready"); return; }
    build();
    wire();
    refreshAssets();
    console.log("[ESIP outfit] dressed mode active");
  }

  if (window.ESIPMap && window.ESIPMap.config) {
    boot();
  } else {
    window.addEventListener("esip:ready", boot, { once: true });
  }
})();
