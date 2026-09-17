"""FastAPI application entrypoint."""

from __future__ import annotations

import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path


_SERVER_START_TIME = time.time()

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from .config import load_config, AppConfig
from .database import init_db, close_db
from .websocket import manager
from .models import Viewport
from .services import session_service
from .services import screenshot_service

from .routers import maps, sessions, events, assets, files

# MCP layer (Phase 0 of .vision-documents/mcp-compliance-roadmap.md): an in-process
# Model Context Protocol server mounted at /mcp (Streamable HTTP). It wraps the
# SAME service functions the REST API uses — additive, no separate state.
from .mcp_tools import mcp_server, _internal_base_url

# MCP Resources layer (roadmap §6, partial): the map:// resource taxonomy.
# Importing the module registers every resource template on mcp_server —
# nothing else to wire. Must come after mcp_tools (one-way import).
from . import mcp_resources  # noqa: F401

# MCP Apps layer (ext-apps / SEP-1865; .vision-documents/mcp-apps-appification.md):
# the ui:// template + show_map tool + _meta.ui stamping + the
# io.modelcontextprotocol/ui extension advertisement. Import == install;
# MUST come after mcp_tools/mcp_resources and BEFORE mcp_compat (compat
# wraps last so all results get the 2026 draft-revision stamps).
from . import mcp_apps  # noqa: F401

# 2026 draft-revision pre-adoption shims (Phase 5 forward-compat): stamps
# resultType / ttlMs / cacheScope / serverInfo _meta on results and maps
# resource-not-found reads to -32602. Import == install; MUST come after
# mcp_tools AND mcp_resources so every request handler is registered before
# wrapping. See mcp_compat.py + .vision-documents/mcp-2026-protocol-upgrade.md.
from . import mcp_compat  # noqa: F401

# Standalone auth portal (MAPCONTROL_AUTH_MODE=standalone; see portal/):
# login/signup/admin/dashboard pages + the portal-gated OAuth consent. The
# check is env-driven at import time — in the default local mode nothing is
# registered and this import has zero effect (ADR-0001 dual-deployability).
from .portal import portal_enabled as _portal_enabled

logger = logging.getLogger("mapcontrol")

# Will be set during startup
_config: AppConfig | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown."""
    global _config
    _config = load_config()

    logging.basicConfig(level=logging.INFO)
    logger.info("Starting Map Control Server...")

    await init_db(_config)
    logger.info(f"Database initialized at {_config.storage.database_path}")

    # Standalone auth portal (MAPCONTROL_AUTH_MODE=standalone): open the user
    # store + seed the admin account. Fail-loud on misconfiguration (missing
    # admin password, bad custom backend) — an auth boundary must never
    # silently degrade. No-op in local/integrated modes.
    if _portal_enabled():
        from .portal.routes import portal_startup

        await portal_startup()
        logger.info("Standalone auth portal live at / (login, signup, admin)")

    # Run the MCP Streamable HTTP session manager for the lifetime of the app
    # so the /mcp endpoint (mounted below) is live while the server serves.
    async with mcp_server.session_manager.run():
        logger.info("MCP server live at /mcp (Streamable HTTP)")
        yield

    if _portal_enabled():
        from .portal.routes import portal_shutdown

        await portal_shutdown()

    await close_db()
    logger.info("Server shut down.")


# Reverse-proxy mount prefix (ADR-0001 dual-deployability). Read once at import
# from MAPCONTROL_ROOT_PATH (normalized in config.load_config) and handed to
# FastAPI as the ASGI root_path. Default "" → served at root, so local dev /
# docker-compose.local.yml / direct-port / internal M2M are byte-for-byte
# unchanged. When set (e.g. "/service/map") Starlette auto-prefixes
# request.base_url AND still routes both prefixed (browser-via-proxy) and
# un-prefixed (internal M2M) paths — see server/tests/test_mcp.py.
_ROOT_PATH = load_config().server.root_path

app = FastAPI(
    title="Map Control Server",
    description="Remote-controllable map proxy server",
    version="0.1.0",
    lifespan=lifespan,
    root_path=_ROOT_PATH,
)


# CORS — config-driven allowlist (Phase 1 edge hardening; see
# .vision-documents/mcp-compliance-roadmap.md). Dual-deployability invariant: the DEFAULT
# (env unset) stays permissive `*` so local dev / docker-compose.local.yml work
# untouched; the CLOUD env-file sets MAPCONTROL_ALLOWED_ORIGINS to the real
# browser origins (e.g. the Svelte UI) to lock the public surface down.
#
# Comma-separated, e.g.:
#   MAPCONTROL_ALLOWED_ORIGINS=https://app.example.com,http://18.116.107.200:8888
# A literal "*" (or unset) means allow-all.
_cors_origins_raw = os.environ.get("MAPCONTROL_ALLOWED_ORIGINS", "*").strip()
_allow_all_origins = _cors_origins_raw == "" or _cors_origins_raw == "*"
_cors_allow_origins = (
    ["*"]
    if _allow_all_origins
    else [o.strip() for o in _cors_origins_raw.split(",") if o.strip()]
)
# Per the CORS spec a wildcard origin cannot be combined with credentials, and
# Starlette silently drops the ACAO header if you try. Only enable credentials
# when origins are explicitly enumerated (the locked-down cloud path).
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_allow_origins,
    allow_credentials=not _allow_all_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Register routers
app.include_router(maps.router)
app.include_router(sessions.router)
app.include_router(events.router)
app.include_router(assets.router)
app.include_router(files.router)


# ─── WebSocket endpoint ──────────────────────────────────────────────────────

@app.websocket("/ws/{map_id}/{user_session_id}")
async def websocket_endpoint(websocket: WebSocket, map_id: str, user_session_id: str):
    """WebSocket connection for real-time map events."""
    await manager.connect(websocket, map_id, user_session_id)

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            msg_type = msg.get("type", "")

            if msg_type == "session_restore":
                # Send full state snapshot
                snapshot = await session_service.get_session_snapshot(
                    map_id, user_session_id
                )
                await websocket.send_text(
                    json.dumps(
                        {"type": "session_snapshot", "data": snapshot.model_dump()}
                    )
                )

            elif msg_type == "viewport_update":
                # Store viewport state
                viewport_data = msg.get("data", {})
                viewport = Viewport(
                    center=viewport_data.get("center"),
                    zoom=viewport_data.get("zoom"),
                    bbox=viewport_data.get("bbox"),
                    pitch=viewport_data.get("pitch"),
                    bearing=viewport_data.get("bearing"),
                )
                basemap = viewport_data.get("basemap")
                terrain = viewport_data.get("terrain")
                await session_service.update_viewport(
                    map_id, user_session_id, viewport, basemap, terrain
                )

            elif msg_type == "screenshot_result":
                # Browser has captured a screenshot and sent it back
                correlation_id = msg.get("correlation_id", "")
                base64_png = msg.get("data", "")
                error = msg.get("error")
                if error:
                    await screenshot_service.resolve_ws_screenshot_error(
                        correlation_id, error
                    )
                else:
                    await screenshot_service.resolve_ws_screenshot(
                        correlation_id, base64_png
                    )

            elif msg_type == "user_drawn_feature":
                # Store user-drawn features as assets and broadcast to all sessions
                from .services import asset_service, event_service
                from .models import AssetStyle

                draw_data = msg.get("data", {})
                geojson_str = draw_data.get("geojson", "")
                draw_type = draw_data.get("draw_type", "polygon")
                # Client-generated correlation id: the drawing browser renders
                # the shape immediately in a "pending" style and swaps it for
                # the confirmed asset when this id round-trips.
                client_id = draw_data.get("client_id", "")
                asset_type = f"drawn_{draw_type}"
                name = draw_data.get("name", f"User drawn {draw_type}")

                # Default style for drawn features: the EOGPT earth-green
                # accent (--eo-accent #7cc242, darker #5fa830 stroke) so
                # user-drawn shapes read as part of the product, distinct
                # from agent-added analysis layers.
                style = AssetStyle(
                    fill_color="#7cc242",
                    stroke_color="#5fa830",
                    stroke_width=2.0,
                )

                try:
                    asset = await asset_service.create_asset(
                        map_id=map_id,
                        asset_type=asset_type,
                        geojson=geojson_str,
                        name=name,
                        style=style,
                    )
                    logger.info(
                        f"User drawn feature saved: asset_id={asset.asset_id} "
                        f"type={asset_type} map={map_id} session={user_session_id}"
                    )

                    # Broadcast the new asset to all sessions as an add_polygon
                    # event. client_id lets the drawing session replace its
                    # pending-styled shape; other sessions ignore it.
                    broadcast_msg = {
                        "type": "add_polygon",
                        "data": {
                            "asset_id": asset.asset_id,
                            "geojson": geojson_str,
                            "style": style.model_dump(),
                            "client_id": client_id,
                        },
                    }
                    await manager.broadcast_to_map(map_id, broadcast_msg)

                    # Send confirmation back to the drawing session
                    await websocket.send_text(json.dumps({
                        "type": "draw_complete",
                        "data": {
                            "asset_id": asset.asset_id,
                            "asset_type": asset_type,
                            "draw_type": draw_type,
                            "client_id": client_id,
                        },
                    }))
                except Exception as e:
                    logger.error(f"Failed to save drawn feature: {e}")
                    await websocket.send_text(json.dumps({
                        "type": "draw_error",
                        "data": {"error": str(e)},
                    }))

            elif msg_type == "user_delete_asset":
                # User clicked delete on an asset in the browser
                from .services import asset_service

                delete_data = msg.get("data", {})
                asset_id = delete_data.get("asset_id", "")

                if asset_id:
                    try:
                        await asset_service.delete_asset(map_id, asset_id)
                        logger.info(
                            f"User deleted asset: asset_id={asset_id} "
                            f"map={map_id} session={user_session_id}"
                        )

                        # Broadcast deletion to all sessions
                        broadcast_msg = {
                            "type": "delete_asset",
                            "data": {"asset_id": asset_id},
                        }
                        await manager.broadcast_to_map(map_id, broadcast_msg)
                    except Exception as e:
                        logger.error(f"Failed to delete asset {asset_id}: {e}")

    except WebSocketDisconnect:
        manager.disconnect(websocket, map_id)
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        manager.disconnect(websocket, map_id)


# ─── Map frontend serving ────────────────────────────────────────────────────

# Check for built frontend
FRONTEND_DIST = Path(__file__).parent.parent.parent / "frontend" / "dist"


@app.get("/map/{map_id}")
async def serve_map(map_id: str, request: Request):
    """Serve the map frontend for a given map ID."""
    # Check if the map exists
    info = await session_service.get_map(map_id)

    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        return FileResponse(FRONTEND_DIST / "index.html")

    # Fallback: inline minimal HTML that loads MapLibre GL JS from CDN
    # This allows the system to work even without a frontend build
    config = _config or load_config()

    def _thumbnail_url(entry) -> str:
        """Cheap preview thumbnail for the picker dropdown. Both kinds get
        a single raster tile at z=2, x=2, y=1 (covers Europe + Africa —
        diverse land/ocean content). For vector entries the style.json
        URL is rewritten to the matching raster-tile endpoint, since
        MapTiler serves raster of every style at /maps/{id}/{z}/{x}/{y}.png
        and the Static Maps API requires a paid plan."""
        url = entry.url
        if entry.kind == "vector":
            return url.replace("/style.json", "/2/2/1.png")
        return url.replace("{z}", "2").replace("{x}", "2").replace("{y}", "1")

    # Serialize the full BasemapEntry dict — the frontend builds sources,
    # layers, and the picker UI from this payload so adding/removing
    # basemaps in config.toml needs zero JS changes.
    basemaps_json = json.dumps({
        name: {
            "label": entry.label or name,
            "kind": entry.kind,
            "url": entry.url,
            "attribution": entry.attribution,
            "tile_size": entry.tile_size,
            "max_zoom": entry.max_zoom,
            "group": entry.group,
            "thumbnail": _thumbnail_url(entry),
        }
        for name, entry in config.map.basemaps.items()
    })

    # Theme resolution (light | dark | auto). Priority:
    #   1. ?theme= query param (explicit per-request override, e.g. an
    #      embedding page forcing a match with its own chrome)
    #   2. the MAP-level theme stored at create_map / set_theme
    #   3. "auto" — the page follows the viewer's OS/browser
    #      prefers-color-scheme (resolved client-side, live-updating).
    # "auto" is resolved in JS below; the server renders the initial
    # data-theme as "light" for auto (corrected before first paint by an
    # inline script so there is no flash on dark-preference systems).
    requested_theme = (request.query_params.get("theme") or "").lower()
    if requested_theme in ("light", "dark", "auto"):
        theme_mode = requested_theme
    else:
        theme_mode = await session_service.get_map_theme(map_id)
    theme = theme_mode if theme_mode in ("light", "dark") else "light"

    # Basemap resolution. Priority:
    #   1. ?basemap=<key> query param (embedding page override)
    #   2. the MAP-level basemap pinned at create_map / set_basemap
    #   3. theme-appropriate server default: when the map's theme is DARK and
    #      nothing was explicitly chosen, use default_dark_basemap (keyless
    #      Carto Dark Matter) so "dark mode" visibly darkens the map itself,
    #      not just the chrome. Explicit choices always win.
    #   4. the configured default_basemap.
    # Unknown keys are silently ignored at each step.
    default_basemap = config.map.default_basemap
    if theme_mode == "dark" and config.map.default_dark_basemap:
        default_basemap = config.map.default_dark_basemap
    map_level_basemap = await session_service.get_map_basemap(map_id)
    if map_level_basemap and map_level_basemap in config.map.basemaps:
        default_basemap = map_level_basemap
    requested_basemap = request.query_params.get("basemap")
    if requested_basemap and requested_basemap in config.map.basemaps:
        default_basemap = requested_basemap

    # ?ui=none|controls|default decides how much chrome the served map grows.
    #   none     — naked canvas: no basemap picker, no draw tools, no embed
    #              outfit. Only renders assets + emits interaction events
    #              (the canonical ESIP design; what slide embeds want).
    #   controls — native map controls (basemap picker + draw toolbar +
    #              attribution) but NO embed outfit. What embedders with
    #              their own management UI (e.g. EOGPT) want.
    #   default  — controls PLUS the built-in "default outfit" (layer panel
    #              + hover cards), an opt-in reference consumer built
    #              entirely on the public event/REST contract.
    # Falls back to the server-configured default (config.map.default_ui).
    # Unknown values fall back too, so older/newer clients degrade safely.
    _UI_MODES = ("none", "controls", "default")
    default_ui = getattr(config.map, "default_ui", "none")
    requested_ui = (request.query_params.get("ui") or "").lower()
    ui = requested_ui if requested_ui in _UI_MODES else default_ui
    if ui not in _UI_MODES:
        ui = "none"


    # Reverse-proxy mount prefix (ADR-0001 dual-deployability). When ESIP runs
    # behind a single-origin proxy under a sub-path (MAPCONTROL_ROOT_PATH →
    # uvicorn --root-path), Starlette puts that prefix on request.scope.
    # request.base_url is auto-prefixed by it, so every BASE_URL-derived URL
    # below already resolves correctly; the ONLY emitted URLs that bypass
    # base_url — and therefore need manual prefixing — are the root-absolute
    # /static asset tags (rendered here) and the WebSocket URLs (built client
    # side from window.location.host, see ROOT_PATH in the inline script).
    # Default "" = served at root → local/dev/M2M byte-for-byte unchanged.
    root_path = (request.scope.get("root_path") or "").rstrip("/")

    # Static-asset injection. The interaction contract (esip-contract.js) is
    # ALWAYS loaded so even a naked map emits asset_hover/asset_click/map_click
    # and publishes window.ESIPMap for bespoke UIs. The default outfit
    # (esip-embed.css/js) is loaded only when ui == "default".
    ui_head = (
        f'<link rel="stylesheet" href="{root_path}/static/esip-embed.css">'
        if ui == "default" else ""
    )
    ui_body = f'<script src="{root_path}/static/esip-contract.js"></script>'
    if ui == "default":
        ui_body += f'<script src="{root_path}/static/esip-embed.js"></script>'


    # Initial camera resolution (server-side). The page used to ALWAYS boot at
    # the config default (world view) and only move after the WebSocket
    # session_restore arrived — so a headless screenshot page spent its whole
    # tile budget loading the wrong view, then jumped and captured mid-load.
    # Now the effective viewport (own session → map-level zoom_* persistence /
    # latest live session, newest wins → assets bbox) is resolved HERE and
    # rendered into DEFAULTS, so the first tiles requested are already the
    # right ones. session_restore still applies the same view (idempotent).
    initial_center = [config.map.defaults.center_lon, config.map.defaults.center_lat]
    initial_zoom = config.map.defaults.zoom
    initial_bounds = None
    _requesting_session = request.query_params.get("user_session") or ""
    _effective_vp = await session_service.get_effective_viewport(
        map_id, _requesting_session
    )
    if _effective_vp is not None:
        if _effective_vp.center:
            initial_center = _effective_vp.center
            if _effective_vp.zoom is not None:
                initial_zoom = _effective_vp.zoom
        elif _effective_vp.bbox and len(_effective_vp.bbox) == 4:
            initial_bounds = _effective_vp.bbox

    defaults_json = json.dumps({
        "center": initial_center,
        "zoom": initial_zoom,
        "bounds": initial_bounds,  # [minLon, minLat, maxLon, maxLat] | null
        "basemap": default_basemap,
        "terrain": config.map.default_terrain,
    })
    style_json = json.dumps({
        "fill_color": config.map.style.fill_color,
        "stroke_color": config.map.style.stroke_color,
        "stroke_width": config.map.style.stroke_width,
    })

    base_url = str(request.base_url).rstrip("/")

    html = f"""<!DOCTYPE html>
<html lang="en" data-theme="{theme}" data-esip-ui="{ui}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Map Control</title>
    <link rel="stylesheet" href="https://unpkg.com/maplibre-gl@5.23.0/dist/maplibre-gl.css">
<!-- Terra Draw is headless (no stylesheet) — the draw toolbar is our own themed UI -->
    {ui_head}
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap">
    <style>
        /* EO-GPT theme tokens — light/dark variants picked by ?theme query.
           Earth-green accent and slate-navy darks match the rest of the
           app's design system. */
        :root[data-theme="light"] {{
            --eo-bg: #ffffff;
            --eo-surface: #ffffff;
            --eo-surface-hover: rgba(0, 0, 0, 0.04);
            --eo-border: rgba(0, 0, 0, 0.08);
            --eo-text: #0f172a;
            --eo-text-muted: #475569;
            --eo-accent: #5fa830;
            --eo-accent-soft: rgba(124, 194, 66, 0.18);
            --eo-shadow: 0 8px 24px -8px rgba(15, 23, 42, 0.16), 0 2px 6px -2px rgba(15, 23, 42, 0.08);
        }}
        :root[data-theme="dark"] {{
            --eo-bg: #0d141d;
            --eo-surface: #18212e;
            --eo-surface-hover: rgba(255, 255, 255, 0.06);
            --eo-border: rgba(255, 255, 255, 0.08);
            --eo-text: #dbe3ee;
            --eo-text-muted: #94a3b8;
            --eo-accent: #7cc242;
            --eo-accent-soft: rgba(124, 194, 66, 0.16);
            --eo-shadow: 0 8px 24px -8px rgba(0, 0, 0, 0.6), 0 2px 6px -2px rgba(0, 0, 0, 0.4);
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body, #map {{ width: 100%; height: 100%; font-family: 'Inter', system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }}
        /* Theme the canvas surround ("space" around the globe / letterboxing):
           MapLibre's canvas is transparent where the style paints nothing, so
           the page background shows through. Bind it to the theme tokens so a
           dark map is visibly dark even at globe zoom, live-updating with
           data-theme flips from set_theme / prefers-color-scheme. */
        html, body, #map {{ background: var(--eo-bg); }}
        /* ─── ?ui=none — the naked canvas ─────────────────────────────────
           The canonical ESIP design: a bare map that only renders assets and
           emits interaction events. No draw toolbar, no basemap picker, no
           custom buttons, no on-map attribution pill. JS below also skips
           building the draw toolbar and the picker control entirely; these
           rules are the belt-and-suspenders layer for anything that still
           lands in the DOM.
           ATTRIBUTION CONTRACT: hiding the pill shifts the provider-credit
           obligation to the EMBEDDING PAGE — anyone iframing a ui=none map
           must render basemap attribution (e.g. "© Esri, © OpenStreetMap
           contributors · MapLibre") in their own chrome. ui=default keeps
           the on-map pill. */
        html[data-esip-ui="none"] .maplibregl-ctrl-top-left,
        html[data-esip-ui="none"] .maplibregl-ctrl-bottom-right,
        html[data-esip-ui="none"] .basemap-picker-wrap,
        html[data-esip-ui="none"] .draw-toolbar {{
            display: none !important;
        }}
        /* Custom toolbar buttons (undo, pan, delete) */
        /* ─── Draw toolbar (Terra Draw is headless — this is our own UI) ──
           Themed via the tokens above so it follows light/dark live, exactly
           like the basemap picker. One vertical group, top-left, mirroring
           the MapLibre control aesthetic without depending on it. */
        .draw-toolbar {{
            display: flex;
            flex-direction: column;
            background: var(--eo-surface);
            border: 1px solid var(--eo-border);
            border-radius: 8px;
            box-shadow: var(--eo-shadow);
            overflow: hidden;
        }}
        .draw-toolbar button {{
            width: 32px;
            height: 32px;
            border: none;
            border-bottom: 1px solid var(--eo-border);
            background: transparent;
            color: var(--eo-text-muted);
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: background 0.15s, color 0.15s;
        }}
        .draw-toolbar button:last-child {{ border-bottom: none; }}
        .draw-toolbar button:hover {{ background: var(--eo-surface-hover); color: var(--eo-text); }}
        .draw-toolbar button[aria-pressed="true"] {{
            background: var(--eo-accent-soft);
            color: var(--eo-accent);
        }}
        .draw-toolbar button svg {{ width: 18px; height: 18px; display: block; }}
        .delete-mode-active {{ cursor: crosshair !important; }}
        /* ─── Basemap picker (collapsed dropdown) ─────────────────────────
           A trigger button that expands into a grouped thumbnail grid.
           Styled to match the EO-GPT palette via the theme tokens above.
           Wrapped in a .maplibregl-ctrl-group so it sits alongside native
           controls visually but with our own typography + accent. */
        .basemap-picker-wrap {{
            font-family: 'Inter', system-ui, -apple-system, sans-serif;
            position: relative;
            color: var(--eo-text);
        }}
        .basemap-trigger {{
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 180px;
            padding: 8px 12px;
            border: 1px solid var(--eo-border);
            border-radius: 6px;
            background: var(--eo-surface);
            color: var(--eo-text);
            font-family: inherit;
            font-size: 12px;
            font-weight: 500;
            cursor: pointer;
            box-shadow: var(--eo-shadow);
            transition: border-color 120ms ease, background 120ms ease;
        }}
        .basemap-trigger:hover {{ border-color: var(--eo-accent); }}
        .basemap-trigger[aria-expanded="true"] {{ border-color: var(--eo-accent); }}
        .basemap-trigger-label {{
            flex: 1;
            text-align: left;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .basemap-trigger-caret {{
            width: 12px;
            height: 12px;
            color: var(--eo-text-muted);
            flex-shrink: 0;
            transition: transform 160ms ease;
        }}
        .basemap-trigger[aria-expanded="true"] .basemap-trigger-caret {{
            transform: rotate(180deg);
        }}
        .basemap-panel {{
            position: absolute;
            top: calc(100% + 6px);
            right: 0;
            min-width: 320px;
            max-height: 480px;
            overflow-y: auto;
            padding: 8px;
            background: var(--eo-surface);
            border: 1px solid var(--eo-border);
            border-radius: 8px;
            box-shadow: var(--eo-shadow);
            display: none;
        }}
        .basemap-panel[data-open="true"] {{ display: block; }}
        .basemap-group-label {{
            padding: 8px 6px 4px;
            font-size: 10px;
            font-weight: 600;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            color: var(--eo-text-muted);
        }}
        .basemap-group-grid {{
            display: grid;
            /* minmax(0, 1fr) instead of 1fr — without it the default
               min-content sizing lets long labels (e.g. "MapTiler
               Satellite Dark") push their column wider than the others,
               which makes the thumbs visibly different sizes across rows. */
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 6px;
            padding: 2px;
        }}
        .basemap-tile {{
            display: flex;
            flex-direction: column;
            padding: 4px;
            border: 1px solid var(--eo-border);
            border-radius: 6px;
            background: transparent;
            color: var(--eo-text);
            cursor: pointer;
            font-family: inherit;
            transition: background 120ms ease, border-color 120ms ease;
        }}
        .basemap-tile:hover {{
            background: var(--eo-surface-hover);
            border-color: var(--eo-text-muted);
        }}
        .basemap-tile[aria-pressed="true"] {{
            background: var(--eo-accent-soft);
            border-color: var(--eo-accent);
        }}
        .basemap-tile-thumb {{
            /* Source tiles are square (256×256 native), so a 1:1 aspect
               ratio shows the whole tile without crop variance. Combined
               with the fixed grid columns this guarantees identical
               pixel dimensions across every preview. */
            width: 100%;
            aspect-ratio: 1 / 1;
            border-radius: 4px;
            background: var(--eo-surface-hover) center/cover no-repeat;
            border: 1px solid var(--eo-border);
        }}
        .basemap-tile-label {{
            margin-top: 6px;
            font-size: 11px;
            font-weight: 500;
            line-height: 1.2;
            text-align: center;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
    </style>
</head>
<body>
    <div id="map"></div>
    <script src="https://unpkg.com/maplibre-gl@5.23.0/dist/maplibre-gl.js"></script>
    <!-- Terra Draw (headless drawing, community-maintained) + its MapLibre
         adapter. Core must load before the adapter (UMD dependency). -->
    <script src="https://unpkg.com/terra-draw@1.33.0/dist/terra-draw.umd.js"></script>
    <script src="https://unpkg.com/terra-draw-maplibre-gl-adapter@1.4.1/dist/terra-draw-maplibre-gl-adapter.umd.js"></script>
    <script src="https://unpkg.com/deck.gl@^9.0.0/dist.min.js"></script>
    <script>
        const MAP_ID = "{map_id}";
        const USER_SESSION = new URLSearchParams(window.location.search).get('user_session') || '';
        const BASE_URL = "{base_url}";
        // Reverse-proxy mount prefix (ADR-0001). "" at root; "/service/map"
        // behind the single-origin proxy. window.location.host has no path, so
        // WebSocket URLs (which can't use BASE_URL) must prepend this manually.
        const ROOT_PATH = "{root_path}";
        const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const WS_URL = wsProto + '//' + window.location.host + ROOT_PATH + '/ws/' + MAP_ID + '/' + USER_SESSION;

        const BASEMAPS = {basemaps_json};
        const DEFAULTS = {defaults_json};
        const DEFAULT_STYLE = {style_json};

        // ─── Theme (light | dark | auto) ───
        // Map-level UI theme. "auto" follows the viewer's OS/browser
        // prefers-color-scheme and live-updates on OS changes. Explicit
        // light/dark (from create_map, set_theme, or ?theme=) pins it.
        let themeMode = "{theme_mode}";  // light | dark | auto
        const _darkMQ = window.matchMedia
            ? window.matchMedia('(prefers-color-scheme: dark)') : null;
        function resolvedTheme() {{
            if (themeMode === 'dark' || themeMode === 'light') return themeMode;
            return (_darkMQ && _darkMQ.matches) ? 'dark' : 'light';
        }}
        function applyTheme(mode) {{
            themeMode = (mode === 'dark' || mode === 'light') ? mode : 'auto';
            document.documentElement.setAttribute('data-theme', resolvedTheme());
        }}
        if (_darkMQ && _darkMQ.addEventListener) {{
            _darkMQ.addEventListener('change', function () {{
                if (themeMode === 'auto') applyTheme('auto');
            }});
        }}
        // Correct the server-rendered default before anything paints when
        // the mode is auto and the viewer prefers dark.
        applyTheme(themeMode);

        // ─── Asset Registry ───
        // Tracks layer IDs and GeoJSON bounds for each asset
        const assetRegistry = {{}};  // asset_id -> {{ layerIds, bounds, srcId }}

        let currentBasemap = DEFAULTS.basemap;
        let currentTerrain = DEFAULTS.terrain || '2d';  // '2d' or '3d'

        // ─── MapLibre Map ───
        // Sources and layers are built from the server-provided BASEMAPS
        // dict so adding providers in config.toml requires no JS changes.
        // Raster basemap layer ids match their basemap id; isBasemapLayer()
        // lets the rest of the code skip them when ordering user assets.
        // Vector basemaps live in style.json (loaded via setStyle on switch).
        const basemapIds = Object.keys(BASEMAPS);
        function isBasemapLayer(layerId) {{ return basemapIds.includes(layerId); }}

        // Track which kind of basemap is currently shown so set_basemap
        // knows whether it can take the cheap visibility-flip path or has
        // to do a full setStyle (which wipes user assets — see
        // captureMapState/restoreMapState).
        let currentBasemapKind = (BASEMAPS[DEFAULTS.basemap] || {{}}).kind || 'raster';

        // Build a MapLibre style spec containing one raster source+layer per
        // raster basemap. Vector basemaps are excluded — they get loaded via
        // setStyle(style.json URL) on switch. Used both at init and when
        // switching from a vector basemap back to a raster one.
        function buildRasterStyle(visibleId) {{
            const sources = {{}};
            const layers = [];
            for (const [id, entry] of Object.entries(BASEMAPS)) {{
                if ((entry.kind || 'raster') !== 'raster') continue;
                sources[id] = {{
                    type: 'raster',
                    tiles: [entry.url],
                    tileSize: entry.tile_size || 256,
                    attribution: entry.attribution || '',
                    maxzoom: entry.max_zoom || 22,
                }};
                layers.push({{
                    id: id, type: 'raster', source: id,
                    layout: {{ visibility: visibleId === id ? 'visible' : 'none' }},
                }});
            }}
            return {{
                version: 8,
                // Glyphs endpoint is required for symbol (text label) layers.
                // Vector basemaps bring their own glyphs via style.json.
                glyphs: 'https://demotiles.maplibre.org/font/{{fontstack}}/{{range}}.pbf',
                sources,
                layers,
            }};
        }}

        // ─── Basemap picker control ───
        // Collapsed dropdown trigger that opens a grouped thumbnail grid.
        // Three sections: SATELLITE / NAVIGATION / DATA, matching the
        // `group` field on each BasemapEntry. Clicking a thumbnail funnels
        // through the shared set_basemap handler so the picker, the SDK,
        // and snapshot restore all share one code path.
        const _basemapTiles = {{}};   // id -> <button> (tile)
        const _groupOrder = ['satellite', 'navigation', 'data'];
        const _groupLabels = {{ satellite: 'Satellite', navigation: 'Navigation', data: 'Data' }};
        let _triggerLabel = null;

        function updateBasemapPickerActive() {{
            for (const id of basemapIds) {{
                const tile = _basemapTiles[id];
                if (tile) tile.setAttribute('aria-pressed', id === currentBasemap ? 'true' : 'false');
            }}
            if (_triggerLabel && BASEMAPS[currentBasemap]) {{
                _triggerLabel.textContent = BASEMAPS[currentBasemap].label || currentBasemap;
            }}
        }}

        class BasemapPickerControl {{
            onAdd(mapInstance) {{
                this._map = mapInstance;
                this._container = document.createElement('div');
                this._container.className = 'maplibregl-ctrl basemap-picker-wrap';
                if (basemapIds.length < 2) {{
                    this._container.style.display = 'none';
                    return this._container;
                }}

                // Trigger: shows current basemap label + caret.
                const trigger = document.createElement('button');
                trigger.type = 'button';
                trigger.className = 'basemap-trigger';
                trigger.setAttribute('aria-expanded', 'false');
                trigger.setAttribute('aria-haspopup', 'true');
                trigger.innerHTML = '<span class="basemap-trigger-label"></span><svg class="basemap-trigger-caret" viewBox="0 0 12 12" fill="none" xmlns="http://www.w3.org/2000/svg"><path d="M3 4.5L6 7.5L9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>';
                _triggerLabel = trigger.querySelector('.basemap-trigger-label');

                // Panel: grouped thumbnail grid.
                const panel = document.createElement('div');
                panel.className = 'basemap-panel';
                panel.setAttribute('data-open', 'false');

                // Bucket entries by group, preserving insertion order within each.
                const buckets = new Map(_groupOrder.map(g => [g, []]));
                for (const id of basemapIds) {{
                    const entry = BASEMAPS[id];
                    const group = buckets.has(entry.group) ? entry.group : 'navigation';
                    buckets.get(group).push(id);
                }}

                for (const group of _groupOrder) {{
                    const ids = buckets.get(group);
                    if (!ids || ids.length === 0) continue;
                    const label = document.createElement('div');
                    label.className = 'basemap-group-label';
                    label.textContent = _groupLabels[group] || group;
                    panel.appendChild(label);
                    const grid = document.createElement('div');
                    grid.className = 'basemap-group-grid';
                    for (const id of ids) {{
                        const entry = BASEMAPS[id];
                        const tile = document.createElement('button');
                        tile.type = 'button';
                        tile.className = 'basemap-tile';
                        tile.setAttribute('aria-pressed', id === currentBasemap ? 'true' : 'false');
                        tile.title = entry.attribution
                            ? entry.label + ' — ' + entry.attribution.replace(/<[^>]+>/g, '')
                            : entry.label;
                        const thumb = document.createElement('span');
                        thumb.className = 'basemap-tile-thumb';
                        if (entry.thumbnail) {{
                            thumb.style.backgroundImage = `url("${{entry.thumbnail}}")`;
                        }}
                        const labelEl = document.createElement('span');
                        labelEl.className = 'basemap-tile-label';
                        // Drop the redundant "MapTiler " prefix in the
                        // tile label — the group headers + tooltip carry
                        // the provider context and short labels fit the
                        // 95px column without truncation.
                        labelEl.textContent = entry.label.replace(/^MapTiler\\s+/, '');
                        tile.appendChild(thumb);
                        tile.appendChild(labelEl);
                        tile.addEventListener('click', () => {{
                            handlers.set_basemap({{ basemap: id }});
                            setOpen(false);
                        }});
                        _basemapTiles[id] = tile;
                        grid.appendChild(tile);
                    }}
                    panel.appendChild(grid);
                }}

                // ── Availability probe ─────────────────────────────────
                // A basemap whose preview tile can't load almost certainly
                // can't serve map tiles either (invalid/limited API key,
                // plan-gated style, provider outage) — config-load only
                // drops entries whose key env var is UNSET, so a set-but-
                // broken key still lists them. Probe each thumbnail and
                // remove failing tiles instead of showing blank previews
                // that break when clicked. The current basemap is never
                // removed (it's demonstrably rendering).
                const containerEl = this._container;
                const pruneEmptyGroups = () => {{
                    panel.querySelectorAll('.basemap-group-label').forEach((label) => {{
                        const grid = label.nextElementSibling;
                        if (grid && grid.classList.contains('basemap-group-grid') &&
                            grid.children.length === 0) {{
                            grid.remove();
                            label.remove();
                        }}
                    }});
                    if (Object.keys(_basemapTiles).length < 2) {{
                        containerEl.style.display = 'none';
                    }}
                }};
                for (const id of basemapIds) {{
                    const entry = BASEMAPS[id];
                    if (!entry.thumbnail) continue;
                    const probe = new Image();
                    probe.onerror = () => {{
                        if (id === currentBasemap) return;
                        const tile = _basemapTiles[id];
                        if (!tile) return;
                        tile.remove();
                        delete _basemapTiles[id];
                        pruneEmptyGroups();
                        console.warn('Basemap preview tile failed — hidden from picker:', id);
                    }};
                    probe.src = entry.thumbnail;
                }}

                const setOpen = (open) => {{
                    trigger.setAttribute('aria-expanded', open ? 'true' : 'false');
                    panel.setAttribute('data-open', open ? 'true' : 'false');
                }};
                trigger.addEventListener('click', (e) => {{
                    e.stopPropagation();
                    const isOpen = trigger.getAttribute('aria-expanded') === 'true';
                    setOpen(!isOpen);
                }});
                // Close on outside click or Escape.
                document.addEventListener('click', (e) => {{
                    if (!this._container.contains(e.target)) setOpen(false);
                }});
                document.addEventListener('keydown', (e) => {{
                    if (e.key === 'Escape') setOpen(false);
                }});

                this._container.appendChild(trigger);
                this._container.appendChild(panel);
                // Seed the trigger label.
                updateBasemapPickerActive();
                return this._container;
            }}
            onRemove() {{
                this._container.parentNode && this._container.parentNode.removeChild(this._container);
                this._map = undefined;
            }}
        }}

        // If the default basemap is vector, init with its style.json URL
        // directly. Otherwise build a raster-only style covering every
        // raster basemap. Either way the style loaded here is PERMANENT:
        // every raster basemap is injected into it (hidden) right after
        // load, and switching among the loaded vector basemap plus any
        // raster basemap is pure visibility toggling — see set_basemap. setStyle
        // (a destructive full-style rebuild that wipes the draw tool's layers
        // and user assets) only remains for the rare "switch to a vector
        // style that is NOT the loaded one" case.
        const _defaultEntry = BASEMAPS[DEFAULTS.basemap] || {{}};
        const _initialStyle = (_defaultEntry.kind === 'vector')
            ? _defaultEntry.url
            : buildRasterStyle(DEFAULTS.basemap);

        const _mapInit = {{
            container: 'map',
            style: _initialStyle,
            center: DEFAULTS.center,
            zoom: DEFAULTS.zoom,
            preserveDrawingBuffer: true,
        }};
        // Server-resolved initial camera: when the map has a persisted or
        // inherited bbox viewport, start FITTED to it so the very first tiles
        // requested are the right ones (critical for headless screenshots).
        if (DEFAULTS.bounds) {{
            _mapInit.bounds = [
                [DEFAULTS.bounds[0], DEFAULTS.bounds[1]],
                [DEFAULTS.bounds[2], DEFAULTS.bounds[3]],
            ];
            _mapInit.fitBoundsOptions = {{ padding: 50, maxZoom: 18 }};
            delete _mapInit.center;
            delete _mapInit.zoom;
        }}
        const map = new maplibregl.Map(_mapInit);

        // ─── Merged-style basemap bookkeeping ───
        // Which vector basemap's layers live in the permanent style (null on
        // raster-init), and the ids of those layers so switches can toggle
        // the whole group.
        let loadedVectorBasemap = (_defaultEntry.kind === 'vector') ? DEFAULTS.basemap : null;
        let vectorLayerIds = [];

        function injectRasterBasemaps() {{
            // Record which layers belong to the loaded vector style BEFORE
            // injecting anything. Runs at style load (and after a legacy
            // vector rebuild) — before draw-tool init and asset restore — so
            // everything present that isn't a raster basemap is the vector
            // basemap's. Terra Draw's td-* layers filtered defensively anyway.
            if (loadedVectorBasemap) {{
                vectorLayerIds = map.getStyle().layers.map(l => l.id)
                    .filter(id => !basemapIds.includes(id) && !id.startsWith('td-'));
            }}
            const firstId = (map.getStyle().layers[0] || {{}}).id;
            for (const [id, entry] of Object.entries(BASEMAPS)) {{
                if ((entry.kind || 'raster') !== 'raster') continue;
                // Guard on OUR layer id, and namespace the source id: vector
                // styles can ship sources whose ids collide with basemap ids
                // (MapTiler hybrid has a source literally named "satellite"),
                // which silently blocked that basemap's injection.
                if (map.getLayer(id)) continue;  // raster-init already has them
                const srcId = 'bm-src-' + id;
                if (!map.getSource(srcId)) {{
                    map.addSource(srcId, {{
                        type: 'raster',
                        tiles: [entry.url],
                        tileSize: entry.tile_size || 256,
                        attribution: entry.attribution || '',
                        maxzoom: entry.max_zoom || 22,
                    }});
                }}
                // Below everything else: user assets and vector labels
                // render above an active raster basemap.
                map.addLayer({{
                    id: id, type: 'raster', source: srcId,
                    layout: {{ visibility: currentBasemap === id ? 'visible' : 'none' }},
                }}, firstId);
            }}
        }}

        map.once('style.load', () => {{
            injectRasterBasemaps();
            // Apply the default view mode NOW, before the first settled
            // paint, not at 'load' (which waits for tiles): flipping
            // mercator→globe seconds after first paint read as the map
            // "resetting and zooming out", and left vector labels unplaced
            // until the next interaction.
            if (currentTerrain && currentTerrain !== '2d') {{
                try {{ map.setProjection({{ type: 'globe' }}); }} catch(e) {{}}
                enableSky();
                map.jumpTo({{ pitch: 0, bearing: 0 }});
            }}
        }});

        // ?ui=none — naked canvas: skip every human control (basemap picker,
        // draw toolbar). The page still renders assets, replays events,
        // and emits the interaction contract; it just grows no chrome.
        const UI_NAKED = document.documentElement.getAttribute('data-esip-ui') === 'none';

        // Basemap picker — top-right, sits above MapLibre's native attribution
        // control. The control hides itself when fewer than 2 basemaps are
        // configured (e.g. dev install without MAPTILER_API_KEY and someone
        // commented out the satellite entry).
        if (!UI_NAKED) map.addControl(new BasemapPickerControl(), 'top-right');

        // ─── Helpers ───
        function getStyleProp(style, prop, fallback) {{
            return (style && style[prop]) || fallback;
        }}

        // Atmospheric sky for 3D/globe terrain modes. MapLibre GL JS has no
        // `sky` *layer* type (that is Mapbox's API) — it uses map.setSky().
        // Adding a `type: 'sky'` layer fails style validation and, because the
        // error is fired on the map's error event rather than thrown, a
        // try/catch around addLayer cannot suppress it. setSky is the correct,
        // validation-clean API (MapLibre 5+).
        function enableSky() {{
            try {{
                map.setSky({{
                    'sky-color': '#199EF3',
                    'sky-horizon-blend': 0.5,
                    'horizon-color': '#ffffff',
                    'horizon-fog-blend': 0.5,
                    'fog-color': '#ffffff',
                    'fog-ground-blend': 0.5,
                }});
            }} catch(e) {{ /* setSky unsupported in this MapLibre version */ }}
        }}
        function disableSky() {{
            try {{ map.setSky(undefined); }} catch(e) {{}}
        }}

        // Compute LngLatBounds from a GeoJSON object
        function geojsonBounds(geojson) {{
            const bounds = new maplibregl.LngLatBounds();
            function addCoord(c) {{ bounds.extend(c); }}
            function walkCoords(coords, depth) {{
                if (depth === 0) {{ addCoord(coords); return; }}
                for (const item of coords) walkCoords(item, depth - 1);
            }}
            function walkGeometry(geom) {{
                if (!geom) return;
                const t = geom.type;
                if (t === 'Point') addCoord(geom.coordinates);
                else if (t === 'MultiPoint' || t === 'LineString') walkCoords(geom.coordinates, 1);
                else if (t === 'MultiLineString' || t === 'Polygon') walkCoords(geom.coordinates, 2);
                else if (t === 'MultiPolygon') walkCoords(geom.coordinates, 3);
                else if (t === 'GeometryCollection') (geom.geometries || []).forEach(walkGeometry);
            }}
            if (geojson.type === 'FeatureCollection') (geojson.features || []).forEach(f => walkGeometry(f.geometry));
            else if (geojson.type === 'Feature') walkGeometry(geojson.geometry);
            else walkGeometry(geojson);
            return bounds;
        }}

        // Detect geometry types present in a GeoJSON
        function detectGeomTypes(geojson) {{
            const types = new Set();
            function walk(geom) {{
                if (!geom) return;
                if (geom.type === 'Point' || geom.type === 'MultiPoint') types.add('point');
                else if (geom.type === 'LineString' || geom.type === 'MultiLineString') types.add('line');
                else if (geom.type === 'Polygon' || geom.type === 'MultiPolygon') {{ types.add('fill'); types.add('line'); }}
                else if (geom.type === 'GeometryCollection') (geom.geometries || []).forEach(walk);
            }}
            if (geojson.type === 'FeatureCollection') (geojson.features || []).forEach(f => walk(f.geometry));
            else if (geojson.type === 'Feature') walk(geojson.geometry);
            else walk(geojson);
            return types;
        }}

        // ─── Color ramps for data-driven styling (style.color_by) ───
        const COLOR_RAMPS = {{
            viridis: ['#440154', '#3b528b', '#21918c', '#5ec962', '#fde725'],
            magma:   ['#000004', '#51127c', '#b73779', '#fc8961', '#fcfdbf'],
            plasma:  ['#0d0887', '#7e03a8', '#cc4778', '#f89540', '#f0f921'],
            blues:   ['#f7fbff', '#c6dbef', '#6baed6', '#2171b5', '#08306b'],
            reds:    ['#fff5f0', '#fcbba1', '#fb6a4a', '#cb181d', '#67000d'],
            greens:  ['#f7fcf5', '#c7e9c0', '#74c476', '#238b45', '#00441b'],
        }};

        // Build a MapLibre interpolate expression from style.color_by:
        // {{property, ramp, domain}}. Domain defaults to the min/max of the
        // property across the GeoJSON's features. Returns null when not
        // applicable (no property / no numeric values).
        function colorByExpression(colorBy, geojson) {{
            if (!colorBy || !colorBy.property) return null;
            const ramp = COLOR_RAMPS[(colorBy.ramp || 'viridis').toLowerCase()] || COLOR_RAMPS.viridis;
            let domain = (Array.isArray(colorBy.domain) && colorBy.domain.length === 2)
                ? colorBy.domain : null;
            if (!domain) {{
                let min = Infinity, max = -Infinity;
                const feats = geojson.type === 'FeatureCollection' ? (geojson.features || [])
                    : geojson.type === 'Feature' ? [geojson] : [];
                for (const f of feats) {{
                    const v = (f && f.properties) ? Number(f.properties[colorBy.property]) : NaN;
                    if (isFinite(v)) {{ if (v < min) min = v; if (v > max) max = v; }}
                }}
                if (!isFinite(min) || !isFinite(max)) return null;
                if (min === max) max = min + 1;
                domain = [min, max];
            }}
            const expr = ['interpolate', ['linear'],
                ['to-number', ['get', colorBy.property], domain[0]]];
            for (let i = 0; i < ramp.length; i++) {{
                expr.push(domain[0] + (domain[1] - domain[0]) * i / (ramp.length - 1));
                expr.push(ramp[i]);
            }}
            return expr;
        }}

        // ─── Label (symbol) layer for an asset ───
        // style.label: true → asset/feature name; string → custom text.
        // style.label_placement: 'point' | 'center' | 'perimeter'
        //   (perimeter renders text ALONG the polygon outline / line).
        // Defaults: polygons → center, lines → perimeter, points → point.
        function addLabelLayer(assetId, srcId, s, vis, name, geomTypes) {{
            const label = s && s.label;
            if (!label) return null;
            // Custom string wins; label===true → per-feature 'name' property,
            // falling back to the asset name.
            const labelField = (typeof label === 'string')
                ? label
                : ['coalesce', ['get', 'name'], name || ''];
            let placement = (s && s.label_placement) || null;
            if (!placement) {{
                if (geomTypes.has('fill')) placement = 'center';
                else if (geomTypes.has('line')) placement = 'perimeter';
                else placement = 'point';
            }}
            const labelId = 'label-' + assetId;
            const layout = {{
                'text-field': labelField,
                'text-size': (s && s.label_size) || 14,
                'text-font': ['Open Sans Semibold'],
                visibility: vis,
            }};
            let filter = null;
            if (placement === 'perimeter') {{
                layout['symbol-placement'] = 'line';
                filter = ['any', ['==', '$type', 'Polygon'], ['==', '$type', 'LineString']];
            }} else if (placement === 'point') {{
                layout['symbol-placement'] = 'point';
                layout['text-anchor'] = 'bottom';
                layout['text-offset'] = [0, -0.8];
                layout['text-allow-overlap'] = true;
                filter = ['==', '$type', 'Point'];
            }} else {{
                // 'center' — one label anchored inside each polygon/feature
                layout['symbol-placement'] = 'point';
            }}
            const strokeColor = getStyleProp(s, 'stroke_color', DEFAULT_STYLE.stroke_color);
            // Crisp halo: contrast-aware rim instead of a soft white glow.
            // White text used to get a white halo (reads as BLUR over bright
            // imagery); now light text gets a dark rim and dark text a light
            // one — the standard cartographic treatment. halo-blur stays 0.
            const labelColor = (s && s.label_color) || strokeColor;
            function _lum(hex) {{
                if (!hex || typeof hex !== 'string' || hex[0] !== '#') return 0;
                let h = hex.slice(1);
                if (h.length === 3) h = h.split('').map(c => c + c).join('');
                const r = parseInt(h.slice(0, 2), 16) || 0;
                const g = parseInt(h.slice(2, 4), 16) || 0;
                const b = parseInt(h.slice(4, 6), 16) || 0;
                return (0.299 * r + 0.587 * g + 0.114 * b) / 255;
            }}
            const haloColor = _lum(labelColor) > 0.5
                ? 'rgba(0,0,0,0.78)' : 'rgba(255,255,255,0.85)';
            const spec = {{
                id: labelId, type: 'symbol', source: srcId,
                layout: layout,
                paint: {{
                    'text-color': labelColor,
                    'text-halo-color': haloColor,
                    'text-halo-width': 1.1,
                    'text-halo-blur': 0,
                }},
            }};
            if (filter) spec.filter = filter;
            try {{ map.addLayer(spec); }} catch (e) {{
                console.warn('Label layer failed (missing glyphs endpoint?):', e);
                return null;
            }}
            return labelId;
        }}

        // ─── Animation engine (style.animate / style.glow) ───────────────
        // One shared requestAnimationFrame loop drives every animated asset.
        // style.animate is a list of effects, each oscillating one numeric
        // paint property between `from` and `to` over `period` seconds:
        //   [{{"property": "opacity", "from": 0.35, "to": 1.0, "period": 1.2}},
        //    {{"property": "circle_radius", "from": 6, "to": 10, "period": 1.2}}]
        // Supported properties: opacity (fill/line/circle, composes with the
        // feature-state hover expression), circle_radius, stroke_width, and
        // ripple (a sonar-ping halo on point markers: an auxiliary ring
        // beneath the marker expands `from`→`to` px while fading to
        // transparent — sawtooth, not sine — then restarts).
        // style.glow (bool | {{period, min_opacity, max_opacity, stroke}}) is
        // kept as back-compat sugar and compiles to a single opacity effect.
        // The loop self-starts when the first animated asset registers and
        // self-stops when the last one is removed (no idle CPU burn).
        const animAssets = {{}};  // asset_id -> {{effects: [...], layerIds: {{fill, line, circle}}, staticOpacity}}
        let animRafId = null;

        function normalizeEffects(s) {{
            // Style → effects list. `animate` wins; `glow` compiles to an
            // opacity effect. Returns [] when there is nothing to animate.
            const out = [];
            if (Array.isArray(s.animate)) {{
                for (const e of s.animate) {{
                    if (!e || !e.property) continue;
                    out.push({{
                        property: String(e.property),
                        from: Number(e.from !== undefined ? e.from : 0.2),
                        to: Number(e.to !== undefined ? e.to : 1.0),
                        period: Math.max(0.2, Number(e.period) || 2.0),
                        stroke: e.stroke !== false,
                        color: (typeof e.color === 'string') ? e.color : null,
                    }});
                }}
            }} else if (s.glow) {{
                const o = (typeof s.glow === 'object') ? s.glow : {{}};
                out.push({{
                    property: 'opacity',
                    from: Math.min(1, Math.max(0, o.min_opacity !== undefined ? Number(o.min_opacity) : 0.15)),
                    to: Math.min(1, Math.max(0, o.max_opacity !== undefined ? Number(o.max_opacity) : 0.85)),
                    period: Math.max(0.2, Number(o.period) || 2.0),
                    stroke: o.stroke !== false,
                }});
            }}
            return out;
        }}

        function applyStaticOpacity(assetId, opacity) {{
            // Flat opacity on all of an asset's paint layers (style.opacity).
            // null/undefined restores renderer defaults. Fill keeps the
            // hover-brighten expression, scaled by the requested opacity.
            const op = (opacity === null || opacity === undefined) ? null
                : Math.min(1, Math.max(0, Number(opacity)));
            const fillId = 'fill-' + assetId, lineId = 'line-' + assetId, circleId = 'circle-' + assetId;
            try {{
                if (map.getLayer(fillId)) {{
                    map.setPaintProperty(fillId, 'fill-opacity', op === null
                        ? ['case', ['boolean', ['feature-state', 'hover'], false], 0.72, 0.5]
                        : ['case', ['boolean', ['feature-state', 'hover'], false],
                            Math.min(1, 0.72 * op + 0.2 * op), 0.5 * op]);
                }}
                if (map.getLayer(lineId)) map.setPaintProperty(lineId, 'line-opacity', op === null ? 1 : op);
                if (map.getLayer(circleId)) {{
                    map.setPaintProperty(circleId, 'circle-opacity', op === null ? 1 : op);
                    map.setPaintProperty(circleId, 'circle-stroke-opacity', op === null ? 1 : op);
                }}
            }} catch (e) {{}}
        }}

        // ─── Mask engine (spotlight / focus effect, style.mask) ──────────
        // Darkens everything OUTSIDE masked polygon assets. ONE shared fill
        // layer (MASK_LAYER_ID) whose geometry is the whole world (outer
        // ring) with the outer rings of EVERY visible masked asset punched
        // out as holes. So several spotlights all stay clear and the shared
        // exterior is dimmed exactly once (no compounding). Paint comes from
        // the most recently set mask config. The layer is inserted just
        // below the lowest masked asset's own fill so outlines/labels stay
        // on top. Rebuilt on add / update_style / set_visibility / delete.
        //
        // Limitations: polygons crossing the antimeridian mask incorrectly
        // (world-ring approach); interior holes of the AOI stay undarkened.
        const MASK_WORLD_RING = [[-180, -90], [180, -90], [180, 90], [-180, 90], [-180, -90]];
        const MASK_LAYER_ID = '__mask';
        const MASK_SRC_ID = '__mask-src';
        let maskLatestCfg = null;   // paint of the most recent style.mask

        function parseMask(m) {{
            if (!m) return null;
            const o = (typeof m === 'object') ? m : {{}};
            const op = o.opacity !== undefined ? Number(o.opacity) : 0.55;
            return {{
                color: (typeof o.color === 'string' && o.color) ? o.color : '#000000',
                opacity: Math.min(1, Math.max(0, isNaN(op) ? 0.55 : op)),
            }};
        }}

        // Collect every outer ring of every Polygon/MultiPolygon in a GeoJSON.
        function collectOuterRings(geojson, out) {{
            function walk(geom) {{
                if (!geom) return;
                if (geom.type === 'Polygon') {{
                    if (geom.coordinates && geom.coordinates[0]) out.push(geom.coordinates[0]);
                }} else if (geom.type === 'MultiPolygon') {{
                    for (const poly of geom.coordinates || []) if (poly && poly[0]) out.push(poly[0]);
                }} else if (geom.type === 'GeometryCollection') {{
                    (geom.geometries || []).forEach(walk);
                }}
            }}
            if (!geojson) return out;
            if (geojson.type === 'FeatureCollection') (geojson.features || []).forEach(f => walk(f.geometry));
            else if (geojson.type === 'Feature') walk(geojson.geometry);
            else walk(geojson);
            return out;
        }}

        function assetIsVisible(assetId) {{
            const reg = assetRegistry[assetId];
            if (!reg) return false;
            for (const lid of reg.layerIds) {{
                if (map.getLayer(lid)) return (map.getLayoutProperty(lid, 'visibility') || 'visible') !== 'none';
            }}
            return true;
        }}

        // Rebuild the single shared mask layer from every visible masked asset.
        // Call after anything that changes which assets are masked / visible.
        function rebuildMask() {{
            const holes = [];
            let lowestFillId = null;      // insert the mask beneath the lowest masked asset
            const styleLayers = map.getStyle().layers || [];
            const layerIndex = (id) => styleLayers.findIndex(l => l.id === id);
            for (const [assetId, reg] of Object.entries(assetRegistry)) {{
                if (!reg || !reg.mask) continue;
                if (!assetIsVisible(assetId)) continue;
                collectOuterRings(reg.geojson, holes);
                const fid = map.getLayer('fill-' + assetId) ? 'fill-' + assetId
                    : (map.getLayer('line-' + assetId) ? 'line-' + assetId : null);
                if (fid && (lowestFillId === null || layerIndex(fid) < layerIndex(lowestFillId))) lowestFillId = fid;
            }}
            const cfg = maskLatestCfg;
            if (holes.length === 0 || !cfg) {{
                try {{
                    if (map.getLayer(MASK_LAYER_ID)) map.removeLayer(MASK_LAYER_ID);
                    if (map.getSource(MASK_SRC_ID)) map.removeSource(MASK_SRC_ID);
                }} catch (e) {{}}
                return;
            }}
            const maskGeo = {{
                type: 'Feature', properties: {{}},
                geometry: {{ type: 'Polygon', coordinates: [MASK_WORLD_RING].concat(holes) }},
            }};
            const src = map.getSource(MASK_SRC_ID);
            if (src) src.setData(maskGeo);
            else map.addSource(MASK_SRC_ID, {{ type: 'geojson', data: maskGeo }});
            if (!map.getLayer(MASK_LAYER_ID)) {{
                const spec = {{
                    id: MASK_LAYER_ID, type: 'fill', source: MASK_SRC_ID,
                    paint: {{ 'fill-color': cfg.color, 'fill-opacity': cfg.opacity }},
                }};
                try {{ map.addLayer(spec, lowestFillId || undefined); }} catch (e) {{ map.addLayer(spec); }}
            }} else {{
                map.setPaintProperty(MASK_LAYER_ID, 'fill-color', cfg.color);
                map.setPaintProperty(MASK_LAYER_ID, 'fill-opacity', cfg.opacity);
                if (lowestFillId) {{ try {{ map.moveLayer(MASK_LAYER_ID, lowestFillId); }} catch (e) {{}} }}
            }}
        }}

        // Register/unregister an asset's mask config, then rebuild.
        function setAssetMask(assetId, maskCfg) {{
            const reg = assetRegistry[assetId];
            if (!reg) return;
            const cfg = parseMask(maskCfg);
            if (cfg) {{
                reg.mask = cfg;
                maskLatestCfg = cfg;
            }} else {{
                delete reg.mask;
            }}
            rebuildMask();
        }}

        function animTick(nowMs) {{
            const ids = Object.keys(animAssets);
            if (ids.length === 0) {{ animRafId = null; return; }}
            const t = nowMs / 1000;
            for (const aid of ids) {{
                const a = animAssets[aid];
                for (const fx of a.effects) {{
                    const phase = 0.5 + 0.5 * Math.sin((2 * Math.PI * t) / fx.period);
                    const v = fx.from + (fx.to - fx.from) * phase;
                    try {{
                        if (fx.property === 'opacity') {{
                            if (a.layerIds.fill && map.getLayer(a.layerIds.fill)) {{
                                // Compose with hover: hovered feature stays brighter
                                map.setPaintProperty(a.layerIds.fill, 'fill-opacity', ['case',
                                    ['boolean', ['feature-state', 'hover'], false],
                                    Math.min(1, v + 0.2), v]);
                            }}
                            if (fx.stroke && a.layerIds.line && map.getLayer(a.layerIds.line)) {{
                                map.setPaintProperty(a.layerIds.line, 'line-opacity', v);
                            }}
                            if (a.layerIds.circle && map.getLayer(a.layerIds.circle)) {{
                                map.setPaintProperty(a.layerIds.circle, 'circle-opacity', v);
                                map.setPaintProperty(a.layerIds.circle, 'circle-stroke-opacity', v);
                            }}
                        }} else if (fx.property === 'circle_radius') {{
                            if (a.layerIds.circle && map.getLayer(a.layerIds.circle)) {{
                                map.setPaintProperty(a.layerIds.circle, 'circle-radius', v);
                            }}
                        }} else if (fx.property === 'stroke_width') {{
                            if (a.layerIds.line && map.getLayer(a.layerIds.line)) {{
                                map.setPaintProperty(a.layerIds.line, 'line-width', v);
                            }}
                            if (a.layerIds.circle && map.getLayer(a.layerIds.circle)) {{
                                map.setPaintProperty(a.layerIds.circle, 'circle-stroke-width', v);
                            }}
                        }} else if (fx.property === 'ripple') {{
                            // Sonar ping: sawtooth phase — the halo ring
                            // grows outward while fading to transparent,
                            // then snaps back and repeats (dissipating wave).
                            if (a.layerIds.halo && map.getLayer(a.layerIds.halo)) {{
                                const saw = ((t / fx.period) % 1 + 1) % 1;
                                const r = fx.from + (fx.to - fx.from) * saw;
                                const o = 0.55 * (1 - saw);
                                map.setPaintProperty(a.layerIds.halo, 'circle-radius', r);
                                map.setPaintProperty(a.layerIds.halo, 'circle-stroke-opacity', o);
                            }}
                        }}
                    }} catch (e) {{ /* layer mid-removal; next tick recovers */ }}
                }}
            }}
            map.triggerRepaint();
            animRafId = requestAnimationFrame(animTick);
        }}

        function ensureHaloLayer(assetId, rippleFx) {{
            // Auxiliary expanding-ring layer for the ripple effect. Reuses
            // the asset's GeoJSON source; inserted beneath the marker circle
            // so the ping radiates from behind it. Returns the halo layer id
            // or null (no source on this asset).
            const haloId = 'halo-' + assetId;
            if (map.getLayer(haloId)) return haloId;
            const srcId = 'src-' + assetId;
            if (!map.getSource(srcId)) return null;
            const circleId = 'circle-' + assetId;
            let color = rippleFx.color;
            if (!color) {{
                try {{
                    const p = map.getPaintProperty(circleId, 'circle-color');
                    if (typeof p === 'string') color = p;
                }} catch (e) {{}}
            }}
            const spec = {{
                id: haloId, type: 'circle', source: srcId,
                filter: ['==', '$type', 'Point'],
                paint: {{
                    'circle-color': 'rgba(0,0,0,0)',
                    'circle-radius': rippleFx.from,
                    'circle-opacity': 0,
                    'circle-stroke-color': color || '#38bdf8',
                    'circle-stroke-width': 2,
                    'circle-stroke-opacity': 0,
                }},
            }};
            try {{
                if (map.getLayer(circleId)) map.addLayer(spec, circleId);
                else map.addLayer(spec);
            }} catch (e) {{ return null; }}
            return haloId;
        }}

        function removeHaloLayer(assetId) {{
            const haloId = 'halo-' + assetId;
            try {{ if (map.getLayer(haloId)) map.removeLayer(haloId); }} catch (e) {{}}
        }}

        function registerAnim(assetId, style) {{
            const effects = normalizeEffects(style || {{}});
            if (effects.length === 0) return;
            const rippleFx = effects.find(fx => fx.property === 'ripple');
            const haloId = rippleFx ? ensureHaloLayer(assetId, rippleFx) : null;
            if (!rippleFx) removeHaloLayer(assetId);
            animAssets[assetId] = {{
                effects: effects,
                layerIds: {{
                    fill: 'fill-' + assetId,
                    line: 'line-' + assetId,
                    circle: 'circle-' + assetId,
                    halo: haloId,
                }},
                staticOpacity: (style && style.opacity !== undefined) ? style.opacity : null,
            }};
            if (animRafId === null) animRafId = requestAnimationFrame(animTick);
        }}

        function unregisterAnim(assetId, staticOpacity) {{
            if (!animAssets[assetId]) return;
            const a = animAssets[assetId];
            delete animAssets[assetId];
            // Restore static paint values (honoring style.opacity if given)
            const op = (staticOpacity !== undefined) ? staticOpacity : a.staticOpacity;
            applyStaticOpacity(assetId, op);
            try {{
                if (map.getLayer(a.layerIds.circle)) {{
                    map.setPaintProperty(a.layerIds.circle, 'circle-radius', 6);
                }}
            }} catch (e) {{}}
            removeHaloLayer(assetId);
        }}

        // Back-compat aliases (call sites + older embeds)
        function registerGlow(assetId, glowCfg) {{ registerAnim(assetId, {{ glow: glowCfg }}); }}
        function unregisterGlow(assetId) {{ unregisterAnim(assetId); }}

        // ─── Hover highlight (feature-state) ───
        // Polygon fills brighten under the cursor. Uses generateId'd feature
        // ids on the GeoJSON source; the fill layer's fill-opacity is a
        // feature-state expression (see addGeoJSON).
        // Feature-state hover highlighting, ONE dwell-gated hit-test shared by
        // every fill layer. The previous per-layer map.on('mousemove', layerId)
        // wiring made MapLibre run an internal hit-test per asset on EVERY raw
        // mousemove — measured ~2ms per layer per move over run-sized results,
        // which (together with the contract's own per-move query) saturated
        // the main thread whenever the mouse traveled and crawled the draw
        // tools. Highlighting is a dwell affordance: probe when the cursor
        // settles (~90ms), do nothing while it moves or while drawing.
        const hoverFillSources = new Map();  // fill layerId -> srcId
        function wireHoverHighlight(layerId, srcId) {{
            hoverFillSources.set(layerId, srcId);
        }}
        const hoverHL = {{ srcId: null, fid: null }};
        function clearHoverHL() {{
            if (hoverHL.srcId !== null && hoverHL.fid !== null) {{
                try {{
                    map.setFeatureState({{ source: hoverHL.srcId, id: hoverHL.fid }}, {{ hover: false }});
                }} catch (e) {{ /* source may already be gone */ }}
            }}
            hoverHL.srcId = null;
            hoverHL.fid = null;
            map.getCanvas().style.cursor = '';
        }}
        let hoverHLTimer = null;
        map.on('mousemove', (e) => {{
            if (hoverHLTimer) clearTimeout(hoverHLTimer);
            const pt = e.point;
            hoverHLTimer = setTimeout(() => {{
                if (currentDrawMode !== null || deleteMode) {{ clearHoverHL(); return; }}
                const layers = [...hoverFillSources.keys()].filter(id => map.getLayer(id));
                if (!layers.length) return;
                const feats = map.queryRenderedFeatures(pt, {{ layers }});
                if (!feats.length) {{ clearHoverHL(); return; }}
                const f = feats[0];
                const srcId = hoverFillSources.get(f.layer.id);
                if (f.id === undefined || !srcId) return;
                if (hoverHL.srcId === srcId && hoverHL.fid === f.id) return;
                clearHoverHL();
                hoverHL.srcId = srcId;
                hoverHL.fid = f.id;
                map.setFeatureState({{ source: srcId, id: f.id }}, {{ hover: true }});
                map.getCanvas().style.cursor = 'pointer';
            }}, 90);
        }});
        map.on('mouseout', () => {{
            if (hoverHLTimer) clearTimeout(hoverHLTimer);
            clearHoverHL();
        }});

        // ─── Add GeoJSON to map ───
        function addGeoJSON(assetId, geojsonStr, style, visible, name, assetType) {{
            const geojson = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
            const srcId = 'src-' + assetId;
            const vis = visible !== false ? 'visible' : 'none';
            const s = style || {{}};
            // style.mask without an explicit fill_color → transparent interior
            // (the spotlight look: bare map inside, dimmed outside).
            const fillColor = (s.mask && !s.fill_color) ? 'rgba(0,0,0,0)'
                : getStyleProp(s, 'fill_color', DEFAULT_STYLE.fill_color);
            const strokeColor = getStyleProp(s, 'stroke_color', DEFAULT_STYLE.stroke_color);
            const strokeWidth = getStyleProp(s, 'stroke_width', DEFAULT_STYLE.stroke_width);
            const lineDash = (Array.isArray(s.line_dash) && s.line_dash.length > 0) ? s.line_dash : null;
            // Data-driven gradient (style.color_by) overrides flat colors.
            const colorExpr = colorByExpression(s.color_by, geojson);

            // generateId gives features stable numeric ids for feature-state
            // (hover highlight) without requiring ids in the input GeoJSON.
            map.addSource(srcId, {{ type: 'geojson', data: geojson, generateId: true }});

            const layerIds = [];
            const geomTypes = detectGeomTypes(geojson);

            if (geomTypes.has('fill')) {{
                const fillId = 'fill-' + assetId;
                map.addLayer({{
                    id: fillId, type: 'fill', source: srcId,
                    filter: ['any', ['==', '$type', 'Polygon']],
                    paint: {{
                        'fill-color': colorExpr || fillColor,
                        // Feature-state hover: brighten the hovered polygon
                        'fill-opacity': ['case',
                            ['boolean', ['feature-state', 'hover'], false], 0.72, 0.5],
                    }},
                    layout: {{ visibility: vis }},
                }});
                layerIds.push(fillId);
                wireHoverHighlight(fillId, srcId);
            }}
            if (geomTypes.has('line') || geomTypes.has('fill')) {{
                const lineId = 'line-' + assetId;
                const linePaint = {{ 'line-color': colorExpr || strokeColor, 'line-width': strokeWidth }};
                if (lineDash) linePaint['line-dasharray'] = lineDash;
                map.addLayer({{
                    id: lineId, type: 'line', source: srcId,
                    filter: ['any', ['==', '$type', 'Polygon'], ['==', '$type', 'LineString']],
                    paint: linePaint,
                    layout: {{ visibility: vis }},
                }});
                layerIds.push(lineId);
            }}
            if (geomTypes.has('point')) {{
                const circleId = 'circle-' + assetId;
                map.addLayer({{
                    id: circleId, type: 'circle', source: srcId,
                    filter: ['==', '$type', 'Point'],
                    paint: {{
                        'circle-color': colorExpr || fillColor,
                        'circle-radius': 6,
                        'circle-stroke-color': strokeColor,
                        'circle-stroke-width': strokeWidth,
                    }},
                    layout: {{ visibility: vis }},
                }});
                layerIds.push(circleId);
            }}

            // Optional text label (style.label / label_placement / ...)
            const labelId = addLabelLayer(assetId, srcId, s, vis, name, geomTypes);
            if (labelId) layerIds.push(labelId);

            // geojson kept so update_style can rebuild the mask layer later.
            assetRegistry[assetId] = {{ layerIds, bounds: geojsonBounds(geojson), srcId, name: name || null, asset_type: assetType || 'vector', geomTypes: Array.from(geomTypes), geojson }};

            // Optional mask (style.mask): darken everything outside the polygon
            if (s.mask && geomTypes.has('fill')) setAssetMask(assetId, s.mask);
            // Static opacity (style.opacity), then optional animation
            // (style.animate / style.glow) — the animation overrides the
            // static value while running.
            if (s.opacity !== undefined && s.opacity !== null) applyStaticOpacity(assetId, s.opacity);
            if ((Array.isArray(s.animate) && s.animate.length > 0) || s.glow) registerAnim(assetId, s);
        }}

        // ─── Add Image Overlay (GeoTIFF) ───
        // Find the first vector layer ID (fill/line/circle) so rasters insert below it
        function getFirstVectorLayerId() {{
            for (const [id, reg] of Object.entries(assetRegistry)) {{
                for (const lid of reg.layerIds) {{
                    const layer = map.getLayer(lid);
                    if (layer && (layer.type === 'fill' || layer.type === 'line' || layer.type === 'circle')) {{
                        return lid;
                    }}
                }}
            }}
            return undefined;  // no vector layers yet
        }}

        async function addImageOverlay(assetId, imageUrl, bounds, opacity, visible, name, assetType) {{
            // bounds = [minLon, minLat, maxLon, maxLat]
            const fullUrl = imageUrl.startsWith('http') ? imageUrl : BASE_URL + imageUrl;
            const srcId = 'src-' + assetId;
            const layerId = 'img-' + assetId;
            const coords = [
                [bounds[0], bounds[3]],  // top-left
                [bounds[2], bounds[3]],  // top-right
                [bounds[2], bounds[1]],  // bottom-right
                [bounds[0], bounds[1]],  // bottom-left
            ];
            // Fetch image as blob URL to prevent canvas tainting
            // (HTTP URLs can taint the WebGL canvas, making toDataURL return blank)
            let imgSrc = fullUrl;
            try {{
                const resp = await fetch(fullUrl);
                const blob = await resp.blob();
                imgSrc = URL.createObjectURL(blob);
                console.log('[GeoTIFF] Loaded as blob URL:', imgSrc, '(' + Math.round(blob.size/1024) + ' KB)');
            }} catch (e) {{
                console.warn('[GeoTIFF] Blob fetch failed, using direct URL:', e.message);
            }}
            map.addSource(srcId, {{ type: 'image', url: imgSrc, coordinates: coords }});
            // Insert raster below any existing vector layers (vectors always on top)
            const beforeId = getFirstVectorLayerId();
            map.addLayer({{
                id: layerId, type: 'raster', source: srcId,
                paint: {{ 'raster-opacity': opacity || 1.0 }},
                layout: {{ visibility: visible !== false ? 'visible' : 'none' }},
            }}, beforeId);
            const b = new maplibregl.LngLatBounds([bounds[0], bounds[1]], [bounds[2], bounds[3]]);
            assetRegistry[assetId] = {{ layerIds: [layerId], bounds: b, srcId, name: name || null, asset_type: assetType || 'geotiff' }};
        }}

        // ─── deck.gl overlay: true 3D arcs (sketch 003 → deck ArcLayer) ───
        // Interleaved MapboxOverlay renders into MapLibre's own WebGL
        // context, so arcs depth-sort against terrain AND canvas-based
        // screenshots still capture them. If the deck bundle failed to load
        // (offline / CSP), arc assets gracefully fall back to their stored
        // flat Bézier LineString via the normal vector path.
        const deckArcs = {{}};  // asset_id -> {{from, to, height, color, width, visible}}
        let deckOverlay = null;
        try {{
            if (window.deck && deck.MapboxOverlay) {{
                deckOverlay = new deck.MapboxOverlay({{ interleaved: true, layers: [] }});
                map.addControl(deckOverlay);
                console.log('deck.gl overlay attached (interleaved)');
            }}
        }} catch (e) {{
            console.warn('deck.gl overlay unavailable — arcs will render flat:', e);
            deckOverlay = null;
        }}

        function hexToRgba(hex) {{
            if (!hex || typeof hex !== 'string') return [15, 52, 96, 255];
            let h = hex.replace('#', '');
            if (h.length === 3) h = h.split('').map(c => c + c).join('');
            const r = parseInt(h.slice(0, 2), 16);
            const g = parseInt(h.slice(2, 4), 16);
            const b = parseInt(h.slice(4, 6), 16);
            const a = h.length >= 8 ? parseInt(h.slice(6, 8), 16) : 255;
            return [isNaN(r) ? 15 : r, isNaN(g) ? 52 : g, isNaN(b) ? 96 : b, isNaN(a) ? 255 : a];
        }}

        function renderDeckArcs() {{
            if (!deckOverlay) return;
            const visibleArcs = Object.values(deckArcs).filter(a => a.visible !== false && !a.suppressed);
            deckOverlay.setProps({{
                layers: [
                    new deck.ArcLayer({{
                        id: 'esip-arcs',
                        data: visibleArcs,
                        getSourcePosition: d => d.from,
                        getTargetPosition: d => d.to,
                        getSourceColor: d => d.color,
                        getTargetColor: d => d.color,
                        getWidth: d => d.width,
                        getHeight: d => d.height,
                        widthUnits: 'pixels',
                        pickable: false,
                    }}),
                ],
            }});
        }}

        // Add an arc asset: deck ArcLayer for the 3D ribbon + MapLibre
        // circle/label layers for the endpoint dots. `from`/`to` come from
        // the live add_arc broadcast; on session restore they're recovered
        // from the stored FeatureCollection's Point features.
        function addArc(assetId, geojsonStr, style, visible, name, from, to, curvature) {{
            const gj = typeof geojsonStr === 'string' ? JSON.parse(geojsonStr) : geojsonStr;
            if (!from || !to) {{
                const pts = (gj.features || []).filter(f => f.geometry && f.geometry.type === 'Point');
                if (pts.length >= 2) {{
                    from = pts[0].geometry.coordinates;
                    to = pts[1].geometry.coordinates;
                }}
            }}
            // Always render the FULL FeatureCollection (flat Bézier line +
            // endpoint dots + labels) through the normal MapLibre vector
            // path — this draws correctly under EVERY projection, including
            // globe, where deck's interleaved ArcLayer renders nothing
            // (verified headless: 0 ribbon pixels in globe, fine in
            // mercator). The deck 3D ribbon is layered on top only in
            // mercator mode; syncArcMode() then hides the flat line so the
            // two never double-draw.
            addGeoJSON(assetId, gj, style, visible, name, 'arc');
            if (!deckOverlay || !from || !to) return;
            if (assetRegistry[assetId]) {{
                assetRegistry[assetId].isDeckArc = true;
            }}
            const s = style || {{}};
            const c = (typeof curvature === 'number' && isFinite(curvature)) ? Math.abs(curvature) : 0.25;
            deckArcs[assetId] = {{
                from: from,
                to: to,
                color: hexToRgba(getStyleProp(s, 'stroke_color', DEFAULT_STYLE.stroke_color)),
                width: getStyleProp(s, 'stroke_width', DEFAULT_STYLE.stroke_width) || 3,
                height: Math.max(0.2, c * 2),  // curvature 0.25 → height 0.5
                visible: visible !== false,
            }};
            syncArcMode();
        }}

        // Globe/mercator arc arbitration. deck's interleaved ArcLayer does
        // not render under MapLibre's globe projection, so in globe ('3d')
        // mode the deck ribbon is suppressed and the flat MapLibre line
        // shows; in mercator the deck ribbon renders and the flat line is
        // hidden. Called on arc add, visibility change, terrain switch,
        // and session restore.
        function syncArcMode() {{
            const globe = (currentTerrain === '3d');
            for (const [id, arc] of Object.entries(deckArcs)) {{
                arc.suppressed = globe;
                const lineId = 'line-' + id;
                if (map.getLayer(lineId)) {{
                    const showFlat = globe && arc.visible !== false;
                    map.setLayoutProperty(lineId, 'visibility', showFlat ? 'visible' : 'none');
                }}
            }}
            renderDeckArcs();
        }}

        // ─── Pending draws (optimistic rendering with an honest style) ───
        // A finished drawing renders IMMEDIATELY in a dashed "pending" style
        // and is swapped for the solid server-confirmed asset when the
        // add_polygon echo (carrying our client_id) round-trips. The dash is
        // deliberate signal, not decoration: the server must own the shape
        // before EOGPT can operate on it, so a shape that STAYS dashed tells
        // a user on a bad connection that it has not landed yet — instead of
        // silently pretending it did (or, worse, the old behavior: removing
        // it and showing nothing until the round trip completed).
        const pendingDraws = {{}};  // client_id -> {{ srcId, layerIds }}

        function addPendingDraw(clientId, geojson) {{
            const srcId = 'pending-src-' + clientId;
            const fillId = 'pending-fill-' + clientId;
            const lineId = 'pending-line-' + clientId;
            try {{
                map.addSource(srcId, {{ type: 'geojson', data: geojson }});
                map.addLayer({{
                    id: fillId, type: 'fill', source: srcId,
                    filter: ['==', '$type', 'Polygon'],
                    paint: {{ 'fill-color': '#7cc242', 'fill-opacity': 0.08 }},
                }});
                map.addLayer({{
                    id: lineId, type: 'line', source: srcId,
                    paint: {{
                        'line-color': '#5fa830', 'line-width': 2,
                        'line-dasharray': [2, 2], 'line-opacity': 0.9,
                    }},
                }});
                pendingDraws[clientId] = {{ srcId, layerIds: [fillId, lineId] }};
            }} catch (e) {{
                console.warn('Could not render pending draw:', e);
            }}
        }}

        function removePendingDraw(clientId) {{
            if (!clientId) return;
            const pending = pendingDraws[clientId];
            if (!pending) return;
            for (const lid of pending.layerIds) {{
                if (map.getLayer(lid)) map.removeLayer(lid);
            }}
            if (map.getSource(pending.srcId)) map.removeSource(pending.srcId);
            delete pendingDraws[clientId];
        }}

        // ─── Event Handlers ───
        const handlers = {{
            // ─── Navigation: uses MapLibre native flyTo (van Wijk algorithm) ───
            zoom_to_point(data) {{
                map.flyTo({{ center: [data.lon, data.lat], zoom: data.zoom || 14 }});
            }},
            zoom_to_bbox(data) {{
                // data.bbox = [minLon, minLat, maxLon, maxLat]
                map.fitBounds(
                    [[data.bbox[0], data.bbox[1]], [data.bbox[2], data.bbox[3]]],
                    {{ padding: 50, maxZoom: 18 }}
                );
            }},
            zoom_to_assets(data) {{
                const combined = new maplibregl.LngLatBounds();
                let hasData = false;
                for (const id of (data.asset_ids || [])) {{
                    const reg = assetRegistry[id];
                    if (reg && reg.bounds && !reg.bounds.isEmpty()) {{
                        combined.extend(reg.bounds);
                        hasData = true;
                    }}
                }}
                if (hasData) {{
                    map.fitBounds(combined, {{ padding: 50, maxZoom: 18 }});
                }}
            }},
            zoom_out(data) {{
                const levels = data.levels || 3;
                map.flyTo({{ zoom: Math.max(0, map.getZoom() - levels), duration: 1200 }});
            }},

            // ─── Asset creation ───
            add_polygon(data) {{
                // Drawn-shape echo: drop our pending-styled copy first (no-op
                // for other sessions / agent-created polygons).
                removePendingDraw(data.client_id);
                addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'polygon');
            }},
            add_polygon_url(data) {{ addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'polygon'); }},
            add_path(data) {{ addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'path'); }},
            add_path_url(data) {{ addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'path'); }},
            add_point(data) {{ addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'point'); }},
            // Arc: 3D deck.gl ArcLayer (+ MapLibre endpoint dots/labels);
            // falls back to the stored flat LineString if deck is unavailable.
            add_arc(data) {{ addArc(data.asset_id, data.geojson, data.style, true, data.name, data.from, data.to, data.curvature); }},

            // ─── Asset management ───
            delete_asset(data) {{
                const reg = assetRegistry[data.asset_id];
                if (reg) {{
                    for (const lid of reg.layerIds) {{
                        if (map.getLayer(lid)) map.removeLayer(lid);
                    }}
                    if (map.getSource(reg.srcId)) map.removeSource(reg.srcId);
                    const hadMask = !!reg.mask;
                    delete assetRegistry[data.asset_id];
                    if (hadMask) rebuildMask();
                }}
                if (deckArcs[data.asset_id]) {{
                    delete deckArcs[data.asset_id];
                    renderDeckArcs();
                }}
                unregisterGlow(data.asset_id);
            }},
            set_visibility(data) {{
                const reg = assetRegistry[data.asset_id];
                if (reg) {{
                    const vis = data.visible !== false ? 'visible' : 'none';
                    for (const lid of reg.layerIds) {{
                        map.setLayoutProperty(lid, 'visibility', vis);
                    }}
                    // Hidden masked assets drop out of the shared mask.
                    if (reg.mask) rebuildMask();
                }}
                if (deckArcs[data.asset_id]) {{
                    deckArcs[data.asset_id].visible = data.visible !== false;
                    syncArcMode();
                }}
            }},
            update_style(data) {{
                const reg = assetRegistry[data.asset_id];
                if (reg && data.style) {{
                    const s = data.style;
                    for (const lid of reg.layerIds) {{
                        const layer = map.getLayer(lid);
                        if (!layer) continue;
                        if (layer.type === 'fill') {{
                            if (s.fill_color) map.setPaintProperty(lid, 'fill-color', s.fill_color);
                        }} else if (layer.type === 'line') {{
                            if (s.stroke_color) map.setPaintProperty(lid, 'line-color', s.stroke_color);
                            if (s.stroke_width) map.setPaintProperty(lid, 'line-width', s.stroke_width);
                            // line_dash: array of dash lengths (in line-widths). Empty/null = solid.
                            if (s.line_dash !== undefined) {{
                                if (Array.isArray(s.line_dash) && s.line_dash.length > 0) {{
                                    map.setPaintProperty(lid, 'line-dasharray', s.line_dash);
                                }} else {{
                                    map.setPaintProperty(lid, 'line-dasharray', [1]);
                                }}
                            }}
                        }} else if (layer.type === 'circle') {{
                            if (s.fill_color) map.setPaintProperty(lid, 'circle-color', s.fill_color);
                            if (s.stroke_color) map.setPaintProperty(lid, 'circle-stroke-color', s.stroke_color);
                            if (s.stroke_width) map.setPaintProperty(lid, 'circle-stroke-width', s.stroke_width);
                        }} else if (layer.type === 'symbol') {{
                            if (s.label_color) map.setPaintProperty(lid, 'text-color', s.label_color);
                            if (s.label_size) map.setLayoutProperty(lid, 'text-size', s.label_size);
                        }}
                    }}
                    // Label add/remove/re-place: rebuild the symbol layer when
                    // label or label_placement is present in the update.
                    if (s.label !== undefined || s.label_placement !== undefined) {{
                        const labelId = 'label-' + data.asset_id;
                        if (map.getLayer(labelId)) {{
                            map.removeLayer(labelId);
                            const i = reg.layerIds.indexOf(labelId);
                            if (i !== -1) reg.layerIds.splice(i, 1);
                        }}
                        if (s.label) {{
                            const geomTypes = new Set(reg.geomTypes || []);
                            const newId = addLabelLayer(data.asset_id, reg.srcId, s, 'visible', reg.name, geomTypes);
                            if (newId) reg.layerIds.push(newId);
                        }}
                    }}
                    // Static opacity via update_style (applied first; a
                    // running animation re-drives opacity on its next tick)
                    if (s.opacity !== undefined) {{
                        applyStaticOpacity(data.asset_id, s.opacity);
                        if (animAssets[data.asset_id]) {{
                            animAssets[data.asset_id].staticOpacity = s.opacity;
                        }}
                    }}
                    // Animation start/stop/re-tune via update_style.
                    // animate: []  → stop; animate: [...] → replace effects;
                    // glow keeps working as the back-compat alias.
                    if (s.animate !== undefined || s.glow !== undefined) {{
                        unregisterAnim(data.asset_id, s.opacity);
                        registerAnim(data.asset_id, s);
                    }}
                    // Mask add/remove/re-tune via update_style. The fill layer
                    // is left untouched unless mask is being turned ON with
                    // no fill color — then the interior is cleared to the
                    // bare map.
                    if (s.mask !== undefined) {{
                        if (s.mask && !reg.geojson) {{
                            const src = map.getSource(reg.srcId);
                            if (src && src._data) reg.geojson = (typeof src._data === 'string') ? JSON.parse(src._data) : src._data;
                        }}
                        setAssetMask(data.asset_id, s.mask);
                        const fid = 'fill-' + data.asset_id;
                        if (s.mask && !s.fill_color && map.getLayer(fid)) {{
                            map.setPaintProperty(fid, 'fill-color', 'rgba(0,0,0,0)');
                        }}
                    }}
                }}
            }},

            // ─── Basemap ───
            // Three switch cases:
            //   raster → raster   : cheap visibility flip on existing layers
            //   raster → vector   : map.setStyle(vector style.json), then restore
            //                       user assets in the style.load callback
            //   vector → anything : rebuild target style + restore user assets
            set_basemap(data) {{
                const name = data.basemap || basemapIds[0];
                const entry = BASEMAPS[name];
                if (!entry) {{
                    console.warn('Unknown basemap:', name, '— available:', basemapIds);
                    return;
                }}
                const nextKind = entry.kind || 'raster';

                const showVectorGroup = (visible) => {{
                    for (const id of vectorLayerIds) {{
                        if (map.getLayer(id)) {{
                            map.setLayoutProperty(id, 'visibility', visible ? 'visible' : 'none');
                        }}
                    }}
                }};
                const showRasterLayer = (visibleId) => {{
                    for (const id of basemapIds) {{
                        const e = BASEMAPS[id];
                        if ((e.kind || 'raster') !== 'raster') continue;
                        if (map.getLayer(id)) {{
                            map.setLayoutProperty(id, 'visibility', id === visibleId ? 'visible' : 'none');
                        }}
                    }}
                }};

                // Toggle-only switching within the permanent merged style:
                // any raster target, or returning to the vector basemap the
                // style was loaded with. Nothing is destroyed — Terra Draw,
                // assets, and view state are untouched.
                if (nextKind === 'raster') {{
                    showRasterLayer(name);
                    showVectorGroup(false);
                    currentBasemap = name;
                    currentBasemapKind = 'raster';
                    updateBasemapPickerActive();
                    sendViewportUpdate();
                    return;
                }}
                if (name === loadedVectorBasemap) {{
                    showRasterLayer(null);
                    showVectorGroup(true);
                    currentBasemap = name;
                    currentBasemapKind = 'vector';
                    updateBasemapPickerActive();
                    sendViewportUpdate();
                    return;
                }}

                // Legacy heavy path — ONLY for a vector style that is not the
                // loaded one: setStyle wipes the world, so save/restore user
                // assets. The new vector style then becomes the permanent one
                // and the raster basemaps are re-injected into it.
                const userState = captureMapState();
                // Terra Draw: park the mode, stop (removes its td-* layers
                // cleanly), and start again after the new style loads — its
                // feature store lives in JS, so this round-trips safely. Any
                // in-progress (unfinished) sketch is intentionally dropped.
                if (drawInstance) {{
                    try {{ drawInstance.setMode('static'); drawInstance.stop(); }} catch (e) {{}}
                    currentDrawMode = null;
                    updateDrawToolbar();
                }}
                // Register the restore handler BEFORE setStyle: inline object
                // styles can fire 'style.load' SYNCHRONOUSLY inside setStyle,
                // so a handler registered on the next line misses it and the
                // restore silently never runs (observed in Firefox).
                map.once('style.load', () => {{
                    currentBasemap = name;
                    currentBasemapKind = 'vector';
                    loadedVectorBasemap = name;
                    vectorLayerIds = [];
                    injectRasterBasemaps();   // before restore: user layers must not be misclassified
                    restoreMapState(userState);
                    if (drawInstance) {{
                        try {{ drawInstance.start(); }} catch (e) {{
                            console.warn('Terra Draw restart after style switch failed:', e.message);
                        }}
                    }}
                    updateBasemapPickerActive();
                    sendViewportUpdate();
                }});
                map.setStyle(entry.url);
            }},

            // ─── Terrain (2D/3D toggle) ───
            set_terrain(data) {{
                const mode = (data.mode || '2d').toLowerCase();
                applyTerrainMode(mode, true);
            }},

            // ─── Theme (light/dark/auto — live flip, no reload) ───
            set_theme(data) {{
                applyTheme((data.theme || 'auto').toLowerCase());
                console.log('Theme set:', themeMode, '→ resolved:', resolvedTheme());
            }},

            // ─── Pitch / Bearing control ───
            set_pitch(data) {{
                const opts = {{ duration: data.duration || 1000 }};
                if (data.pitch !== undefined) opts.pitch = data.pitch;
                if (data.bearing !== undefined) opts.bearing = data.bearing;
                map.easeTo(opts);
            }},

            // ─── Demo label (forwarded to parent frame for demo pages) ───
            set_demo_label(data) {{
                try {{ window.parent.postMessage({{ type: 'demo_label', text: data.text || '' }}, '*'); }} catch(e) {{}}
            }},

            // ─── GeoTIFF Handlers ───
            add_geotiff_rgb(data) {{
                addImageOverlay(data.asset_id, data.image_url, data.bounds, data.alpha || 1.0, true, data.name, 'geotiff_rgb');
            }},
            add_geotiff_singleband(data) {{
                addImageOverlay(data.asset_id, data.image_url, data.bounds, data.alpha || 1.0, true, data.name, 'geotiff_singleband');
            }},

            // ─── Drawing Control (from SDK/API) ───
            enable_drawing(data) {{
                const mode = (data.mode || 'polygon').toLowerCase();
                // Normalize SDK aliases onto our draw types (Terra Draw
                // supports all four natively).
                const normalized = {{
                    box: 'box', rectangle: 'box',
                    polygon: 'polygon', circle: 'circle',
                    line: 'line', linestring: 'line',
                }}[mode] || 'polygon';
                setDrawMode(normalized);
            }},
            disable_drawing(data) {{
                cancelDraw();
            }},

            // ─── Drawn asset from server (add_drawn_polygon) ───
            add_drawn_polygon(data) {{
                removePendingDraw(data.client_id);
                addGeoJSON(data.asset_id, data.geojson, data.style, true, data.name, 'drawn_polygon');
            }},

            // ─── Draw confirmation (push to undo stack) ───
            draw_complete(data) {{
                // Belt-and-braces: the add_polygon broadcast normally clears
                // the pending copy, but draw_complete also carries client_id.
                removePendingDraw(data.client_id);
                if (data.asset_id) {{
                    drawnAssetStack.push(data.asset_id);
                    updateUndoButton();
                    console.log('Draw confirmed, undo stack size:', drawnAssetStack.length);
                }}
            }},

            // ─── Tile Layers (XYZ, WMS, etc.) ───
            add_tile_layer(data) {{
                const assetId = data.asset_id;
                const srcId = 'src-' + assetId;
                const layerId = 'tile-' + assetId;
                const url = data.url;
                // Normalize opacity: if > 1, treat as percentage (e.g. 40 → 0.4)
                let opacity = data.opacity !== undefined ? data.opacity : 1.0;
                if (opacity > 1) opacity = opacity / 100;
                opacity = Math.max(0, Math.min(1, opacity));
                const vis = data.visible !== false ? 'visible' : 'none';

                map.addSource(srcId, {{
                    type: 'raster',
                    tiles: [url],
                    tileSize: 256,
                    attribution: data.attribution || '',
                }});

                // Insert tile layers below vector layers (same as GeoTIFFs)
                const beforeId = getFirstVectorLayerId();
                map.addLayer({{
                    id: layerId,
                    type: 'raster',
                    source: srcId,
                    paint: {{ 'raster-opacity': opacity }},
                    layout: {{ visibility: vis }},
                }}, beforeId);

                assetRegistry[assetId] = {{ layerIds: [layerId], bounds: null, srcId, tileUrl: url, name: data.name || null, asset_type: 'tile' }};
                console.log('Added tile layer:', assetId, url, 'opacity:', opacity);
            }},

            remove_tile_layer(data) {{
                handlers.delete_asset(data);
            }},

            // ─── Opacity Control ───
            set_opacity(data) {{
                const reg = assetRegistry[data.asset_id];
                if (!reg) return;
                // Normalize: if > 1, treat as percentage (e.g. 40 → 0.4)
                let rawOpacity = data.opacity;
                if (rawOpacity > 1) rawOpacity = rawOpacity / 100;
                const opacity = Math.max(0, Math.min(1, rawOpacity));
                for (const lid of reg.layerIds) {{
                    const layer = map.getLayer(lid);
                    if (!layer) continue;
                    if (layer.type === 'raster') {{
                        map.setPaintProperty(lid, 'raster-opacity', opacity);
                    }} else if (layer.type === 'fill') {{
                        map.setPaintProperty(lid, 'fill-opacity', opacity);
                    }} else if (layer.type === 'line') {{
                        map.setPaintProperty(lid, 'line-opacity', opacity);
                    }} else if (layer.type === 'circle') {{
                        map.setPaintProperty(lid, 'circle-opacity', opacity);
                        map.setPaintProperty(lid, 'circle-stroke-opacity', opacity);
                    }}
                }}
                console.log('Set opacity:', data.asset_id, opacity);
            }},

            // ─── Layer Ordering ───
            move_layer(data) {{
                const reg = assetRegistry[data.asset_id];
                if (!reg) return;
                const position = data.position; // 'up', 'down', 'top', 'bottom'
                const allLayers = map.getStyle().layers;
                const ourLayers = reg.layerIds.filter(lid => map.getLayer(lid));
                if (ourLayers.length === 0) return;

                if (position === 'top') {{
                    // Move to top of all layers
                    for (const lid of ourLayers) {{
                        map.moveLayer(lid);
                    }}
                }} else if (position === 'bottom') {{
                    // Move just above basemap layers
                    const firstNonBasemap = allLayers.find(l => !isBasemapLayer(l.id));
                    if (firstNonBasemap) {{
                        for (const lid of ourLayers) {{
                            map.moveLayer(lid, firstNonBasemap.id);
                        }}
                    }}
                }} else if (position === 'up') {{
                    // Move above the next layer
                    const topLayer = ourLayers[ourLayers.length - 1];
                    const idx = allLayers.findIndex(l => l.id === topLayer);
                    if (idx >= 0 && idx < allLayers.length - 1) {{
                        // Find the next non-our layer above
                        for (let i = idx + 1; i < allLayers.length; i++) {{
                            if (!ourLayers.includes(allLayers[i].id)) {{
                                // Move our layers above this one
                                map.moveLayer(topLayer);
                                break;
                            }}
                        }}
                    }}
                }} else if (position === 'down') {{
                    // Move below the previous layer, but never below the basemap stack
                    const bottomLayer = ourLayers[0];
                    const idx = allLayers.findIndex(l => l.id === bottomLayer);
                    if (idx > basemapIds.length) {{
                        for (let i = idx - 1; i >= 0; i--) {{
                            if (!ourLayers.includes(allLayers[i].id) && !isBasemapLayer(allLayers[i].id)) {{
                                map.moveLayer(bottomLayer, allLayers[i].id);
                                break;
                            }}
                        }}
                    }}
                }}
                // Keep the shared mask beneath the lowest masked asset.
                if (map.getLayer(MASK_LAYER_ID)) rebuildMask();
                console.log('Moved layer:', data.asset_id, position);
            }},

            // ─── Full re-stack (drag-to-reorder in layer managers) ───
            // data.asset_ids: complete stacking order, TOP-most first.
            // Walk bottom→top moving each asset's layers to the top of the
            // style, so the last-moved (list-top) asset renders on top.
            // Relative order of non-listed layers (basemap etc.) is kept
            // because we only move user asset layers.
            reorder_assets(data) {{
                const ids = (data.asset_ids || []).slice().reverse();  // bottom-most first
                for (const id of ids) {{
                    const reg = assetRegistry[id];
                    if (!reg) continue;
                    for (const lid of reg.layerIds) {{
                        if (map.getLayer(lid)) {{
                            try {{ map.moveLayer(lid); }} catch (e) {{}}
                        }}
                    }}
                }}
                // Keep the shared mask beneath the lowest masked asset.
                if (map.getLayer(MASK_LAYER_ID)) rebuildMask();
                console.log('Reordered assets (top first):', data.asset_ids);
            }},
        }};

        // ─── Save/restore around map.setStyle() (vector basemap support) ───
        // setStyle() wipes every source and layer, including user-drawn
        // polygons, GeoTIFF overlays, tile overlays, terrain, and sky.
        // captureMapState() snapshots the user-owned pieces (looked up via
        // assetRegistry, the canonical record of user assets) plus terrain
        // mode and viewport; restoreMapState() re-adds them inside the
        // new style's 'style.load' callback.
        function captureMapState() {{
            const style = map.getStyle();
            const userSourceIds = new Set();
            for (const reg of Object.values(assetRegistry)) {{
                if (reg && reg.srcId) userSourceIds.add(reg.srcId);
            }}
            // Unconfirmed drawn shapes survive a basemap switch too.
            for (const pending of Object.values(pendingDraws)) {{
                if (pending && pending.srcId) userSourceIds.add(pending.srcId);
            }}
            // (Terra Draw's td-* sources/layers are deliberately NOT captured:
            // its state lives in its own JS store and the legacy rebuild path
            // stop()s it before setStyle and start()s it after, which re-adds
            // its layers cleanly — unlike the retired Geoman, whose internal
            // bookkeeping could not survive a style rebuild.)
            if (map.getSource(MASK_SRC_ID)) userSourceIds.add(MASK_SRC_ID);
            const userSources = {{}};
            const userLayers = [];
            for (const [id, src] of Object.entries(style.sources || {{}})) {{
                if (userSourceIds.has(id)) userSources[id] = src;
            }}
            for (const layer of style.layers || []) {{
                if (layer.source && userSourceIds.has(layer.source)) {{
                    userLayers.push(layer);
                }}
            }}
            return {{
                sources: userSources,
                layers: userLayers,
                terrain: currentTerrain,
                view: {{
                    center: map.getCenter().toArray(),
                    zoom: map.getZoom(),
                    pitch: map.getPitch(),
                    bearing: map.getBearing(),
                }},
            }};
        }}

        function restoreMapState(snap) {{
            if (!snap) return;
            for (const [id, src] of Object.entries(snap.sources)) {{
                if (!map.getSource(id)) {{
                    try {{ map.addSource(id, src); }} catch(e) {{
                        console.warn('Failed to re-add source', id, e);
                    }}
                }}
            }}
            for (const layer of snap.layers) {{
                if (!map.getLayer(layer.id)) {{
                    try {{ map.addLayer(layer); }} catch(e) {{
                        console.warn('Failed to re-add layer', layer.id, e);
                    }}
                }}
            }}
            // 3d mode is globe projection + sky ONLY — MapLibre's raster-DEM
            // terrain is retired here (globe+terrain is the half-supported
            // combination behind the shader-cache crash class; real 3D is the
            // Babylon/3D-Tiles view's job).
            if (snap.terrain && snap.terrain !== '2d') {{
                try {{ map.setProjection({{ type: 'globe' }}); }} catch(e) {{}}
                enableSky();
            }}
            try {{
                map.jumpTo({{
                    center: snap.view.center,
                    zoom: snap.view.zoom,
                    pitch: snap.view.pitch,
                    bearing: snap.view.bearing,
                }});
            }} catch(e) {{}}
        }}

        // ─── Session Restore Handler ───
        function handleSnapshot(snapshot) {{
            // Clear all existing assets
            for (const [id, reg] of Object.entries(assetRegistry)) {{
                for (const lid of reg.layerIds) {{
                    if (map.getLayer(lid)) map.removeLayer(lid);
                }}
                if (map.getSource(reg.srcId)) map.removeSource(reg.srcId);
                delete assetRegistry[id];
            }}
            rebuildMask();   // registry is empty → drops the shared mask layer
            for (const id of Object.keys(deckArcs)) delete deckArcs[id];
            renderDeckArcs();

            // Restore all assets
            for (const asset of (snapshot.assets || [])) {{
                if (asset.asset_type && asset.asset_type.startsWith('geotiff_')) {{
                    const imageUrl = '/api/files/' + asset.asset_id + '.png';
                    const geojson = typeof asset.geojson === 'string' ? JSON.parse(asset.geojson) : asset.geojson;
                    const coords = geojson.geometry ? geojson.geometry.coordinates[0] : geojson.coordinates[0];
                    const lons = coords.map(c => c[0]);
                    const lats = coords.map(c => c[1]);
                    const bounds = [Math.min(...lons), Math.min(...lats), Math.max(...lons), Math.max(...lats)];
                    addImageOverlay(asset.asset_id, imageUrl, bounds, 1.0, asset.visible, asset.name, asset.asset_type);
                }} else if (asset.asset_type === 'arc') {{
                    // 3D deck arc: endpoints recovered from the stored
                    // FeatureCollection's Point features inside addArc.
                    addArc(asset.asset_id, asset.geojson, asset.style, asset.visible, asset.name);
                }} else {{
                    // Pass name + type through — losing them on restore left
                    // hover cards and layer managers with unnamed "vector" rows.
                    addGeoJSON(asset.asset_id, asset.geojson, asset.style, asset.visible, asset.name, asset.asset_type);
                }}
            }}
            // Restore basemap — ONLY when it actually differs, and always
            // through the kind-aware set_basemap handler. Snapshots arrive on
            // every WS (re)connect, so this path must be a no-op when nothing
            // changed: the old inline raster-toggle ran unconditionally and
            // assumed raster basemap LAYERS exist — false whenever the live
            // style is a vector basemap (setLayoutProperty then fires
            // "Cannot style non-existing layer" and currentBasemap went out
            // of sync with what is actually displayed). Routing through the
            // handler also makes vector-basemap restores work (heavy
            // setStyle path with user-asset preservation + Terra Draw restart).
            // A ?basemap= URL param is an explicit embedder choice (EOGPT pins
            // the theme-matched hybrid); a stale session-stored basemap must
            // not override it on (re)connect. Without the param, the stored
            // basemap is the truth to restore.
            const urlPinnedBasemap = new URLSearchParams(window.location.search).has('basemap');
            if (snapshot.basemap && BASEMAPS[snapshot.basemap]
                && snapshot.basemap !== currentBasemap
                && !urlPinnedBasemap) {{
                handlers.set_basemap({{ basemap: snapshot.basemap }});
            }}
            // Restore theme (map-level; light/dark/auto). CSS-only, cheap.
            if (snapshot.theme) {{
                applyTheme(snapshot.theme);
            }}
            // Restore view mode BEFORE viewport — setProjection('globe') can
            // disrupt center/zoom, so projection first, then viewport on the
            // next animation frame. Change-only for the same reason as
            // basemap: snapshots arrive on every reconnect and redundant
            // projection churn must not happen.
            const snapTerrain = snapshot.terrain;
            if (snapTerrain && snapTerrain !== currentTerrain) {{
                // 3d = globe projection + sky only (no raster-DEM terrain —
                // see restoreMapState for why it was retired).
                if (snapTerrain !== '2d') {{
                    currentTerrain = snapTerrain;
                    try {{ map.setProjection({{ type: 'globe' }}); }} catch(e) {{}}
                    enableSky();
                }} else {{
                    currentTerrain = '2d';
                    try {{ map.setProjection({{ type: 'mercator' }}); }} catch(e) {{}}
                    disableSky();
                }}
            }}
            // Projection may have changed — re-arbitrate deck-ribbon vs
            // flat-line for any restored arcs.
            syncArcMode();
            // Restore viewport AFTER projection so a globe flip doesn't
            // clobber the center/zoom — and ONLY when it actually differs
            // from where the camera already is. Snapshots arrive on every
            // (re)connect; unconditionally re-applying the stored view made
            // fresh pages visibly "reset" moments after first paint.
            if (snapshot.viewport) {{
                const v = snapshot.viewport;
                let alreadyThere = false;
                if (v.center) {{
                    const c = map.getCenter();
                    alreadyThere =
                        Math.abs(c.lng - v.center[0]) < 1e-3 &&
                        Math.abs(c.lat - v.center[1]) < 1e-3 &&
                        Math.abs(map.getZoom() - (v.zoom || DEFAULTS.zoom)) < 0.05;
                }}
                if (!alreadyThere) requestAnimationFrame(function() {{
                    if (v.center) {{
                        map.jumpTo({{
                            center: v.center,
                            zoom: v.zoom || DEFAULTS.zoom,
                            pitch: v.pitch || 0,
                            bearing: v.bearing || 0,
                        }});
                    }} else if (v.bbox) {{
                        map.fitBounds([[v.bbox[0], v.bbox[1]], [v.bbox[2], v.bbox[3]]], {{
                            padding: 50, maxZoom: 18,
                        }});
                    }}
                }});
            }}
        }}

        // ─── Viewport Reporter ───
        function sendViewportUpdate() {{
            if (ws && ws.readyState === WebSocket.OPEN) {{
                const center = map.getCenter();
                const bounds = map.getBounds();
                ws.send(JSON.stringify({{
                    type: 'viewport_update',
                    data: {{
                        center: [center.lng, center.lat],
                        zoom: map.getZoom(),
                        pitch: map.getPitch(),
                        bearing: map.getBearing(),
                        bbox: [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()],
                        basemap: currentBasemap,
                        terrain: currentTerrain,
                    }}
                }}));
            }}
        }}

        let viewportTimeout;
        map.on('moveend', function() {{
            clearTimeout(viewportTimeout);
            viewportTimeout = setTimeout(sendViewportUpdate, 300);
        }});

        // ─── Drawing Tools (Terra Draw) ───
        // Headless drawing: Terra Draw keeps its feature store in JS and
        // treats the map purely as a render target — after any style rebuild
        // its state is recoverable via stop()/start() (unlike the retired
        // Geoman, whose internal source bookkeeping could not survive
        // setStyle and dead-locked into 60s retry timeouts). The toolbar is
        // entirely ours, themed with the page tokens (light/dark aware).
        let drawInstance = null;
        let currentDrawMode = null; // 'polygon' | 'box' | 'circle' | 'line' | null
        let deleteMode = false;
        const drawnAssetStack = []; // undo stack: tracks asset IDs of user-drawn features

        // our draw-type names ↔ Terra Draw mode names
        const DRAW_MODE_FOR = {{ polygon: 'polygon', box: 'rectangle', circle: 'circle', line: 'linestring' }};
        const DRAW_TYPE_FOR = {{ polygon: 'polygon', rectangle: 'box', circle: 'circle', linestring: 'line' }};

        // In-progress geometry styled from the live theme tokens so drawing
        // matches the product in light AND dark (issue: map controls ignored
        // the theme). Terra Draw validates hex colors — the tokens are hex.
        function _drawAccent() {{
            const v = getComputedStyle(document.documentElement)
                .getPropertyValue('--eo-accent').trim();
            return /^#[0-9a-fA-F]{{6}}$/.test(v) ? v : '#7cc242';
        }}
        function _areaStyles() {{
            const accent = _drawAccent();
            return {{
                fillColor: accent, fillOpacity: 0.12,
                outlineColor: accent, outlineWidth: 2,
                closingPointColor: accent, closingPointWidth: 4,
                closingPointOutlineColor: '#ffffff', closingPointOutlineWidth: 2,
            }};
        }}
        function _lineStyles() {{
            const accent = _drawAccent();
            return {{
                lineStringColor: accent, lineStringWidth: 2,
                closingPointColor: accent, closingPointWidth: 4,
                closingPointOutlineColor: '#ffffff', closingPointOutlineWidth: 2,
            }};
        }}

        function updateDrawToolbar() {{
            document.querySelectorAll('.draw-toolbar button[data-draw-mode]').forEach(btn => {{
                btn.setAttribute('aria-pressed',
                    btn.getAttribute('data-draw-mode') === currentDrawMode ? 'true' : 'false');
            }});
            const del = document.querySelector('.draw-toolbar button[data-role="delete"]');
            if (del) del.setAttribute('aria-pressed', deleteMode ? 'true' : 'false');
        }}

        function setDrawMode(mode) {{
            exitDeleteMode();
            if (!drawInstance) return;
            try {{
                if (mode && DRAW_MODE_FOR[mode]) {{
                    drawInstance.setMode(DRAW_MODE_FOR[mode]);
                    currentDrawMode = mode;
                }} else {{
                    drawInstance.setMode('static');
                    currentDrawMode = null;
                }}
            }} catch (e) {{
                console.warn('setDrawMode failed:', e.message);
            }}
            updateDrawToolbar();
        }}

        // Themed toolbar — a real MapLibre control (top-left) so it stacks
        // with native controls, but every pixel is ours.
        const DRAW_ICONS = {{
            polygon: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"><path d="M12 3l8.5 6.2-3.2 10H6.7L3.5 9.2z"/></svg>',
            box: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="6" width="16" height="12" rx="1"/></svg>',
            circle: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="8"/></svg>',
            line: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 18L10 8l4 6 6-9"/><circle cx="4" cy="18" r="1.6" fill="currentColor"/><circle cx="20" cy="5" r="1.6" fill="currentColor"/></svg>',
            pan: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M2 12h20M12 2l-3 3M12 2l3 3M12 22l-3-3M12 22l3-3M2 12l3-3M2 12l3 3M22 12l-3-3M22 12l-3 3"/></svg>',
            del: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M4 7h16M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2m3 0l-.8 12a2 2 0 0 1-2 1.9H8.8a2 2 0 0 1-2-1.9L6 7"/></svg>',
        }};

        class DrawToolbarControl {{
            onAdd() {{
                const wrap = document.createElement('div');
                wrap.className = 'maplibregl-ctrl draw-toolbar';
                const add = (title, html, onClick, attrs) => {{
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.title = title;
                    b.innerHTML = html;
                    b.setAttribute('aria-pressed', 'false');
                    for (const [k, v] of Object.entries(attrs || {{}})) b.setAttribute(k, v);
                    b.addEventListener('click', onClick);
                    wrap.appendChild(b);
                }};
                add('Draw polygon', DRAW_ICONS.polygon,
                    () => setDrawMode(currentDrawMode === 'polygon' ? null : 'polygon'),
                    {{ 'data-draw-mode': 'polygon' }});
                add('Draw rectangle', DRAW_ICONS.box,
                    () => setDrawMode(currentDrawMode === 'box' ? null : 'box'),
                    {{ 'data-draw-mode': 'box' }});
                add('Draw circle', DRAW_ICONS.circle,
                    () => setDrawMode(currentDrawMode === 'circle' ? null : 'circle'),
                    {{ 'data-draw-mode': 'circle' }});
                add('Draw line', DRAW_ICONS.line,
                    () => setDrawMode(currentDrawMode === 'line' ? null : 'line'),
                    {{ 'data-draw-mode': 'line' }});
                add('Pan (Esc)', DRAW_ICONS.pan, () => activatePanMode());
                add('Delete asset (click shape to remove)', DRAW_ICONS.del,
                    () => {{ toggleDeleteMode(); updateDrawToolbar(); }},
                    {{ 'data-role': 'delete' }});
                this._el = wrap;
                return wrap;
            }}
            onRemove() {{
                this._el && this._el.parentNode && this._el.parentNode.removeChild(this._el);
            }}
        }}

        map.on('load', function() {{
            try {{
                const td = window.terraDraw;
                const tda = window.terraDrawMaplibreGlAdapter;
                drawInstance = new td.TerraDraw({{
                    adapter: new tda.TerraDrawMapLibreGLAdapter({{ map }}),
                    modes: [
                        new td.TerraDrawPolygonMode({{ styles: _areaStyles() }}),
                        new td.TerraDrawRectangleMode({{ styles: _areaStyles() }}),
                        new td.TerraDrawCircleMode({{ styles: _areaStyles() }}),
                        new td.TerraDrawLineStringMode({{ styles: _lineStyles() }}),
                    ],
                }});
                drawInstance.start();
                console.log('Terra Draw initialized');

                // Re-style in-progress geometry when the theme flips (the
                // accent differs slightly between light and dark).
                new MutationObserver(() => {{
                    if (!drawInstance) return;
                    try {{
                        drawInstance.updateModeOptions('polygon', {{ styles: _areaStyles() }});
                        drawInstance.updateModeOptions('rectangle', {{ styles: _areaStyles() }});
                        drawInstance.updateModeOptions('circle', {{ styles: _areaStyles() }});
                        drawInstance.updateModeOptions('linestring', {{ styles: _lineStyles() }});
                    }} catch (e) {{ /* styles refresh is cosmetic */ }}
                }}).observe(document.documentElement, {{ attributes: true, attributeFilter: ['data-theme'] }});

                // A completed shape: render the dashed pending copy RIGHT NOW,
                // hand the geometry to the server (echo returns the confirmed
                // green asset), and clear it from Terra Draw's store — the
                // asset pipeline owns it from here. The draw mode STAYS active
                // so consecutive shapes need no re-click.
                drawInstance.on('finish', function(id, context) {{
                    if (context && context.action && context.action !== 'draw') return;
                    const feature = drawInstance.getSnapshot().find(f => f.id === id);
                    if (!feature || !feature.geometry) return;

                    const mode = (feature.properties && feature.properties.mode) || 'polygon';
                    const drawType = DRAW_TYPE_FOR[mode] || 'polygon';
                    const geojson = {{ type: 'Feature', properties: {{}}, geometry: feature.geometry }};
                    const geojsonStr = JSON.stringify(geojson);

                    console.log('Draw complete:', drawType, mode);

                    // Dashed pending copy, swapped for the confirmed asset
                    // when the server's add_polygon echo returns this
                    // client_id. Near-instant on a healthy link; on a
                    // congested one the dash is the honest "not landed on
                    // the server yet" signal.
                    const clientId = 'draw-' + Date.now().toString(36) + '-' +
                        Math.random().toString(36).slice(2, 8);
                    addPendingDraw(clientId, geojson);

                    if (ws && ws.readyState === WebSocket.OPEN) {{
                        ws.send(JSON.stringify({{
                            type: 'user_drawn_feature',
                            data: {{
                                geojson: geojsonStr,
                                draw_type: drawType,
                                client_id: clientId,
                            }}
                        }}));
                    }} else {{
                        console.warn('WebSocket not open — drawn shape kept pending');
                    }}

                    try {{ drawInstance.removeFeatures([id]); }} catch (e) {{}}
                }});

                if (!UI_NAKED) map.addControl(new DrawToolbarControl(), 'top-left');

                // Escape key exits all modes
                document.addEventListener('keydown', function(e) {{
                    if (e.key === 'Escape') {{
                        activatePanMode();
                    }}
                }});
            }} catch (err) {{
                console.warn('Terra Draw init failed (library may not be loaded):', err.message);
                console.log('Drawing tools will not be available');
            }}
        }});

        // ─── Delete Mode (click-to-delete assets) ───
        function onDeleteClick(e) {{
            // Get all our asset layer IDs
            const allLayerIds = [];
            for (const [assetId, reg] of Object.entries(assetRegistry)) {{
                for (const lid of reg.layerIds) {{
                    allLayerIds.push(lid);
                }}
            }}
            if (allLayerIds.length === 0) return;

            // Query rendered features at click point
            const features = map.queryRenderedFeatures(e.point, {{ layers: allLayerIds }});
            if (features.length === 0) return;

            // Find which asset this layer belongs to
            const clickedLayerId = features[0].layer.id;
            let targetAssetId = null;
            for (const [assetId, reg] of Object.entries(assetRegistry)) {{
                if (reg.layerIds.includes(clickedLayerId)) {{
                    targetAssetId = assetId;
                    break;
                }}
            }}
            if (!targetAssetId) return;

            console.log('Delete mode: deleting asset', targetAssetId);

            // Send delete to server via WebSocket
            if (ws && ws.readyState === WebSocket.OPEN) {{
                ws.send(JSON.stringify({{
                    type: 'user_delete_asset',
                    data: {{ asset_id: targetAssetId }}
                }}));
            }}

            // Remove from undo stack
            const idx = drawnAssetStack.indexOf(targetAssetId);
            if (idx !== -1) drawnAssetStack.splice(idx, 1);
            updateUndoButton();
        }}

        function enterDeleteMode() {{
            if (drawInstance) {{
                try {{ drawInstance.setMode('static'); }} catch (e) {{}}
            }}
            currentDrawMode = null;
            deleteMode = true;
            map.getCanvas().classList.add('delete-mode-active');
            map.on('click', onDeleteClick);
            updateDrawToolbar();
        }}

        function exitDeleteMode() {{
            deleteMode = false;
            map.getCanvas().classList.remove('delete-mode-active');
            map.off('click', onDeleteClick);
            updateDrawToolbar();
        }}

        function toggleDeleteMode() {{
            if (deleteMode) {{
                exitDeleteMode();
            }} else {{
                enterDeleteMode();
            }}
        }}

        // ─── Undo (delete last drawn feature) ───
        function updateUndoButton() {{
            // No-op: custom undo button was removed; undo available via SDK/API
        }}

        function undoLastDrawn() {{
            if (drawnAssetStack.length === 0) return;
            const lastAssetId = drawnAssetStack.pop();
            updateUndoButton();

            // Send delete to server via WebSocket
            if (ws && ws.readyState === WebSocket.OPEN) {{
                ws.send(JSON.stringify({{
                    type: 'user_delete_asset',
                    data: {{ asset_id: lastAssetId }}
                }}));
            }}
            console.log('Undo: deleted asset', lastAssetId);
        }}

        // ─── Pan Mode (exit all draw/delete modes) ───
        function activatePanMode() {{
            setDrawMode(null);
        }}

        // Track drawn asset IDs from draw_complete confirmations
        // (added to the undo stack when the server confirms creation)

        // SDK/API drawing controls
        function startDrawPolygon() {{ setDrawMode('polygon'); }}
        function startDrawBox() {{ setDrawMode('box'); }}
        function cancelDraw() {{ setDrawMode(null); }}

        // ─── View Mode (2D flat / 3D globe) ───
        // '3d' is GLOBE PROJECTION + SKY only. MapLibre's raster-DEM terrain
        // is retired: combined with globe (vertical perspective) it is
        // explicitly half-supported upstream and was the trigger of a
        // per-frame painter crash class ("shaderPreludeCode ... undefined")
        // that stalled drawing into 60s Geoman timeouts. Real 3D terrain is
        // the Babylon/3D-Tiles view's job.
        function applyTerrainMode(mode, animate) {{
            currentTerrain = mode;
            if (mode === '3d') {{
                try {{ map.setProjection({{ type: 'globe' }}); }} catch(e) {{ console.warn('Globe projection not available:', e.message); }}
                enableSky();
                if (animate) {{
                    map.easeTo({{ pitch: 60, duration: 1500 }});
                }} else {{
                    map.jumpTo({{ pitch: 60 }});
                }}
                console.log('View mode: 3D Globe (pitch 60, sky on, no terrain)');
            }} else {{
                // 2D mode — flat Mercator
                try {{ map.setProjection({{ type: 'mercator' }}); }} catch(e) {{ console.warn('Projection switch failed:', e.message); }}
                if (animate) {{
                    map.easeTo({{ pitch: 0, bearing: 0, duration: 1500 }});
                    setTimeout(disableSky, 1600);
                }} else {{
                    map.jumpTo({{ pitch: 0, bearing: 0 }});
                    disableSky();
                }}
                console.log('View mode: 2D Flat (mercator)');
            }}
            // Projection change flips which arc representation is visible
            // (deck ribbon in mercator, flat line in globe) — and forces a
            // deck setProps so the ribbon re-draws after the switch.
            syncArcMode();
            // Notify parent frame (for demo pages) of terrain mode change
            try {{ window.parent.postMessage({{ type: 'terrain_mode', mode: mode }}, '*'); }} catch(e) {{}}
            sendViewportUpdate();
        }}

        // ─── WebSocket ───
        let ws;
        let reconnectDelay = 1000;

        function connectWS() {{
            ws = new WebSocket(WS_URL);

            ws.onopen = () => {{
                console.log('WebSocket connected');
                reconnectDelay = 1000;
                ws.send(JSON.stringify({{ type: 'session_restore' }}));
            }};

            ws.onmessage = (event) => {{
                const msg = JSON.parse(event.data);
                if (msg.type === 'session_snapshot') {{
                    handleSnapshot(msg.data);
                }} else if (msg.type === 'take_screenshot') {{
                    // Capture the map canvas and send it back
                    const correlationId = msg.correlation_id;
                    try {{
                        // Capture with retry: if data is too small, wait for more rendering
                        let retries = 0;
                        const MAX_RETRIES = 5;
                        const MIN_DATA_LEN = 50000;  // ~37 KB PNG minimum for a real screenshot
                        function captureWithRetry() {{
                            const canvas = map.getCanvas();
                            console.log('[Screenshot] attempt', retries + 1, 'loaded:', map.loaded(),
                                'tiles:', map.areTilesLoaded(), 'canvas:', canvas.width + 'x' + canvas.height);
                            try {{
                                const dataUrl = canvas.toDataURL('image/png');
                                console.log('[Screenshot] toDataURL len:', dataUrl.length, '(~' + Math.round(dataUrl.length * 0.75 / 1024) + ' KB)');
                                if (dataUrl.length >= MIN_DATA_LEN || retries >= MAX_RETRIES) {{
                                    if (dataUrl.length < MIN_DATA_LEN) {{
                                        console.warn('[Screenshot] Still small after', retries + 1, 'attempts, sending anyway');
                                    }}
                                    ws.send(JSON.stringify({{ type: 'screenshot_result', correlation_id: correlationId, data: dataUrl }}));
                                    return;
                                }}
                                // Data too small — map likely hasn't finished rendering, wait and retry
                                retries++;
                                console.log('[Screenshot] Data too small, retrying in 1s (attempt', retries + 1, ')');
                                setTimeout(function() {{
                                    map.triggerRepaint();
                                    map.once('idle', captureWithRetry);
                                }}, 1000);
                            }} catch (e) {{
                                console.error('[Screenshot] toDataURL error:', e.name, e.message);
                                ws.send(JSON.stringify({{ type: 'screenshot_result', correlation_id: correlationId, error: e.message }}));
                            }}
                        }}
                        // Start: wait for idle then try capture
                        if (map.loaded() && map.areTilesLoaded()) {{
                            captureWithRetry();
                        }} else {{
                            map.once('idle', captureWithRetry);
                        }}
                    }} catch (err) {{
                        ws.send(JSON.stringify({{
                            type: 'screenshot_result',
                            correlation_id: correlationId,
                            error: err.message || 'Canvas capture failed',
                        }}));
                    }}
                }} else if (handlers[msg.type]) {{
                    handlers[msg.type](msg.data);
                }} else {{
                    console.warn('Unknown event type:', msg.type);
                }}
            }};

            ws.onclose = () => {{
                console.log('WebSocket disconnected, reconnecting in', reconnectDelay, 'ms');
                setTimeout(connectWS, reconnectDelay);
                reconnectDelay = Math.min(reconnectDelay * 2, 30000);
            }};

            ws.onerror = (err) => {{
                console.error('WebSocket error:', err);
                ws.close();
            }};
        }}

        // Wait for map style to load before connecting WebSocket
        map.on('load', async () => {{
            if (USER_SESSION) {{
                connectWS();
            }} else {{
                // Auto-create a session if none provided in URL
                console.log('No user_session in URL, auto-creating session...');
                try {{
                    const resp = await fetch(BASE_URL + '/api/maps/' + MAP_ID + '/sessions', {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify({{}}),
                    }});
                    if (resp.ok) {{
                        const data = await resp.json();
                        // Update the global and URL
                        window.USER_SESSION_AUTO = data.user_session_id;
                        const newUrl = window.location.pathname + '?user_session=' + data.user_session_id;
                        window.history.replaceState(null, '', newUrl);
                        // Rebuild WS URL with the new session
                        const wsProto2 = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
                        const newWsUrl = wsProto2 + '//' + window.location.host + ROOT_PATH + '/ws/' + MAP_ID + '/' + data.user_session_id;

                        // Override the WS URL and connect
                        ws = new WebSocket(newWsUrl);
                        ws.onopen = () => {{
                            console.log('WebSocket connected (auto-session)');
                            reconnectDelay = 1000;
                            ws.send(JSON.stringify({{ type: 'session_restore' }}));
                        }};
                        ws.onmessage = (event) => {{
                            const msg = JSON.parse(event.data);
                            if (msg.type === 'session_snapshot') {{
                                handleSnapshot(msg.data);
                            }} else if (msg.type === 'take_screenshot') {{
                                const correlationId = msg.correlation_id;
                                try {{
                                    function captureWhenReady2() {{
                                        const c = map.getCanvas();
                                        console.log('[Screenshot2] loaded:', map.loaded(), 'tiles:', map.areTilesLoaded(),
                                            'canvas:', c.width + 'x' + c.height);
                                        if (map.loaded() && map.areTilesLoaded()) {{
                                            const d = c.toDataURL('image/png');
                                            console.log('[Screenshot2] Captured immediately, len:', d.length);
                                            ws.send(JSON.stringify({{ type: 'screenshot_result', correlation_id: correlationId, data: d }}));
                                        }} else {{
                                            map.once('idle', () => {{
                                                const d2 = map.getCanvas().toDataURL('image/png');
                                                console.log('[Screenshot2] Captured after idle, len:', d2.length);
                                                ws.send(JSON.stringify({{ type: 'screenshot_result', correlation_id: correlationId, data: d2 }}));
                                            }});
                                        }}
                                    }}
                                    captureWhenReady2();
                                }} catch (err) {{
                                    ws.send(JSON.stringify({{ type: 'screenshot_result', correlation_id: correlationId, error: err.message }}));
                                }}
                            }} else if (handlers[msg.type]) {{
                                handlers[msg.type](msg.data);
                            }}
                        }};
                        ws.onclose = () => {{ setTimeout(() => {{ location.reload(); }}, 5000); }};
                        ws.onerror = (err) => {{ console.error('WS error:', err); ws.close(); }};
                        console.log('Auto-created session:', data.user_session_id);
                    }} else {{
                        console.warn('Failed to auto-create session:', resp.status);
                    }}
                }} catch (e) {{
                    console.warn('Auto-session creation failed:', e);
                }}
            }}
        }});

        // ─── ESIP interaction-contract bridge ─────────────────────────────
        // Publish the few internals the always-loaded esip-contract.js needs
        // to wire up asset_hover / asset_click / map_click and window.ESIPMap.
        // This tiny handoff is the ONLY coupling between this inline shell and
        // the contract: the contract drives the map exclusively through the
        // public REST events API (never by poking these internals), so the
        // built-in outfit and any third-party UI share one identical surface.
        function __esipGetUserSession() {{
            return window.USER_SESSION_AUTO || USER_SESSION || '';
        }}
        window.__esipInternals = {{
            map: map,
            registry: assetRegistry,
            basemaps: BASEMAPS,
            handlers: handlers,
            mapId: MAP_ID,
            baseUrl: BASE_URL,
            getUserSession: __esipGetUserSession,
            getCurrentBasemap: function () {{ return currentBasemap; }},
            // Lets the contract suppress hover hit-tests while the user is
            // actively drawing/deleting (mousemove-heavy interactions).
            isDrawing: function () {{ return currentDrawMode !== null || deleteMode; }},
        }};
        try {{ window.dispatchEvent(new CustomEvent('esip:internals-ready')); }} catch (e) {{}}
    </script>
    {ui_body}
</body>
</html>"""

    return HTMLResponse(content=html)


# ─── Screenshot endpoint ─────────────────────────────────────────────────────

from fastapi import HTTPException


@app.post("/api/maps/{map_id}/sessions/{user_session_id}/screenshot")
async def take_screenshot(
    map_id: str,
    user_session_id: str,
    request: Request,
    width: int = 1280,
    height: int = 720,
    method: str = "playwright",
):
    """Take a screenshot of the map.

    Uses headless Chromium (Playwright) by default — works everywhere, no browser
    needed. If a real browser is connected via WebSocket and method="websocket",
    uses the faster canvas capture approach instead.

    Query params:
        width: Viewport width in pixels (default: 1280)
        height: Viewport height in pixels (default: 720)
        method: "auto" (default), "playwright", or "websocket"
    """
    base_url = str(request.base_url).rstrip("/")

    # Determine which method to use
    use_websocket = (
        method == "websocket"
        or (method == "auto" and manager.has_session_connection(map_id, user_session_id))
    )

    if use_websocket and manager.has_session_connection(map_id, user_session_id):
        # Fast path: WebSocket canvas capture from connected browser
        correlation_id, pending = await screenshot_service.request_ws_screenshot()
        await manager.send_to_session(map_id, user_session_id, {
            "type": "take_screenshot",
            "correlation_id": correlation_id,
        })
        try:
            result = await screenshot_service.await_ws_screenshot(
                correlation_id, timeout=30.0
            )
        except TimeoutError as e:
            raise HTTPException(status_code=504, detail=str(e))
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        # Default path: Playwright headless screenshot. Chromium runs on this
        # host and must self-navigate over loopback on the bound port — the
        # request base_url may be a public/proxy origin unreachable from here.
        map_url = f"{_internal_base_url()}/map/{map_id}?user_session={user_session_id}"
        try:
            result = await screenshot_service.take_screenshot_playwright(
                map_url=map_url,
                width=width,
                height=height,
            )
        except RuntimeError as e:
            raise HTTPException(status_code=500, detail=str(e))

    return {
        "screenshot_id": result.screenshot_id,
        "url": result.url,
        "full_url": f"{base_url}{result.url}",
        "filename": result.filename,
    }


# ─── Screenshot demo page ────────────────────────────────────────────────────

@app.get("/demo/screenshot")
async def screenshot_demo_page(request: Request):
    """Serve a demo page that embeds a map in an iframe with a screenshot button."""
    base_url = str(request.base_url).rstrip("/")

    demo_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MapControl Screenshot Demo</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        .toolbar {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 12px 20px;
            background: #16213e;
            border-bottom: 1px solid #0f3460;
        }}
        .toolbar h1 {{
            font-size: 18px;
            font-weight: 600;
            color: #e94560;
        }}
        .toolbar button {{
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
        }}
        #screenshotBtn {{
            background: #e94560;
            color: white;
        }}
        #screenshotBtn:hover {{ background: #c73650; }}
        #screenshotBtn:disabled {{ background: #555; cursor: wait; }}
        #setupBtn {{
            background: #0f3460;
            color: #e94560;
            border: 1px solid #e94560;
        }}
        #setupBtn:hover {{ background: #1a4a80; }}
        .status {{
            font-size: 13px;
            color: #aaa;
            flex: 1;
            text-align: right;
        }}
        .status.success {{ color: #4ecca3; }}
        .status.error {{ color: #e94560; }}
        .status.loading {{ color: #ffd93d; }}
        .map-container {{
            flex: 1;
            position: relative;
        }}
        .map-container iframe {{
            width: 100%;
            height: 100%;
            border: none;
        }}
    </style>
</head>
<body>
    <div class="toolbar">
        <h1>MapControl Screenshot Demo</h1>
        <button id="setupBtn" onclick="setupMap()">Setup Demo Assets</button>
        <button id="screenshotBtn" onclick="takeScreenshot()">📸 Take Screenshot</button>
        <span id="status" class="status">Click "Setup Demo Assets" to begin</span>
    </div>
    <div class="map-container">
        <iframe id="mapFrame" src="about:blank"></iframe>
    </div>

    <script>
        const BASE_URL = "{base_url}";
        let mapId = null;
        let sessionId = null;

        async function setupMap() {{
            const status = document.getElementById('status');
            const setupBtn = document.getElementById('setupBtn');
            setupBtn.disabled = true;
            status.textContent = 'Creating map...';
            status.className = 'status loading';

            try {{
                // Create map
                const mapResp = await fetch(BASE_URL + '/api/maps', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: 'screenshot-demo' }}),
                }});
                const mapData = await mapResp.json();
                mapId = mapData.map_id;

                // Create session
                const sessResp = await fetch(BASE_URL + '/api/maps/' + mapId + '/sessions', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{}}),
                }});
                const sessData = await sessResp.json();
                sessionId = sessData.user_session_id;

                // Load map in iframe
                const mapUrl = BASE_URL + '/map/' + mapId + '?user_session=' + sessionId;
                document.getElementById('mapFrame').src = mapUrl;

                // Wait for map to load
                status.textContent = 'Loading map...';
                await new Promise(r => setTimeout(r, 2000));

                // Upload GeoTIFF (sample.tif served locally from /example_tiff/)
                status.textContent = 'Loading GeoTIFF...';
                try {{
                    const tiffUrl = BASE_URL + '/example_tiff/sample.tif';
                    const tiffResp = await fetch(tiffUrl);
                    if (tiffResp.ok) {{
                        const tiffBlob = await tiffResp.blob();
                        status.textContent = 'Uploading GeoTIFF to map...';
                        const formData = new FormData();
                        formData.append('file', tiffBlob, 'sample.tif');
                        formData.append('mode', 'singleband');
                        formData.append('colormap', 'viridis');
                        formData.append('name', 'Sample GeoTIFF');
                        formData.append('user_session_id', sessionId);
                        await fetch(BASE_URL + '/api/maps/' + mapId + '/upload-geotiff', {{
                            method: 'POST',
                            body: formData,
                        }});
                        await new Promise(r => setTimeout(r, 1000));
                    }} else {{
                        console.warn('Could not fetch GeoTIFF from GitHub, skipping');
                    }}
                }} catch (e) {{
                    console.warn('GeoTIFF download failed, skipping:', e.message);
                }}

                // GeoTIFF bounds: lon 4.32-4.47, lat 52.17-52.26 (Netherlands, near Leiden)
                // Add a polygon near the GeoTIFF area
                status.textContent = 'Adding polygon...';
                await sendEvent('add_polygon', {{
                    geojson: JSON.stringify({{
                        type: 'Feature',
                        properties: {{ name: 'Observation Zone' }},
                        geometry: {{
                            type: 'Polygon',
                            coordinates: [[
                                [4.30, 52.15], [4.50, 52.15], [4.50, 52.27],
                                [4.30, 52.27], [4.30, 52.15]
                            ]]
                        }}
                    }}),
                    style: {{ fill_color: '#e9456040', stroke_color: '#e94560', stroke_width: 2 }},
                }});
                await new Promise(r => setTimeout(r, 500));

                // Add a path (route near the GeoTIFF)
                status.textContent = 'Adding route...';
                await sendEvent('add_path', {{
                    geojson: JSON.stringify({{
                        type: 'Feature',
                        properties: {{ name: 'Survey Route' }},
                        geometry: {{
                            type: 'LineString',
                            coordinates: [
                                [4.33, 52.17], [4.36, 52.19], [4.38, 52.21],
                                [4.40, 52.22], [4.43, 52.24], [4.46, 52.25]
                            ]
                        }}
                    }}),
                    style: {{ stroke_color: '#ffd93d', stroke_width: 4 }},
                }});
                await new Promise(r => setTimeout(r, 500));

                // Add a point marker at the center of the GeoTIFF
                status.textContent = 'Adding marker...';
                await sendEvent('add_point', {{
                    geojson: JSON.stringify({{
                        type: 'Feature',
                        properties: {{ name: 'Observation Point' }},
                        geometry: {{
                            type: 'Point',
                            coordinates: [4.3982, 52.2128]
                        }}
                    }}),
                    style: {{ fill_color: '#4ecca3', stroke_color: '#fff', stroke_width: 2 }},
                }});
                await new Promise(r => setTimeout(r, 500));

                // Zoom to the GeoTIFF area
                status.textContent = 'Zooming to assets...';
                await sendEvent('zoom_to_bbox', {{ bbox: [4.28, 52.13, 4.52, 52.29] }});
                await new Promise(r => setTimeout(r, 2000));

                status.textContent = 'Map ready. Click "Take Screenshot" to capture.';
                status.className = 'status success';
                setupBtn.disabled = false;

            }} catch (err) {{
                status.textContent = 'Error: ' + err.message;
                status.className = 'status error';
                setupBtn.disabled = false;
            }}
        }}

        async function sendEvent(type, data) {{
            return fetch(BASE_URL + '/api/maps/' + mapId + '/events', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify({{ type, data, user_session_id: sessionId }}),
            }});
        }}

        async function takeScreenshot() {{
            if (!mapId || !sessionId) {{
                alert('Please set up the map first.');
                return;
            }}

            const status = document.getElementById('status');
            const btn = document.getElementById('screenshotBtn');
            btn.disabled = true;
            status.textContent = 'Capturing screenshot...';
            status.className = 'status loading';

            try {{
                const resp = await fetch(
                    BASE_URL + '/api/maps/' + mapId + '/sessions/' + sessionId + '/screenshot',
                    {{ method: 'POST' }}
                );

                if (!resp.ok) {{
                    const err = await resp.json();
                    throw new Error(err.detail || 'Screenshot failed');
                }}

                const result = await resp.json();
                status.textContent = 'Screenshot saved. Downloading...';
                status.className = 'status success';

                // Download the screenshot
                const link = document.createElement('a');
                link.href = result.full_url;
                link.download = result.filename;
                document.body.appendChild(link);
                link.click();
                document.body.removeChild(link);

                status.textContent = 'Screenshot downloaded: ' + result.filename;
                status.className = 'status success';

            }} catch (err) {{
                status.textContent = 'Error: ' + err.message;
                status.className = 'status error';
            }} finally {{
                btn.disabled = false;
            }}
        }}
    </script>
</body>
</html>"""

    return HTMLResponse(content=demo_html)


# ─── Drawing demo page ────────────────────────────────────────────────────────

@app.get("/demo/drawing")
async def drawing_demo_page(request: Request):
    """Demo page: map in iframe with live asset list panel."""
    base_url = str(request.base_url).rstrip("/")

    demo_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MapControl Drawing Demo</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 10px 20px;
            background: #16213e;
            border-bottom: 1px solid #0f3460;
        }}
        .header h1 {{ font-size: 16px; font-weight: 600; color: #e94560; }}
        .header .status {{ font-size: 13px; color: #aaa; flex: 1; text-align: right; }}
        .header .status.ok {{ color: #4ecca3; }}
        .header .status.loading {{ color: #ffd93d; }}
        .main {{
            flex: 1;
            display: flex;
            overflow: hidden;
        }}
        .map-pane {{
            flex: 1;
            position: relative;
        }}
        .map-pane iframe {{
            width: 100%;
            height: 100%;
            border: none;
        }}
        .panel {{
            width: 320px;
            background: #16213e;
            border-left: 1px solid #0f3460;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .panel-header {{
            padding: 12px 16px;
            font-size: 14px;
            font-weight: 600;
            color: #e94560;
            border-bottom: 1px solid #0f3460;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }}
        .panel-header .count {{ color: #aaa; font-weight: 400; font-size: 12px; }}
        .asset-list {{
            flex: 1;
            overflow-y: auto;
            padding: 8px;
        }}
        .asset-item {{
            background: #1a1a2e;
            border: 1px solid #0f3460;
            border-radius: 6px;
            padding: 10px 12px;
            margin-bottom: 6px;
            font-size: 13px;
        }}
        .asset-item .name {{ font-weight: 600; color: #fff; margin-bottom: 4px; }}
        .asset-item .meta {{ color: #888; font-size: 11px; }}
        .asset-item .type {{ color: #4ecca3; }}
        .asset-item .id {{ color: #666; font-family: monospace; font-size: 10px; }}
        .asset-item .hidden {{ color: #e94560; }}
        .empty {{ color: #555; text-align: center; padding: 40px 20px; font-size: 13px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>MapControl Drawing Demo</h1>
        <span id="status" class="status loading">Setting up...</span>
    </div>
    <div class="main">
        <div class="map-pane">
            <iframe id="mapFrame" src="about:blank"></iframe>
        </div>
        <div class="panel">
            <div class="panel-header">
                Assets <span id="assetCount" class="count">(0)</span>
            </div>
            <div id="assetList" class="asset-list">
                <div class="empty">Loading...</div>
            </div>
        </div>
    </div>

    <script>
        const BASE_URL = "{base_url}";
        let mapId = null;
        let sessionId = null;

        async function setup() {{
            const status = document.getElementById('status');

            try {{
                // Create map
                status.textContent = 'Creating map...';
                const mapResp = await fetch(BASE_URL + '/api/maps', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{ name: 'drawing-demo' }}),
                }});
                const mapData = await mapResp.json();
                mapId = mapData.map_id;

                // Create session
                const sessResp = await fetch(BASE_URL + '/api/maps/' + mapId + '/sessions', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{}}),
                }});
                const sessData = await sessResp.json();
                sessionId = sessData.user_session_id;

                // Load map in iframe
                const mapUrl = BASE_URL + '/map/' + mapId + '?user_session=' + sessionId;
                document.getElementById('mapFrame').src = mapUrl;

                // Wait for map to load
                status.textContent = 'Loading map...';
                await new Promise(r => setTimeout(r, 2500));

                // Add a reference polygon near Austin, TX
                status.textContent = 'Adding polygon...';
                await fetch(BASE_URL + '/api/maps/' + mapId + '/events', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        type: 'add_polygon',
                        user_session_id: sessionId,
                        data: {{
                            geojson: JSON.stringify({{
                                type: 'Feature',
                                properties: {{ name: 'Downtown Austin' }},
                                geometry: {{
                                    type: 'Polygon',
                                    coordinates: [[
                                        [-97.76, 30.26], [-97.76, 30.28],
                                        [-97.73, 30.28], [-97.73, 30.26],
                                        [-97.76, 30.26]
                                    ]]
                                }}
                            }}),
                            name: 'Downtown Austin',
                            style: {{ fill_color: '#e9456040', stroke_color: '#e94560', stroke_width: 2 }},
                        }}
                    }}),
                }});
                await new Promise(r => setTimeout(r, 500));

                // Zoom to the polygon
                status.textContent = 'Zooming...';
                await fetch(BASE_URL + '/api/maps/' + mapId + '/events', {{
                    method: 'POST',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        type: 'zoom_to_bbox',
                        user_session_id: sessionId,
                        data: {{ bbox: [-97.78, 30.24, -97.71, 30.30] }}
                    }}),
                }});
                await new Promise(r => setTimeout(r, 1000));

                status.textContent = 'Ready — draw shapes on the map, see them here';
                status.className = 'status ok';

                // Start polling for assets
                pollAssets();
                setInterval(pollAssets, 3000);

            }} catch (err) {{
                status.textContent = 'Error: ' + err.message;
            }}
        }}

        async function pollAssets() {{
            if (!mapId) return;
            try {{
                const resp = await fetch(BASE_URL + '/api/maps/' + mapId + '/assets');
                if (!resp.ok) return;
                const assets = await resp.json();
                renderAssets(assets);
            }} catch (e) {{
                // ignore polling errors
            }}
        }}

        function timeAgo(isoStr) {{
            if (!isoStr) return '';
            const then = new Date(isoStr);
            const now = new Date();
            let secs = Math.floor((now - then) / 1000);
            if (secs < 0) secs = 0;
            if (secs < 60) return secs + 's ago';
            let mins = Math.floor(secs / 60);
            if (mins < 60) return mins + ' min ago';
            let hrs = Math.floor(mins / 60);
            mins = mins % 60;
            if (hrs < 24) return hrs + 'h ' + mins + 'min ago';
            let days = Math.floor(hrs / 24);
            hrs = hrs % 24;
            if (days < 30) return days + 'd ' + hrs + 'h ' + mins + 'min ago';
            let months = Math.floor(days / 30);
            days = days % 30;
            return months + 'mo ' + days + 'd ago';
        }}

        function renderAssets(assets) {{
            const list = document.getElementById('assetList');
            document.getElementById('assetCount').textContent = '(' + assets.length + ')';

            if (assets.length === 0) {{
                list.innerHTML = '<div class="empty">No assets yet.<br>Draw something on the map!</div>';
                return;
            }}

            list.innerHTML = assets.map(function(a) {{
                const vis = a.visible ? '' : ' <span class="hidden">[hidden]</span>';
                const ago = timeAgo(a.created_at);
                return '<div class="asset-item">' +
                    '<div class="name">' + (a.name || '(unnamed)') + vis + '</div>' +
                    '<div class="meta"><span class="type">' + a.asset_type + '</span> · ' + ago + '</div>' +
                    '<div class="meta"><span class="id">' + a.asset_id + '</span></div>' +
                '</div>';
            }}).join('');
        }}

        // Auto-start
        setup();
    </script>
</body>
</html>"""

    return HTMLResponse(content=demo_html)


# ─── Terrain demo page ────────────────────────────────────────────────────────

@app.get("/demo/terrain")
async def terrain_demo_page(request: Request):
    """Demo page: map in iframe with 2D/3D mode indicator in the wrapper."""
    base_url = str(request.base_url).rstrip("/")

    demo_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MapControl 3D Terrain Demo</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: #1a1a2e;
            color: #eee;
            height: 100vh;
            display: flex;
            flex-direction: column;
        }}
        .header {{
            display: flex;
            align-items: center;
            gap: 16px;
            padding: 10px 20px;
            background: #16213e;
            border-bottom: 1px solid #0f3460;
        }}
        .header h1 {{ font-size: 16px; font-weight: 600; color: #e94560; }}
        .header .status {{ font-size: 13px; color: #aaa; flex: 1; text-align: right; }}
        .header .status.ok {{ color: #4ecca3; }}
        .header .status.loading {{ color: #ffd93d; }}
        .mode-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.5s ease;
        }}
        .mode-badge.mode-2d {{
            background: #0f3460;
            color: #aaa;
            border: 1px solid #0f3460;
        }}
        .mode-badge.mode-3d {{
            background: linear-gradient(135deg, #0f3460, #e94560);
            color: #fff;
            border: 1px solid #e94560;
            box-shadow: 0 0 12px rgba(233, 69, 96, 0.3);
        }}
        .mode-badge .icon {{ font-size: 16px; }}
        .map-container {{
            flex: 1;
            position: relative;
        }}
        .map-container iframe {{
            width: 100%;
            height: 100%;
            border: none;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>MapControl 3D Terrain Demo</h1>
        <span id="modeBadge" class="mode-badge mode-2d">
            <span class="icon">📍</span>
            <span id="modeText">2D Flat</span>
        </span>
        <span id="status" class="status loading">Waiting for demo script...</span>
    </div>
    <div class="map-container">
        <iframe id="mapFrame" src="about:blank"></iframe>
    </div>

    <script>
        const BASE_URL = "{base_url}";

        // Listen for postMessage events from the map iframe
        window.addEventListener('message', function(event) {{
            if (event.data && event.data.type === 'terrain_mode') {{
                updateModeIndicator(event.data.mode);
            }}
            if (event.data && event.data.type === 'demo_label') {{
                document.getElementById('status').textContent = event.data.text || '';
                document.getElementById('status').className = 'status ok';
            }}
        }});

        function updateModeIndicator(mode) {{
            const badge = document.getElementById('modeBadge');
            const text = document.getElementById('modeText');
            if (mode === '3d') {{
                badge.className = 'mode-badge mode-3d';
                text.textContent = '3D Globe';
                badge.querySelector('.icon').textContent = '🌍';
            }} else {{
                badge.className = 'mode-badge mode-2d';
                text.textContent = '2D Flat';
                badge.querySelector('.icon').textContent = '📍';
            }}
        }}

        // Check URL params for map_id and user_session
        const params = new URLSearchParams(window.location.search);
        const mapId = params.get('map_id');
        const sessionId = params.get('user_session');

        if (mapId && sessionId) {{
            // Load the map directly
            const mapUrl = BASE_URL + '/map/' + mapId + '?user_session=' + sessionId;
            document.getElementById('mapFrame').src = mapUrl;
            document.getElementById('status').textContent = 'Map loaded — controlled by demo script';
            document.getElementById('status').className = 'status ok';
        }} else {{
            document.getElementById('status').textContent = 'Open via demo_terrain_showcase.py';
        }}
    </script>
</body>
</html>"""

    return HTMLResponse(content=demo_html)


# Serve the ESIP default-UI static assets (interaction contract + default
# outfit). esip-contract.js is always injected into the served map;
# esip-embed.{css,js} only when ?ui=default (or config.map.default_ui).
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="esip-static")

# Serve sample GeoTIFFs for demos
EXAMPLE_TIFF_DIR = Path(__file__).parent.parent.parent / "examples" / "data"
if EXAMPLE_TIFF_DIR.exists():
    app.mount("/example_tiff", StaticFiles(directory=str(EXAMPLE_TIFF_DIR)), name="example_tiff")

# Mount static files for built frontend assets (if they exist)
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="static")


@app.get("/health", tags=["diagnostics"])
async def health():
    return {"status": "UP"}


@app.get("/__diag", tags=["diagnostics"])
async def __diag():
    """Cross-container diagnostics self-report. See DiagPayload schema."""
    from .diagnostics import build_diag

    db_path = None
    try:
        if _config is not None:
            db_path = str(_config.storage.database_path)
    except Exception:
        pass

    websocket_count = None
    try:
        websocket_count = sum(len(v) for v in manager._connections.values())
    except Exception:
        pass

    service_specific = {
        "active_maps": len(manager._connections) if hasattr(manager, "_connections") else None,
        "active_websockets": websocket_count,
        "database_path": db_path,
    }

    env_key_names = [
        "MAPCONTROL_HOST",
        "MAPCONTROL_PORT",
        "MAPCONTROL_DB_PATH",
        "MAPCONTROL_FILE_DIR",
        "MAPCONTROL_CONFIG_PATH",
    ]

    return await build_diag(
        service="esip-mapcontrol",
        started_at=_SERVER_START_TIME,
        env_key_names=env_key_names,
        peer_urls={},
        external_urls={},
        dns_hosts=[],
        service_specific=service_specific,
    )


# ─── Demo OAuth consent screen (toy AS only) ─────────────────────────────────
# When the demo Authorization Server is enabled with the optional consent screen
# (MAPCONTROL_MCP_AS=1 + MAPCONTROL_MCP_AS_CONSENT=1), the provider's authorize()
# redirects the user-agent here instead of auto-approving. These routes drive the
# SAME in-memory provider instance FastMCP holds (auth_server.get_provider()), so
# the issued code flows straight back into the /token exchange. Registered before
# the catch-all MCP mount so they match first. Demo-only — see auth_server.py.
from .auth_server import as_enabled as _as_enabled, get_provider as _as_provider


@app.get("/as/consent")
async def as_consent_get(request: Request):
    """Render the static consent page for an in-flight authorization transaction."""
    if not _as_enabled():
        return HTMLResponse("Not found", status_code=404)
    txn = request.query_params.get("txn", "")
    return HTMLResponse(_as_provider().render_consent_page(txn))


@app.post("/as/consent")
async def as_consent_post(request: Request):
    """Handle consent approval: validate the (optional) password, mint the code,
    and 302 the user-agent back to the MCP client's redirect_uri."""
    if not _as_enabled():
        return HTMLResponse("Not found", status_code=404)
    from fastapi.responses import RedirectResponse

    form = await request.form()
    txn = str(form.get("txn", ""))
    password = str(form.get("password", ""))
    redirect_url = _as_provider().complete_consent(txn, password)
    if redirect_url is None:
        return HTMLResponse(
            "<!doctype html><html><body style='font-family:Inter,system-ui;"
            "max-width:32rem;margin:4rem auto'><h2>Authorization failed</h2>"
            "<p>Invalid password or expired request. Please restart the "
            "connection from your MCP client.</p></body></html>",
            status_code=401,
        )
    return RedirectResponse(url=redirect_url, status_code=302)


# ─── Standalone auth portal routes (MAPCONTROL_AUTH_MODE=standalone) ─────────
# Registered BEFORE the catch-all MCP mount so /, /login, /signup, /consent,
# /admin, /logout match first. The OAuth protocol endpoints (/authorize,
# /token, /register DCR, /.well-known/*) stay inside the mounted MCP app —
# the portal only owns the HUMAN surface. In local/integrated modes nothing
# is registered, so "/" falls through to the MCP app exactly as before
# (ADR-0001 dual-deployability).
if _portal_enabled():
    from .portal.routes import router as _portal_router

    app.include_router(_portal_router)


# ─── MCP mount (MUST be last) ────────────────────────────────────────────────
# Mount the MCP Streamable HTTP ASGI app at root so its internal route resolves
# at exactly POST/GET/DELETE /mcp (no redirect). This is registered LAST because
# a mount at "/" is a catch-all: every preceding REST route, WebSocket, /map,
# /health, /__diag, and static mount is matched first; only unmatched paths fall
# through to the MCP app (which serves /mcp and 404s everything else).
#
# NOTE: the OAuth Authorization-Server routes (/authorize, /token, /register,
# /.well-known/oauth-authorization-server) and the §5b Protected-Resource
# Metadata route live INSIDE this mounted MCP app (FastMCP's
# streamable_http_app() adds them when an auth_server_provider / token_verifier
# is configured), so they resolve here too.
app.mount("/", mcp_server.streamable_http_app())



def run():
    """Entry point for the CLI command."""
    config = load_config()
    uvicorn.run(
        "mapcontrol_server.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )


if __name__ == "__main__":
    run()
