/* Viewer-local chart previews and pinned callouts. No map mutations. */
(function () {
  "use strict";

  function safeUrl(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    try {
      var url = new URL(value, document.baseURI);
      return /^(https?:)$/.test(url.protocol) ? url.href : null;
    } catch (_) { return null; }
  }
  function descriptor(asset) {
    var value = asset && asset.metadata && asset.metadata.extra && asset.metadata.extra.inspector;
    if (!value || typeof value !== "object" || Array.isArray(value)) return null;
    if (value.version != null && value.version !== 1) return null;
    if (value.attachments != null && !Array.isArray(value.attachments)) return null;
    return Object.assign({ version: 1 }, value, { attachments: value.attachments || [] });
  }
  function node(tag, className, text) {
    var el = document.createElement(tag);
    if (className) el.className = className;
    if (text != null) el.textContent = text;
    return el;
  }

  function start() {
    var internals = window.__esipInternals;
    var api = window.ESIPMap;
    if (!internals || !api) return;
    var map = internals.map;
    var container = map.getContainer();
    var layer = node("div", "esip-inspector-layer");
    var svgNS = "http://www.w3.org/2000/svg";
    var connectors = document.createElementNS(svgNS, "svg");
    connectors.classList.add("esip-inspector-connectors");
    connectors.setAttribute("aria-hidden", "true");
    layer.appendChild(connectors);
    container.appendChild(layer);
    var pinned = new Map();
    var preview = null;
    var pendingHover = null;
    var hoverTimer = null;
    var active = null;
    var order = 0;
    var drag = null;
    var listeners = [];

    function listen(name, handler) {
      window.addEventListener(name, handler);
      listeners.push([name, handler]);
    }

    function assetFor(id) { return api.getAssetInfo(id); }
    function drawing() { return internals.isDrawing && internals.isDrawing(); }
    function records() { return Array.from(pinned.values()).concat(preview ? [preview] : []); }
    function anchorFor(detail) {
      var geometry = detail.feature && detail.feature.geometry;
      var anchor = geometry && geometry.type === "Point" ? geometry.coordinates.slice(0, 2) : (detail.lngLat || []).slice(0, 2);
      return anchor.length === 2 && anchor.every(Number.isFinite) ? anchor : null;
    }
    function disposeContent(record) {
      record.fetches.forEach(function (controller) { controller.abort(); });
      record.fetches = [];
      record.frames = [];
    }
    function cancelHover() {
      clearTimeout(hoverTimer);
      hoverTimer = null;
      pendingHover = null;
    }
    function removeCard(record) {
      if (!record) return;
      disposeContent(record);
      if (drag && drag.record === record) drag = null;
      record.el.remove();
      if (record.line) record.line.remove();
      if (record.anchorDot) record.anchorDot.remove();
      if (preview === record) preview = null;
      pinned.delete(record.asset_id);
      if (active === record) active = Array.from(pinned.values()).sort(function (a, b) { return b.order - a.order; })[0] || null;
      if (!preview && !pinned.size && internals.deselectAsset) internals.deselectAsset();
    }
    function hidePreview() { cancelHover(); removeCard(preview); }
    function closeAll() { cancelHover(); records().forEach(removeCard); }
    function dismissTop() {
      if (preview || pendingHover) { hidePreview(); return; }
      removeCard(active);
    }
    function raise(record) {
      record.order = ++order;
      record.el.style.zIndex = record.order;
      if (record.pinned) active = record;
    }
    function project(anchor) {
      return map.project ? map.project(anchor) : { x: container.clientWidth / 2, y: container.clientHeight / 2 };
    }
    function clamp(value, min, max) { return Math.max(min, Math.min(value, Math.max(min, max))); }
    function layout(record) {
      var width = container.clientWidth;
      var height = container.clientHeight;
      record.el.style.setProperty("--inspector-map-width", width + "px");
      record.el.style.setProperty("--inspector-map-height", height + "px");
      var size = { width: record.el.offsetWidth, height: record.el.offsetHeight };
      var point = project(record.anchor);
      if (!record.pinned || !record.position) {
        // Glimpses follow their marker; pinned cards keep a screen position.
        var left = point.x - size.width / 2;
        var top = point.y - size.height - 14;
        if (top < 8) top = point.y + 14;
        record.position = { x: left, y: top };
      }
      record.position.x = clamp(record.position.x, 8, width - size.width - 8);
      record.position.y = clamp(record.position.y, 8, height - size.height - 8);
      record.el.style.left = record.position.x + "px";
      record.el.style.top = record.position.y + "px";
      if (record.line) {
        // Connect to the nearest card edge, rather than crossing the chart.
        var x = clamp(point.x, record.position.x, record.position.x + size.width);
        var y = clamp(point.y, record.position.y, record.position.y + size.height);
        var inside = point.x >= record.position.x && point.x <= record.position.x + size.width && point.y >= record.position.y && point.y <= record.position.y + size.height;
        record.line.style.visibility = inside ? "hidden" : "visible";
        record.line.setAttribute("x1", point.x);
        record.line.setAttribute("y1", point.y);
        record.line.setAttribute("x2", x);
        record.line.setAttribute("y2", y);
        record.anchorDot.setAttribute("cx", point.x);
        record.anchorDot.setAttribute("cy", point.y);
      }
    }
    function layoutAll() { records().forEach(layout); }
    function htmlFrame(record, url, title) {
      var frame = node("iframe", "esip-inspector-frame");
      frame.title = title || "Interactive visualization";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.referrerPolicy = "no-referrer";
      frame.tabIndex = record.pinned ? 0 : -1;
      var token = Math.random().toString(36).slice(2);
      record.frames.push({ frame: frame, token: token });
      var controller = new AbortController();
      record.fetches.push(controller);
      fetch(url, { signal: controller.signal, credentials: "include" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }).then(function (html) {
        if (controller.signal.aborted || !frame.isConnected) return;
        var base = document.createElement("base");
        base.href = url;
        var bridge = "<script>window.addEventListener('keydown',function(e){if(e.key==='Escape')parent.postMessage({source:'esip-inspector-artifact',token:" + JSON.stringify(token) + "},'*');});<" + "/script>";
        frame.srcdoc = /<head[^>]*>/i.test(html)
          ? html.replace(/<head[^>]*>/i, function (head) { return head + base.outerHTML + bridge; })
          : base.outerHTML + bridge + html;
      }).catch(function () {
        if (!controller.signal.aborted) frame.src = url;
      });
      return frame;
    }
    function updatePinState(record) {
      record.el.classList.toggle("is-pinned", record.pinned);
      record.el.classList.toggle("is-preview", !record.pinned);
      record.el.inert = !record.pinned;
      record.el.setAttribute("role", record.pinned ? "dialog" : "tooltip");
      record.header.tabIndex = record.pinned ? 0 : -1;
      record.header.title = record.pinned ? "Drag to move; use arrow keys when focused" : "Click the map feature to pin";
      record.el.querySelectorAll("iframe").forEach(function (frame) { frame.tabIndex = record.pinned ? 0 : -1; });
      if (record.pinned && !record.line) {
        record.line = document.createElementNS(svgNS, "line");
        record.line.setAttribute("data-asset-id", record.asset_id);
        connectors.appendChild(record.line);
        record.anchorDot = document.createElementNS(svgNS, "circle");
        record.anchorDot.classList.add("esip-inspector-anchor");
        record.anchorDot.setAttribute("data-asset-id", record.asset_id);
        record.anchorDot.setAttribute("r", "3.5");
        connectors.appendChild(record.anchorDot);
      }
    }
    function render(record) {
      var asset = assetFor(record.asset_id);
      var spec = descriptor(asset);
      if (!spec || asset.visible === false) { removeCard(record); return false; }
      var signature = JSON.stringify([asset.name, asset.metadata]);
      if (signature === record.signature) return true;
      record.signature = signature;
      disposeContent(record);
      record.el.replaceChildren();
      record.el.setAttribute("aria-label", asset.name || asset.metadata.title || "Map feature chart");
      var header = node("div", "esip-inspector-header");
      record.header = header;
      var grip = node("span", "esip-inspector-grip", "⠿");
      grip.setAttribute("aria-hidden", "true");
      header.appendChild(grip);
      header.appendChild(node("h2", "", asset.name || asset.metadata.title || "Map feature chart"));
      var hint = node("span", "esip-inspector-hint", "Click to pin");
      header.appendChild(hint);
      var dismiss = node("button", "esip-inspector-close", "×");
      dismiss.type = "button";
      dismiss.setAttribute("aria-label", "Close chart (Escape)");
      dismiss.addEventListener("click", function () { removeCard(record); });
      header.appendChild(dismiss);
      header.addEventListener("pointerdown", function (event) {
        if (!record.pinned || event.button !== 0 || event.target.closest("button")) return;
        event.preventDefault();
        raise(record);
        drag = { record: record, pointer: event.pointerId, x: event.clientX, y: event.clientY, left: record.position.x, top: record.position.y };
        header.setPointerCapture(event.pointerId);
        record.el.classList.add("is-dragging");
      });
      header.addEventListener("lostpointercapture", endDrag);
      header.addEventListener("keydown", function (event) {
        if (!record.pinned || event.target !== header || !/^Arrow(Left|Right|Up|Down)$/.test(event.key)) return;
        event.preventDefault();
        event.stopPropagation();
        var step = event.shiftKey ? 24 : 8;
        record.position.x += event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0;
        record.position.y += event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0;
        layout(record);
      });
      record.el.appendChild(header);
      var body = node("div", "esip-inspector-body");
      var status = spec.status || (spec.attachments.length ? "ready" : "empty");
      if (status !== "ready") {
        var messages = { loading: "Preparing chart…", empty: "No results for this location.", error: "Chart unavailable. Try again after analysis finishes." };
        var state = node("p", "esip-inspector-status", messages[status] || messages.empty);
        state.setAttribute("role", "status");
        body.appendChild(state);
      }
      var shown = 0;
      spec.attachments.forEach(function (attachment) {
        if (!attachment || typeof attachment !== "object") return;
        var url = safeUrl(attachment.url);
        if (!url || ["html", "image", "link"].indexOf(attachment.type) === -1) return;
        shown++;
        var item = node("div", "esip-inspector-attachment");
        if (attachment.type === "html") item.appendChild(htmlFrame(record, url, attachment.title));
        else if (attachment.type === "image") {
          var img = node("img", "esip-inspector-image");
          img.src = url;
          img.alt = attachment.title || "Analysis result";
          img.addEventListener("error", function () { img.replaceWith(node("p", "esip-inspector-status", "Preview unavailable.")); });
          item.appendChild(img);
        } else {
          var link = node("a", "esip-inspector-link", attachment.title || "View result");
          link.href = url;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          item.appendChild(link);
        }
        body.appendChild(item);
      });
      if (!shown && status === "ready") body.appendChild(node("p", "esip-inspector-status", "No preview available."));
      record.el.appendChild(body);
      updatePinState(record);
      layout(record);
      return true;
    }
    function createCard(detail, pinnedState) {
      var record = { asset_id: detail.asset_id, anchor: anchorFor(detail), pinned: pinnedState, position: null, line: null, frames: [], fetches: [], signature: "", el: node("section", "esip-inspector-card") };
      record.el.setAttribute("data-asset-id", record.asset_id);
      ["click", "dblclick", "pointerdown", "wheel"].forEach(function (name) {
        record.el.addEventListener(name, function (event) { event.stopPropagation(); });
      });
      record.el.addEventListener("pointerdown", function () { if (record.pinned) raise(record); });
      layer.appendChild(record.el);
      if (pinnedState) pinned.set(record.asset_id, record); else preview = record;
      render(record);
      raise(record);
      return record;
    }
    function claimHover(detail) {
      var asset = assetFor(detail.asset_id);
      if (drawing() || !descriptor(asset) || asset.visible === false || !anchorFor(detail)) { hidePreview(); return false; }
      hidePreview();
      if (pinned.has(detail.asset_id)) return true;
      pendingHover = detail;
      hoverTimer = setTimeout(function () {
        var target = pendingHover;
        pendingHover = null;
        hoverTimer = null;
        if (target && !drawing()) createCard(target, false);
      }, 120);
      return true;
    }
    function claimClick(detail) {
      var asset = assetFor(detail.asset_id);
      var anchor = anchorFor(detail);
      if (drawing() || !descriptor(asset) || asset.visible === false || !anchor) { hidePreview(); return false; }
      cancelHover();
      var record = pinned.get(detail.asset_id);
      if (record) {
        hidePreview();
        record.anchor = anchor;
        raise(record);
        layout(record);
      } else if (preview && preview.asset_id === detail.asset_id) {
        record = preview;
        preview = null;
        record.pinned = true;
        record.anchor = anchor;
        pinned.set(record.asset_id, record);
        updatePinState(record);
        raise(record);
        layout(record);
      } else {
        hidePreview();
        record = createCard(detail, true);
      }
      record.header.focus({ preventScroll: true });
      return true;
    }
    function moveDrag(event) {
      if (!drag || event.pointerId !== drag.pointer) return;
      event.preventDefault();
      drag.record.position = { x: drag.left + event.clientX - drag.x, y: drag.top + event.clientY - drag.y };
      layout(drag.record);
    }
    function endDrag(event) {
      if (!drag || event.pointerId !== drag.pointer) return;
      drag.record.el.classList.remove("is-dragging");
      drag = null;
    }
    listen("pointermove", moveDrag);
    listen("pointerup", endDrag);
    listen("pointercancel", endDrag);
    api.inspector = { claimClick: claimClick, claimHover: claimHover, hidePreview: hidePreview, close: closeAll };
    listen("esip:map_click", hidePreview);
    listen("esip:asset_hover_end", hidePreview);
    listen("esip:assetschanged", function () { records().forEach(render); layoutAll(); });
    listen("esip:interactionmode", function () { if (drawing()) closeAll(); });
    listen("keydown", function (event) { if (event.key === "Escape") dismissTop(); });
    listen("message", function (event) {
      var data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.source === "esip-inspector-artifact") {
        var owner = records().find(function (record) { return record.frames.some(function (entry) { return entry.frame.contentWindow === event.source && entry.token === data.token; }); });
        if (owner) removeCard(owner);
        return;
      }
      if (window.parent === window || event.source !== window.parent || data.source !== "esip-embedder" || data.type !== "close_inspector" || data.map_id !== api.config.mapId) return;
      if (document.referrer) {
        try { if (event.origin !== new URL(document.referrer).origin) return; } catch (_) { return; }
      }
      if (data.reason === "escape") dismissTop(); else hidePreview();
    });
    map.on("resize", layoutAll);
    map.on("move", layoutAll);
    map.on("remove", function () {
      closeAll();
      layer.remove();
      listeners.forEach(function (entry) { window.removeEventListener(entry[0], entry[1]); });
      listeners = [];
    });
  }
  if (window.ESIPMap) start();
  else window.addEventListener("esip:ready", start, { once: true });
})();
