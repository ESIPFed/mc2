/* Viewer-local inspection of metadata.extra.inspector. No map mutations. */
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
    if (!internals || !api || !window.maplibregl) return;
    var map = internals.map;
    var selected = null;
    var popup = null;
    var expanded = false;
    var signature = "";
    var frames = [];
    var fetches = [];
    var previousFocus = null;

    function disposeContent() {
      fetches.forEach(function (controller) { controller.abort(); });
      fetches = [];
      frames = [];
    }
    function close(keepMapSelection) {
      disposeContent();
      var old = popup;
      popup = null;
      selected = null;
      signature = "";
      expanded = false;
      if (old) old.remove();
      if (!keepMapSelection && internals.deselectAsset) internals.deselectAsset();
      if (previousFocus && previousFocus.isConnected && previousFocus.focus) {
        previousFocus.focus({ preventScroll: true });
      }
      previousFocus = null;
    }
    function assetFor(id) { return api.getAssetInfo(id); }
    function syncSize() {
      if (!popup) return;
      var el = popup.getElement();
      el.classList.toggle("is-expanded", expanded);
      el.style.setProperty("--inspector-map-height", map.getContainer().clientHeight + "px");
      el.style.setProperty("--inspector-map-width", map.getContainer().clientWidth + "px");
      var height = map.getContainer().clientHeight;
      var point = selected && map.project ? map.project(selected.anchor) : { y: height / 2 };
      // A large card cannot fit either above or below a central marker.
      // Constrain its scrollable body to the larger side before MapLibre
      // chooses the anchor, leaving room for its tip, offset, and edge inset.
      var available = Math.max(64, Math.min(height - 24, Math.max(point.y, height - point.y) - 36));
      el.style.setProperty("--inspector-available-height", available + "px");
      popup.setMaxWidth(Math.max(100, Math.min(380, map.getContainer().clientWidth - 24)) + "px");
      if (selected) popup.setLngLat(selected.anchor);
    }
    function link(url, title) {
      var anchor = node("a", "esip-inspector-link", title || "Open attachment");
      anchor.href = url;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      return anchor;
    }
    function htmlFrame(url, title) {
      var frame = node("iframe", "esip-inspector-frame");
      frame.title = title || "Interactive visualization";
      frame.setAttribute("sandbox", "allow-scripts");
      frame.referrerPolicy = "no-referrer";
      // Direct loading remains a fallback when the artifact host disallows
      // CORS. When readable, add an Escape bridge inside the opaque sandbox.
      var token = Math.random().toString(36).slice(2);
      frames.push({ frame: frame, token: token });
      var controller = new AbortController();
      fetches.push(controller);
      fetch(url, { signal: controller.signal, credentials: "include" }).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }).then(function (html) {
        if (controller.signal.aborted || !frame.isConnected) return;
        var base = document.createElement("base");
        base.href = url;
        var bridge = "<script>window.addEventListener('keydown',function(e){if(e.key==='Escape')parent.postMessage({source:'esip-inspector-artifact',token:" + JSON.stringify(token) + "},'*');});<" + "/script>";
        // Put the trusted base first; later producer <base> tags cannot replace it.
        frame.srcdoc = /<head[^>]*>/i.test(html)
          ? html.replace(/<head[^>]*>/i, function (head) { return head + base.outerHTML + bridge; })
          : base.outerHTML + bridge + html;
      }).catch(function () {
        if (!controller.signal.aborted) frame.src = url;
      });
      return frame;
    }
    function render() {
      if (!selected) return;
      var asset = assetFor(selected.asset_id);
      var spec = descriptor(asset);
      if (!spec || asset.visible === false) { close(); return; }
      var nextSignature = JSON.stringify([asset.name, asset.metadata, selected.asset_id]);
      if (nextSignature === signature) return;
      signature = nextSignature;
      disposeContent();
      var root = node("section", "esip-inspector-card");
      root.setAttribute("role", "dialog");
      root.setAttribute("aria-label", asset.name || asset.metadata.title || "Map feature details");
      var header = node("div", "esip-inspector-header");
      var heading = node("h2", "", asset.name || asset.metadata.title || "Map feature details");
      header.appendChild(heading);
      var expand = node("button", "esip-inspector-button", expanded ? "Reduce" : "Expand");
      expand.type = "button";
      expand.setAttribute("aria-expanded", String(expanded));
      expand.addEventListener("click", function () {
        expanded = !expanded;
        expand.textContent = expanded ? "Reduce" : "Expand";
        expand.setAttribute("aria-expanded", String(expanded));
        syncSize();
      });
      header.appendChild(expand);
      var dismiss = node("button", "esip-inspector-button esip-inspector-close", "×");
      dismiss.type = "button";
      dismiss.setAttribute("aria-label", "Close details (Escape)");
      dismiss.addEventListener("click", function () { close(); });
      header.appendChild(dismiss);
      root.appendChild(header);
      var body = node("div", "esip-inspector-body");
      var description = asset.metadata.description ? node("p", "esip-inspector-description", asset.metadata.description) : null;
      var status = spec.status || (spec.attachments.length ? "ready" : "empty");
      if (status !== "ready") {
        var messages = { loading: "Results are still being prepared…", empty: "No results are available for this location.", error: "Results could not be loaded. Try again after the analysis finishes." };
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
        if (attachment.title && attachment.type !== "link") item.appendChild(node("h3", "", attachment.title));
        if (attachment.type === "html") item.appendChild(htmlFrame(url, attachment.title));
        else if (attachment.type === "image") {
          var img = node("img", "esip-inspector-image");
          img.src = url;
          img.alt = attachment.title || "Analysis result";
          img.addEventListener("error", function () {
            img.replaceWith(node("p", "esip-inspector-status", "Preview unavailable. Open the attachment to view it."));
          });
          item.appendChild(img);
        }
        item.appendChild(link(url, attachment.type === "link" ? attachment.title : "Open in new tab"));
        body.appendChild(item);
      });
      if (!shown && status === "ready") body.appendChild(node("p", "esip-inspector-status", "No preview is available."));
      if (description) {
        if (shown) {
          var details = node("details", "esip-inspector-details");
          details.appendChild(node("summary", "", "Details"));
          details.appendChild(description);
          body.appendChild(details);
        } else body.prepend(description);
      }
      root.appendChild(body);
      // Keep interactions inside the card from becoming map selections.
      ["click", "dblclick", "pointerdown", "wheel"].forEach(function (name) {
        root.addEventListener(name, function (event) { event.stopPropagation(); });
      });
      if (!popup) {
        popup = new maplibregl.Popup({ closeButton: false, closeOnClick: false, focusAfterOpen: false, offset: 12, className: "esip-inspector-popup" });
        popup.setLngLat(selected.anchor).setDOMContent(root).addTo(map);
      } else popup.setDOMContent(root).setLngLat(selected.anchor);
      syncSize();
      requestAnimationFrame(syncSize);
      // Explicit keyboard entry; live updates preserve the selected asset.
      if (!previousFocus) {
        previousFocus = document.activeElement;
        dismiss.focus({ preventScroll: true });
      }
    }
    function claimClick(detail) {
      if (internals.isDrawing && internals.isDrawing()) return false;
      var asset = assetFor(detail.asset_id);
      if (!descriptor(asset) || asset.visible === false) { close(true); return false; }
      var geometry = detail.feature && detail.feature.geometry;
      var anchor = geometry && geometry.type === "Point" ? geometry.coordinates.slice(0, 2) : detail.lngLat.slice();
      if (!anchor.every(Number.isFinite)) return false;
      selected = { asset_id: detail.asset_id, anchor: anchor };
      render();
      // Re-clicking another point within the same asset still moves the anchor.
      if (popup) popup.setLngLat(anchor);
      syncSize();
      return true;
    }
    api.inspector = { claimClick: claimClick, close: function () { close(); } };
    window.addEventListener("esip:map_click", function () { close(); });
    window.addEventListener("esip:assetschanged", render);
    window.addEventListener("esip:interactionmode", function () {
      if (internals.isDrawing && internals.isDrawing()) close();
    });
    window.addEventListener("keydown", function (event) { if (event.key === "Escape") close(); });
    window.addEventListener("message", function (event) {
      var data = event.data;
      if (!data || typeof data !== "object") return;
      if (data.source === "esip-inspector-artifact" && frames.some(function (entry) {
        return entry.frame.contentWindow === event.source && entry.token === data.token;
      })) { close(); return; }
      if (window.parent === window || event.source !== window.parent || data.source !== "esip-embedder" || data.type !== "close_inspector" || data.map_id !== api.config.mapId) return;
      if (document.referrer) {
        try { if (event.origin !== new URL(document.referrer).origin) return; } catch (_) { return; }
      }
      close();
    });
    map.on("resize", syncSize);
    map.on("move", syncSize);
    map.on("remove", function () { close(); });
  }
  if (window.ESIPMap) start();
  else window.addEventListener("esip:ready", start, { once: true });
})();
