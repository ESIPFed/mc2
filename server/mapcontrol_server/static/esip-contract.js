/* ─────────────────────────────────────────────────────────────────────────
   ESIP Interaction Contract — ALWAYS loaded (naked AND dressed).

   This is the genuinely-missing primitive the architecture doc specced but
   never implemented: the map emitting `asset_hover` / `asset_hover_end` /
   `asset_click` / `map_click` so embedding apps (or the optional default
   outfit) can react. It loads in EVERY mode — a naked map fires these events
   into the void; the default outfit (esip-embed.js) is just one listener.

   It also publishes `window.ESIPMap`: the public, in-page command surface a
   vibe-coder builds their own UI against. Crucially, the action methods here
   do NOT poke MapLibre directly — they POST to the public REST events
   endpoint, exactly like the Python SDK does. So the built-in UI and a
   third-party UI drive the map through the identical contract. If this file
   needs something it can't get from `window.__esipInternals` (published by
   the inline shell) or REST, that's a contract hole worth fixing.

   Dual delivery, per the architecture spec:
     1. window CustomEvents (`esip:*`)  → same-document listeners (the outfit)
     2. window.parent.postMessage(...)  → iframe-embedding parents (Svelte)
   ───────────────────────────────────────────────────────────────────────── */
(function () {
  "use strict";

  function start(internals) {
    var map = internals.map;
    var registry = internals.registry;          // asset_id -> { layerIds, visible, srcId, ... }
    var basemaps = internals.basemaps;
    var handlers = internals.handlers;
    var mapId = internals.mapId;
    var baseUrl = internals.baseUrl;
    var getUserSession = internals.getUserSession;

    // ─── Dual-channel emit ────────────────────────────────────────────────
    function emit(type, detail) {
      try {
        window.dispatchEvent(new CustomEvent("esip:" + type, { detail: detail }));
      } catch (e) { /* no CustomEvent support */ }
      try {
        window.parent.postMessage({
          source: "mapcontrol",
          map_id: mapId,
          user_session_id: getUserSession(),
          type: type,
          data: detail,
        }, "*");
      } catch (e) { /* cross-origin parent — ignore */ }
    }

    // ─── Which rendered layers belong to user assets ──────────────────────
    function assetLayerIds() {
      var ids = [];
      for (var aid in registry) {
        if (!registry.hasOwnProperty(aid)) continue;
        var reg = registry[aid];
        if (!reg || !reg.layerIds) continue;
        for (var i = 0; i < reg.layerIds.length; i++) {
          if (map.getLayer(reg.layerIds[i])) ids.push(reg.layerIds[i]);
        }
      }
      return ids;
    }
    function assetForLayer(layerId) {
      for (var aid in registry) {
        if (!registry.hasOwnProperty(aid)) continue;
        var reg = registry[aid];
        if (reg && reg.layerIds && reg.layerIds.indexOf(layerId) !== -1) return aid;
      }
      return null;
    }

    // ─── Hover (peek) ─────────────────────────────────────────────────────
    var hoverId = null;
    map.on("mousemove", function (e) {
      var ids = assetLayerIds();
      var feats = ids.length ? map.queryRenderedFeatures(e.point, { layers: ids }) : [];
      if (feats.length === 0) {
        if (hoverId) {
          emit("asset_hover_end", { asset_id: hoverId });
          hoverId = null;
          map.getCanvas().style.cursor = "";
        }
        return;
      }
      var aid = assetForLayer(feats[0].layer.id);
      if (!aid) return;
      if (aid !== hoverId) {
        if (hoverId) emit("asset_hover_end", { asset_id: hoverId });
        hoverId = aid;
        map.getCanvas().style.cursor = "pointer";
        var reg = registry[aid] || {};
        emit("asset_hover", {
          asset_id: aid,
          name: reg.name || null,
          asset_type: reg.asset_type || null,
          point: { x: e.point.x, y: e.point.y },
          lngLat: [e.lngLat.lng, e.lngLat.lat],
        });
      }
    });

    // ─── Click (commit) ───────────────────────────────────────────────────
    // Additive: this does not interfere with the map's own delete-mode click
    // handler; it only emits an event the embedder may act on.
    map.on("click", function (e) {
      var ids = assetLayerIds();
      var feats = ids.length ? map.queryRenderedFeatures(e.point, { layers: ids }) : [];
      if (feats.length) {
        var aid = assetForLayer(feats[0].layer.id);
        if (aid) {
          emit("asset_click", {
            asset_id: aid,
            point: { x: e.point.x, y: e.point.y },
            lngLat: [e.lngLat.lng, e.lngLat.lat],
          });
          return;
        }
      }
      emit("map_click", { lon: e.lngLat.lng, lat: e.lngLat.lat });
    });

    // ─── Track visibility + signal asset-set changes ──────────────────────
    // We wrap the shell's existing event handlers so the contract can keep an
    // accurate per-session `visible` flag in the registry, and so any
    // listener (the outfit) is told when the layer set changes. We never
    // bypass the handlers — we decorate them.
    function decorate(name, after) {
      var orig = handlers[name];
      if (typeof orig !== "function") return;
      handlers[name] = function (data) {
        var r = orig.apply(this, arguments);
        try { after(data); } catch (e) { /* keep map resilient */ }
        return r;
      };
    }
    var CHANGE_EVENTS = [
      "add_polygon", "add_polygon_url", "add_path", "add_path_url", "add_point",
      "add_arc",
      "add_geotiff_rgb", "add_geotiff_singleband", "add_tile_layer",
      "add_drawn_polygon", "delete_asset", "remove_tile_layer",
      "update_style", "set_opacity", "move_layer",
    ];
    CHANGE_EVENTS.forEach(function (name) {
      decorate(name, function () {
        window.dispatchEvent(new CustomEvent("esip:assetschanged"));
      });
    });
    decorate("set_visibility", function (data) {
      var reg = registry[data.asset_id];
      if (reg) reg.visible = data.visible !== false;
      window.dispatchEvent(new CustomEvent("esip:assetschanged"));
    });

    // ─── REST command surface (dogfoods the public events API) ────────────
    function postEvent(type, data) {
      return fetch(baseUrl + "/api/maps/" + mapId + "/events", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ type: type, data: data, user_session_id: getUserSession() }),
      });
    }

    window.ESIPMap = {
      config: {
        mapId: mapId,
        userSession: getUserSession(),
        baseUrl: baseUrl,
        theme: document.documentElement.getAttribute("data-theme") || "light",
      },
      getBasemaps: function () { return basemaps || {}; },
      getCurrentBasemap: internals.getCurrentBasemap,
      getRegistry: function () { return registry; },
      actions: {
        setVisibility: function (assetId, visible) { return postEvent("set_visibility", { asset_id: assetId, visible: visible }); },
        deleteAsset: function (assetId) { return postEvent("delete_asset", { asset_id: assetId }); },
        setOpacity: function (assetId, opacity01) { return postEvent("set_opacity", { asset_id: assetId, opacity: opacity01 }); },
        moveLayer: function (assetId, position) { return postEvent("move_layer", { asset_id: assetId, position: position }); },
        zoomToAssets: function (assetIds) { return postEvent("zoom_to_assets", { asset_ids: assetIds }); },
        setBasemap: function (id) { return postEvent("set_basemap", { basemap: id }); },
        emit: emit, // exposed so bespoke UIs can re-broadcast if useful
      },
    };

    // Announce the contract is live. The default outfit (and any bespoke
    // consumer) can boot off this.
    window.dispatchEvent(new CustomEvent("esip:ready"));
    console.log("[ESIP contract] interaction events live (mode:",
      document.documentElement.getAttribute("data-esip-ui") || "?", ")");
  }

  // The inline shell publishes internals synchronously at end of its script,
  // then fires 'esip:internals-ready'. We may attach before or after that, so
  // handle both orders.
  if (window.__esipInternals) {
    start(window.__esipInternals);
  } else {
    window.addEventListener("esip:internals-ready", function () {
      start(window.__esipInternals);
    }, { once: true });
  }
})();
