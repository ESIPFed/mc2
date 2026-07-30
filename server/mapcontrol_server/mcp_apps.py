"""MCP Apps layer (ext-apps / SEP-1865) — the ESIP map as an inline chat app.

Implements the server half of the MCP Apps extension
(``io.modelcontextprotocol/ui``) per ``docs/vision/mcp-apps-appification.md`` §5,
on the stable 1.x SDK (no beta pins). Option A (thin wrapper): the ``ui://``
template renders the existing live ``/map/{map_id}`` page in a nested iframe,
so the full live map (WebSocket push, drawing, terrain) appears inline in
MCP Apps hosts (Claude web/desktop, ChatGPT, Cursor, ...). Hosts without the
extension see today's text + ``structuredContent`` results unchanged
(graceful degradation is built into the spec — and our tools already comply).

What this module does (import == install, mirrors ``mcp_resources`` /
``mcp_compat``):

1. **Registers the ``ui://esip/map.html`` template resource** on the shared
   ``mcp_server`` (mimeType exactly ``text/html;profile=mcp-app`` — the spec
   MUST). The HTML lives at ``static/mcp-app.html`` and speaks the ext-apps
   postMessage dialect raw (``ui/initialize`` → ``initialized`` → renders the
   map URL from the triggering tool's result).
2. **Registers the ``show_map`` tool** — lets the model summon the inline map
   for an EXISTING map_id (``create_map`` covers the new-map path).
3. **Stamps ``_meta.ui.resourceUri``** (nested form — the flat
   ``_meta["ui/resourceUri"]`` is deprecated) onto the UI-enabled tools in
   ``tools/list`` via the proven request-handler-wrapping technique
   (the 1.x FastMCP decorators don't expose tool ``_meta``).
4. **Stamps ``_meta.ui`` (csp + prefersBorder)** onto the template's
   ``resources/read`` result. CSP declares ``frameDomains`` = the public map
   origin (``MAPCONTROL_PUBLIC_URL``) so the host's sandbox allows the nested
   live-map iframe (spec: omitted csp ⇒ ``frame-src 'none'``).
5. **Hides ``ui://`` resources from ``resources/list``** (spec: servers MAY
   omit UI resources — discovery is via tool metadata) so the ``map://``
   taxonomy stays clean.
6. **Advertises the extension** in the ``initialize`` response
   (``capabilities.extensions["io.modelcontextprotocol/ui"]``) — advertised ==
   implemented grows in lockstep, same invariant as tools/resources.

Import order (main.py): AFTER ``mcp_tools`` + ``mcp_resources`` (needs the
server instance and the registered handlers), BEFORE ``mcp_compat`` (compat
must wrap LAST so every result — including ours — gets the 2026 draft-revision
stamps). ADR-0001: no new env knobs; the CSP origin derives from the existing
``MAPCONTROL_PUBLIC_URL``.

2026-revision note (roadmap Phase 5): the ``extensions`` capability mechanism
(SEP-2133) survives the new revision; on the v2 SDK bump this module's
stamping migrates to the SDK's native ``extensions=`` / ``_meta`` support and
the advertisement port rides the existing ``server/discover`` wrapper port.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import mcp.types as types
from mcp.types import CallToolResult, ToolAnnotations

from .config import load_config
from .mcp_tools import (
    _public_base_url,
    _require_map,
    _resource_link,
    _tool_result,
    map_uri,
    mcp_server,
)


logger = logging.getLogger("mapcontrol")

# ─── Extension identity (ext-apps spec, verified 2026-07-16) ─────────────────
UI_EXTENSION_ID = "io.modelcontextprotocol/ui"
UI_MIME_TYPE = "text/html;profile=mcp-app"
UI_TEMPLATE_URI = "ui://esip/map.html"

# Tools whose results render through the ui:// template.
UI_ENABLED_TOOLS = ("create_map", "show_map")

_TEMPLATE_PATH = Path(__file__).parent / "static" / "mcp-app.html"


def _template_html() -> str:
    """The app template HTML (read per-request: cheap, dev-editable)."""
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def _public_origin() -> str:
    """scheme://host[:port] of the public map URL — the nested iframe origin
    the host's CSP must allow (frameDomains)."""
    parsed = urlparse(_public_base_url())
    return f"{parsed.scheme}://{parsed.netloc}"


def _basemap_origins() -> list[str]:
    """Origins of every configured basemap tile URL (deduped, sorted).

    MapLibre fetches raster tiles via fetch/XHR, so tile origins belong in
    ``connectDomains``; some hosts route them through img-src instead, so
    they are declared in ``resourceDomains`` too (belt and suspenders)."""
    origins: set[str] = set()
    try:
        for entry in load_config().map.basemaps.values():
            url = getattr(entry, "url", "") or ""
            parsed = urlparse(url)
            if parsed.scheme in ("http", "https") and parsed.netloc:
                origins.add(f"{parsed.scheme}://{parsed.netloc}")
    except Exception:  # config problems must never break the MCP layer
        logger.exception("basemap origin discovery failed")
    return sorted(origins)


def _ui_resource_meta() -> dict[str, Any]:
    """_meta.ui for the template's resources/read result (spec §UI Resource).

    OPTION B (probe-8 verdict, 2026-07-18): Claude web's sandbox honors
    ``connectDomains`` (fetch to our origin resolved) and the resource
    channel (img error was a plain 404, no CSP violation), but NOT
    ``frameDomains`` (explicit ``frame-src`` violation with the stock
    policy "'self' blob: data:"). So the production template renders the
    map NATIVELY — MapLibre GL loaded from our origin (resourceDomains),
    tiles and REST/WebSocket state over connectDomains — with no nested
    iframe. ``frameDomains`` stays declared: it is the correct Option-A
    signal for hosts that do honor it (spec §Sandbox proxy step 5), and
    harmless on Claude web.

    Probe history (see docs/vision/mcp-apps-appification.md §6): probes 1-3 the
    342KB vite bundle broke document delivery, masking everything; 4-6
    isolated delivery/handshake (app INITIATES ui/initialize); 7 proved
    frameDomains unsupported on Claude web; 8 mapped the channels.
    """
    origin = _public_origin()
    tiles = _basemap_origins()
    # CSP source-expression scheme matching is EXACT for https/wss: a
    # "https://host" source does NOT authorize "wss://host" (v1 readout:
    # Claude plumbed our list verbatim into connect-src and the WebSocket
    # was blocked). Declare the wss:// variant explicitly.
    ws_origin = origin.replace("https://", "wss://", 1).replace(
        "http://", "ws://", 1)
    connect = sorted({origin, ws_origin, *tiles})
    resources = sorted({origin, *tiles})
    return {"ui": {"csp": {
        "connectDomains": connect,
        "resourceDomains": resources,
        "frameDomains": [origin],
    }}}






# ─── 1+2. Template resource + show_map tool (plain FastMCP registration) ─────

@mcp_server.resource(
    UI_TEMPLATE_URI,
    title="ESIP Map App",
    description=(
        "MCP Apps HTML template that renders the live ESIP map inline in the "
        "conversation (referenced by create_map / show_map via _meta.ui)."
    ),
    mime_type=UI_MIME_TYPE,
)
async def res_ui_map_template() -> str:
    return _template_html()


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Show Map", readOnlyHint=True, idempotentHint=True
    ),
)
async def show_map(map_id: str) -> CallToolResult:
    """Display an existing map inline in the conversation (MCP Apps hosts)
    or return its browser URL (all other hosts).

    Read-only: no map state changes. Pass the map_id obtained from create_map
    (or from the map://maps resource index).
    """
    await _require_map(map_id)
    base = _public_base_url()
    structured = {
        "map_id": map_id,
        "url": f"{base}/map/{map_id}",
        "resource_uri": map_uri(map_id),
    }
    return _tool_result(
        structured,
        _resource_link(
            map_uri(map_id),
            name=f"map-{map_id}",
            mime_type="application/json",
            description="Map info resource; child resources: /viewport, /assets, /drawn.",
        ),
    )


# ─── 3–5. Request-handler stamping (tools/list, resources/read, resources/list) ─

def _wrap_list_tools(orig):
    """Stamp _meta.ui.resourceUri (nested form) onto the UI-enabled tools."""
    async def handler(req):
        server_result = await orig(req)
        root = getattr(server_result, "root", None)
        if isinstance(root, types.ListToolsResult):
            for tool in root.tools:
                if tool.name in UI_ENABLED_TOOLS:
                    meta = dict(tool.meta or {})
                    ui = dict(meta.get("ui") or {})
                    ui.setdefault("resourceUri", UI_TEMPLATE_URI)
                    meta["ui"] = ui
                    tool.meta = meta
        return server_result

    return handler


def _wrap_read_resource(orig):
    """Stamp _meta.ui onto reads of the ui:// template (when non-empty).

    Uses model_copy(update={"_meta": ...}) — empirically the reliable way to
    get the ``_meta`` key onto the wire for ResourceContents on the 1.x SDK
    (the ``meta`` field's alias only applies on some serialization paths).
    """

    async def handler(req):
        server_result = await orig(req)
        ui_meta = _ui_resource_meta()
        if not ui_meta:
            return server_result
        uri = str(getattr(getattr(req, "params", None), "uri", "") or "")
        if uri == UI_TEMPLATE_URI:
            root = getattr(server_result, "root", None)
            if isinstance(root, types.ReadResourceResult):
                root.contents = [
                    c.model_copy(update={"_meta": ui_meta})
                    for c in root.contents
                ]
        return server_result

    return handler


def _wrap_list_resources(orig):
    """Hide ui:// resources from resources/list (spec: MAY omit; discovery is
    via tool metadata) — keeps the map:// taxonomy clean."""
    async def handler(req):
        server_result = await orig(req)
        root = getattr(server_result, "root", None)
        if isinstance(root, types.ListResourcesResult):
            root.resources = [
                r for r in root.resources if not str(r.uri).startswith("ui://")
            ]
        return server_result

    return handler


# ─── 6. Advertise the extension (advertised == implemented grows) ────────────

def _install_extension_advertisement() -> None:
    """Add capabilities.extensions[io.modelcontextprotocol/ui] to initialize.

    Wraps the (already advertised==implemented-wrapped) init-options factory
    from mcp_tools. ServerCapabilities is extra=allow on the 1.x SDK, so the
    ``extensions`` field serializes onto the wire (SEP-2133 negotiation).
    """
    orig = mcp_server._mcp_server.create_initialization_options

    def create_init_options_with_extensions(*args, **kwargs):
        opts = orig(*args, **kwargs)
        caps = opts.capabilities
        extensions = dict(getattr(caps, "extensions", None) or {})
        extensions[UI_EXTENSION_ID] = {"mimeTypes": [UI_MIME_TYPE]}
        caps.extensions = extensions
        return opts

    mcp_server._mcp_server.create_initialization_options = (
        create_init_options_with_extensions
    )


def install() -> None:
    handlers = mcp_server._mcp_server.request_handlers
    handlers[types.ListToolsRequest] = _wrap_list_tools(
        handlers[types.ListToolsRequest])
    handlers[types.ReadResourceRequest] = _wrap_read_resource(
        handlers[types.ReadResourceRequest])
    handlers[types.ListResourcesRequest] = _wrap_list_resources(
        handlers[types.ListResourcesRequest])
    _install_extension_advertisement()
    logger.info(
        "MCP Apps layer installed: %s advertised, template %s, UI tools %s",
        UI_EXTENSION_ID, UI_TEMPLATE_URI, ", ".join(UI_ENABLED_TOOLS),
    )


# Import == install. main.py imports this AFTER mcp_tools/mcp_resources and
# BEFORE mcp_compat (compat wraps last so all results get the 2026 stamps).
install()
