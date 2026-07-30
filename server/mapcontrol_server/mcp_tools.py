"""MCP (Model Context Protocol) layer for the Map Control server.

Phase 0 of the MCP compliance roadmap (see ``docs/vision/mcp-compliance-roadmap.md``):
expose the existing FastAPI proxy's capabilities over MCP **in-process**, mounted
at ``/mcp`` (Streamable HTTP) on the same FastAPI app. Each tool is a thin wrapper
over the existing service functions (``event_service`` / ``session_service`` /
``asset_service``) — the SAME code paths the REST API and Python SDK already use.
There is no separate state and no second process.

Design invariants (enforced here, see roadmap §"Invariants"):

* **No mega-tool.** Each tool has a typed, bounded schema.
* **Schema source of truth = Pydantic models.** Style input uses ``AssetStyle``.
* **Advertised == implemented.** ``tools`` + ``resources`` (see
  ``mcp_resources.py``) are advertised; prompts/experimental stay suppressed.
* **Explicit ``map_id``.** Identity travels as a tool argument (stateless-core
  ready), not via implicit session binding.
* **EPSG:4326 only across the MCP boundary.** All GeoJSON in/out is lon/lat 4326.

The consolidated tool set (12 tools) follows ``docs/vision/mcp-tool-consolidation.md``:
``create_map``, ``add_geojson``, ``add_geotiff``, ``zoom``, ``list_assets``,
``delete_asset``, ``set_visibility``, ``update_style``, ``set_basemap``,
``set_theme``, ``get_viewport``, ``take_screenshot``.
"""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import (
    CallToolResult,
    ImageContent,
    ResourceLink,
    TextContent,
    ToolAnnotations,
)
from pydantic import AnyUrl

from .config import load_config
from .models import AssetStyle, MapEvent
from .services import asset_service, event_service, screenshot_service, session_service


# ─── Server instance ─────────────────────────────────────────────────────────
#
# stateless_http=True keeps us aligned with the 2026-07-28 RC's stateless core
# (roadmap §7) — each request is self-contained and carries map_id explicitly.
#
# Phase 0 disables DNS-rebinding (Origin/Host) protection so the endpoint is as
# permissive as the existing REST surface (which runs CORS allow_origins=["*"]).
# Phase 1 (edge hardening) re-enables it with an explicit allowlist via
# MAPCONTROL_MCP_ALLOWED_ORIGINS / MAPCONTROL_MCP_ALLOWED_HOSTS.

def _csv_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


_allowed_origins = _csv_env("MAPCONTROL_MCP_ALLOWED_ORIGINS")
_allowed_hosts = _csv_env("MAPCONTROL_MCP_ALLOWED_HOSTS")
# Protection is OFF by default in Phase 0; turn it ON by setting
# MAPCONTROL_MCP_DNS_REBINDING_PROTECTION=1 (Phase 1).
_dns_protection = os.environ.get("MAPCONTROL_MCP_DNS_REBINDING_PROTECTION", "0") == "1"

_transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=_dns_protection,
    allowed_origins=_allowed_origins,
    allowed_hosts=_allowed_hosts,
)

# ─── §5b Authorization (Phase 2, docs/vision/mcp-compliance-roadmap.md) ───────────
# OFF by default (ADR-0001 dual-deployability): build_auth() returns (None, None)
# unless MAPCONTROL_MCP_AUTH=1, so local / docker-compose.local.yml / Phase 0-1
# behavior is byte-for-byte unchanged. When enabled, FastMCP turns into an OAuth
# 2.1 Resource Server on /mcp: it auto-mounts the RFC 9728 Protected Resource
# Metadata, returns 401 + WWW-Authenticate on a missing/invalid bearer token, and
# 403 when the token lacks a required scope. Verifier = ESIP self-issued signed
# scoped tokens (see auth.py).
from .auth import build_auth
from .auth_server import build_auth_server
from .portal import portal_enabled

# Mutually-exclusive auth wirings (FastMCP forbids passing BOTH
# auth_server_provider and token_verifier). Selected by MAPCONTROL_AUTH_MODE
# (local | standalone | integrated) with the legacy boolean knobs as
# back-compat aliases — see portal/__init__.py for the full mode contract:
#
#   1. Standalone portal AS — MAPCONTROL_AUTH_MODE=standalone. The full OAuth
#      2.1 AS+RS gated behind the portal's login/approval flow (persistent
#      users, real consent). See portal/.
#   2. Demo OAuth Authorization Server (toy AS) — legacy MAPCONTROL_MCP_AS=1.
#      Auto-approve, in-memory, single-tenant. See auth_server.py.
#   3. §5b Resource Server (token_verifier) — MAPCONTROL_AUTH_MODE=integrated
#      or legacy MAPCONTROL_MCP_AUTH=1. ESIP only VALIDATES bearer tokens
#      minted elsewhere (pure-M2M / external-IdP topology). See auth.py.
#
# When nothing is on, all are None and FastMCP is built exactly as in
# Phase 0/1 (open /mcp) — ADR-0001 dual-deploy.
if portal_enabled():
    from .portal.provider import build_portal_auth_server

    _auth_server_provider, _auth_settings = build_portal_auth_server()
    _token_verifier = None
else:
    _auth_server_provider, _auth_settings = build_auth_server()
    if _auth_server_provider is not None:
        _token_verifier = None
    else:
        _token_verifier, _auth_settings = build_auth()

mcp_server = FastMCP(
    "ESIP Map Control",
    instructions=(
        "Control a live, web-based map: create maps, add GeoJSON vector assets "
        "and GeoTIFF raster overlays, zoom/frame the view, inspect and manage "
        "assets, switch basemaps, read the viewport, and capture screenshots. "
        "All geographic coordinates are EPSG:4326 (lon, lat). Call create_map "
        "first to obtain a map_id and a browser URL, then pass that map_id to "
        "every other tool."
    ),
    stateless_http=True,
    transport_security=_transport_security,
    auth_server_provider=_auth_server_provider,
    token_verifier=_token_verifier,
    auth=_auth_settings,
)


# ─── Advertise == implemented (Phase 1 edge hardening; §6 Resources live) ────
#
# FastMCP registers prompts/resources handlers unconditionally, so its
# `initialize` response advertises `prompts`, `resources`, and (an empty)
# `experimental` capability regardless of what is actually implemented. Since
# 2026-07 the Resources layer IS implemented (mcp_resources.py — the map://
# taxonomy), so `resources` is now advertised for real (subscribe=False /
# listChanged=False; subscriptions are still deferred per the roadmap).
# `prompts` and `experimental` remain unimplemented and are stripped so the
# "advertised capabilities == implemented capabilities" invariant holds. When
# Prompts land (§5d) this wrapper narrows again — the advertisement grows in
# lockstep with the implementation.
_orig_create_init_options = mcp_server._mcp_server.create_initialization_options


def _create_initialization_options_implemented_only(*args, **kwargs):
    opts = _orig_create_init_options(*args, **kwargs)
    caps = opts.capabilities
    # Only suppress capabilities that have no real handler yet. `tools` and
    # `resources` (implemented) are left untouched.
    caps.prompts = None
    caps.experimental = None
    return opts


mcp_server._mcp_server.create_initialization_options = (
    _create_initialization_options_implemented_only
)



# ─── Helpers ─────────────────────────────────────────────────────────────────

def _public_base_url() -> str:
    """Base URL used to build returned map/screenshot URLs.

    Defaults to localhost for dev; set MAPCONTROL_PUBLIC_URL on the deployment
    (e.g. http://18.116.107.200:8000) so create_map returns a reachable link.
    """
    return os.environ.get("MAPCONTROL_PUBLIC_URL", "http://localhost:8000").rstrip("/")


# ─── map:// URI helpers (single source for the resource scheme) ──────────────
# Canonical URIs for the Resources layer (mcp_resources.py imports these; they
# live here so mcp_resources -> mcp_tools stays a one-way import).

def map_uri(map_id: str) -> str:
    return f"map://{map_id}"


def asset_uri(map_id: str, asset_id: str) -> str:
    return f"map://{map_id}/assets/{asset_id}"


def raster_uri(map_id: str, asset_id: str) -> str:
    return f"map://{map_id}/rasters/{asset_id}"


def screenshot_uri(map_id: str, screenshot_id: str) -> str:
    return f"map://{map_id}/screenshots/{screenshot_id}"


def _resource_link(uri: str, *, name: str, mime_type: str, description: str) -> ResourceLink:
    """A ResourceLink content block pointing at a map:// resource."""
    return ResourceLink(
        type="resource_link",
        uri=AnyUrl(uri),
        name=name,
        mimeType=mime_type,
        description=description,
    )


def _tool_result(structured: dict[str, Any], *extra_content: Any) -> CallToolResult:
    """Build a CallToolResult carrying BOTH the legacy JSON payload (as text +
    structuredContent, so nothing that parses today's output breaks) AND the
    extra content blocks (ResourceLinks / inline images) that make the result
    MCP-native."""
    content: list[Any] = list(extra_content)
    content.append(TextContent(type="text", text=json.dumps(structured, indent=2)))
    return CallToolResult(content=content, structuredContent=structured)


# Inline-image guardrail for tool results: screenshots smaller than this are
# embedded as base64 ImageContent (Claude web renders them in-chat) as-is.
# Larger captures are NOT dropped to link-only anymore — a dense satellite
# view over a city easily exceeds this as PNG — instead they're re-encoded
# to JPEG (and progressively downscaled if needed) until the inline copy
# fits, so a screenshot ALWAYS renders in-chat. The full-resolution PNG
# stays available behind the map:// resource link + URL.
_MAX_INLINE_IMAGE_BYTES = 1_500_000
_INLINE_JPEG_QUALITY = 80
# Downscale ladder applied when even the JPEG is too big (very large custom
# width/height captures): each step scales the previous size by the factor.
_INLINE_DOWNSCALE_STEPS = (1.0, 0.75, 0.5, 0.35)


def _inline_image_content(png_bytes: bytes) -> tuple[ImageContent | None, str | None]:
    """Best inline representation of a screenshot under the size guardrail.

    Returns (content, note). Small PNGs are inlined verbatim (lossless).
    Oversized ones are re-encoded to JPEG (quality 80) and, if still too big,
    progressively downscaled — a 1.7 MB satellite PNG becomes a ~300 KB JPEG
    that renders identically in chat. Returns (None, note) only if every
    attempt failed (e.g. Pillow missing), in which case the caller keeps the
    resource link + URL as the fallback.
    """
    if len(png_bytes) <= _MAX_INLINE_IMAGE_BYTES:
        return ImageContent(
            type="image",
            data=base64.b64encode(png_bytes).decode("ascii"),
            mimeType="image/png",
        ), None

    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        for factor in _INLINE_DOWNSCALE_STEPS:
            if factor < 1.0:
                w, h = img.size
                resized = img.resize(
                    (max(1, int(w * factor)), max(1, int(h * factor))),
                    Image.LANCZOS,
                )
            else:
                resized = img
            buf = io.BytesIO()
            resized.save(
                buf, format="JPEG", quality=_INLINE_JPEG_QUALITY, optimize=True
            )
            jpeg = buf.getvalue()
            if len(jpeg) <= _MAX_INLINE_IMAGE_BYTES:
                note = (
                    f"inline preview is JPEG q{_INLINE_JPEG_QUALITY}"
                    + (f" downscaled to {factor:.0%}" if factor < 1.0 else "")
                    + f" ({len(jpeg)} bytes; full-res PNG is {len(png_bytes)}"
                    " bytes — read the resource_uri or fetch full_url)"
                )
                return ImageContent(
                    type="image",
                    data=base64.b64encode(jpeg).decode("ascii"),
                    mimeType="image/jpeg",
                ), note
    except Exception as exc:  # pragma: no cover — never break the tool result
        return None, f"inline preview unavailable ({exc}); fetch full_url."
    return None, (
        f"inline preview omitted: screenshot too large even after JPEG "
        f"re-encode (> {_MAX_INLINE_IMAGE_BYTES} bytes); fetch full_url."
    )


def _parse_geojson(geojson: str) -> dict[str, Any]:
    """Parse + minimally validate a GeoJSON string.

    Raises ValueError on malformed input. FastMCP converts the raised exception
    into a Tool Execution Error (isError=true) so the model can self-correct —
    this is the correct channel for input-validation failures (report SEP-1303).
    """
    try:
        obj = json.loads(geojson)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            f"Invalid GeoJSON: not parseable as JSON ({exc}). "
            "Provide a GeoJSON Feature, FeatureCollection, or geometry as a "
            "JSON string with EPSG:4326 (lon, lat) coordinates."
        ) from exc
    if not isinstance(obj, dict) or "type" not in obj:
        raise ValueError(
            "Invalid GeoJSON: expected an object with a 'type' field "
            "(Feature, FeatureCollection, or a geometry type)."
        )
    return obj


def _dominant_geometry_kind(geojson_obj: dict[str, Any]) -> str:
    """Pick the canonical add_* event type from a GeoJSON object's geometry.

    The server renders all geometry types via the same path, but choosing a
    matching event type keeps the asset_type label meaningful. Polygon wins over
    line wins over point when a FeatureCollection mixes types.
    """
    kinds: set[str] = set()

    def visit(geom: dict[str, Any] | None) -> None:
        if not geom:
            return
        t = geom.get("type")
        if t in ("Polygon", "MultiPolygon"):
            kinds.add("polygon")
        elif t in ("LineString", "MultiLineString"):
            kinds.add("path")
        elif t in ("Point", "MultiPoint"):
            kinds.add("point")
        elif t == "GeometryCollection":
            for g in geom.get("geometries", []):
                visit(g)

    t = geojson_obj.get("type")
    if t == "FeatureCollection":
        for feat in geojson_obj.get("features", []):
            visit(feat.get("geometry"))
    elif t == "Feature":
        visit(geojson_obj.get("geometry"))
    else:
        visit(geojson_obj)

    if "polygon" in kinds:
        return "polygon"
    if "path" in kinds:
        return "path"
    return "point"


async def _require_map(map_id: str) -> None:
    """Raise a corrective ValueError if the map does not exist."""
    info = await session_service.get_map(map_id)
    if info is None:
        raise ValueError(
            f"Map '{map_id}' not found. Call create_map first to obtain a valid "
            "map_id."
        )


def _current_user() -> str:
    """The authenticated principal for the in-flight MCP request.

    In standalone-portal mode every bearer token was minted for a signed-in
    portal user; the provider records that attribution, so we can resolve the
    token in the MCP auth context back to a username. In all other modes (or
    when attribution is unknown) the principal is "anonymous" — local no-auth
    behavior is unchanged (ADR-0001), it just shares one prefs bucket.
    """
    try:
        from mcp.server.auth.middleware.auth_context import get_access_token

        token = get_access_token()
        if token is not None:
            if portal_enabled():
                from .portal.provider import get_portal_provider

                subject = get_portal_provider().subject_of(token.token)
                if subject:
                    return subject
            # Fall back to the token's client_id (§5b / toy-AS modes) so
            # prefs are at least keyed per OAuth client.
            if getattr(token, "client_id", None):
                return f"client:{token.client_id}"
    except Exception:  # pragma: no cover — never let identity break a tool
        pass
    return "anonymous"


# ─── Tools ───────────────────────────────────────────────────────────────────

@mcp_server.tool(
    annotations=ToolAnnotations(title="Create Map", readOnlyHint=False),
)
async def create_map(
    theme: Literal["light", "dark", "auto"] | None = None,
    basemap: str | None = None,
) -> CallToolResult:
    """Create a new live map workspace.

    Returns the map_id (pass it to every other tool), a browser URL the user
    can open to watch the map update in real time, and a resource link to the
    map's canonical map:// resource.

    Customizations are remembered PER USER: theme and basemap choices made by
    the authenticated user (here, or later via set_theme / set_basemap) are
    stored as their preferences and automatically applied to their future maps
    — you do not need to re-specify them every time. Passing an explicit value
    both uses it and updates the user's saved preference.

    Args:
        theme: UI theme for the served map page. 'dark' or 'light' pins the
            look (dark also switches to a dark basemap unless one is chosen);
            'auto' follows the viewer's OS/browser color-scheme preference.
            Omit to use the user's saved preference (default 'auto').
        basemap: Starting basemap key (e.g. 'satellite', 'osm', 'carto_dark').
            Omit to use the user's saved preference or the theme-appropriate
            server default.
    """
    user = _current_user()
    prefs = await session_service.get_user_prefs(user)

    # Explicit args win and become the user's new sticky preference;
    # otherwise fall back to the stored preference.
    if theme is None:
        theme = prefs.get("theme") or "auto"
    if basemap is None:
        basemap = prefs.get("basemap")  # may stay None → theme-aware default
    await session_service.update_user_prefs(user, theme=theme, basemap=basemap)

    result = await session_service.create_map(
        _public_base_url(), theme=theme, basemap=basemap, owner=user
    )
    structured = {
        "map_id": result.map_id,
        "url": result.url,
        "created_at": result.created_at,
        "theme": result.theme,
        **({"basemap": basemap} if basemap else {}),
        "owner": user,
        "resource_uri": map_uri(result.map_id),
    }
    return _tool_result(
        structured,
        _resource_link(
            map_uri(result.map_id),
            name=f"map-{result.map_id}",
            mime_type="application/json",
            description="Map info resource; child resources: /viewport, /assets, /drawn.",
        ),
    )


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Add GeoJSON", readOnlyHint=False, idempotentHint=False
    ),
)
async def add_geojson(
    map_id: str,
    geojson: str,
    name: str | None = None,
    style: AssetStyle | None = None,
) -> CallToolResult:
    """Add a vector asset (polygon, line/path, or point) to the map from GeoJSON.

    Replaces the separate add_polygon/add_path/add_point tools — the geometry
    type is detected from the GeoJSON. Coordinates must be EPSG:4326 (lon, lat).

    Args:
        map_id: The target map (from create_map).
        geojson: A GeoJSON Feature, FeatureCollection, or geometry as a JSON string.
        name: Optional human-friendly label for the asset.
        style: Optional style (fill_color, stroke_color, stroke_width, line_dash).
            Labels: set style.label=True to render the asset name as map text
            (or a string for custom text); label_placement is 'point',
            'center', or 'perimeter' (text along the polygon outline / line);
            label_color/label_size tune the text. Gradients: style.color_by =
            {"property": <feature prop>, "ramp": "viridis|magma|plasma|blues|reds|greens",
            "domain": [min, max]} colors features by a numeric property.
    """
    await _require_map(map_id)
    obj = _parse_geojson(geojson)
    kind = _dominant_geometry_kind(obj)
    event_type = f"add_{kind}"

    data: dict[str, Any] = {"geojson": geojson}
    if name:
        data["name"] = name
    if style is not None:
        data["style"] = style.model_dump(exclude_none=True)

    result = await event_service.process_event(map_id, MapEvent(type=event_type, data=data))
    if result.error:
        raise ValueError(result.error)
    structured = {
        "event_id": result.event_id,
        "asset_id": result.asset_id,
        "asset_type": kind,
        "created_at": result.created_at,
        "resource_uri": asset_uri(map_id, result.asset_id) if result.asset_id else None,
    }
    extra = []
    if result.asset_id:
        extra.append(_resource_link(
            asset_uri(map_id, result.asset_id),
            name=name or f"{kind}-{result.asset_id}",
            mime_type="application/geo+json",
            description=f"The created {kind} asset's GeoJSON (EPSG:4326).",
        ))
    return _tool_result(structured, *extra)


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Add Arc", readOnlyHint=False, idempotentHint=False
    ),
)
async def add_arc(
    map_id: str,
    from_point: list[float],
    to_point: list[float],
    from_name: str | None = None,
    to_name: str | None = None,
    name: str | None = None,
    style: AssetStyle | None = None,
    curvature: float = 0.25,
) -> CallToolResult:
    """Draw a visibly bowed arc between two locations, with endpoint dots.

    Use for comparisons/connections between two places (e.g. "compare these
    two sites"). The server tessellates a curved LineString and adds circle
    markers at both endpoints. When from_name/to_name are given the endpoints
    are labeled automatically.

    Args:
        map_id: The target map (from create_map).
        from_point: Start [lon, lat] (EPSG:4326).
        to_point: End [lon, lat] (EPSG:4326).
        from_name: Optional label for the start point.
        to_name: Optional label for the end point.
        name: Optional display name for the arc asset.
        style: Optional style (stroke_color/width for the arc, fill_color for
            the endpoint dots, line_dash for a dashed arc, label_* for labels).
        curvature: How much the arc bows sideways as a fraction of the
            distance (default 0.25). Negative bows the other way; 0 gives the
            true great-circle geodesic (nearly straight on short routes).
    """
    await _require_map(map_id)
    data: dict[str, Any] = {"from": from_point, "to": to_point, "curvature": curvature}
    if from_name:
        data["from_name"] = from_name
    if to_name:
        data["to_name"] = to_name
    if name:
        data["name"] = name
    if style is not None:
        data["style"] = style.model_dump(exclude_none=True)

    result = await event_service.process_event(map_id, MapEvent(type="add_arc", data=data))
    if result.error:
        raise ValueError(result.error)
    structured = {
        "event_id": result.event_id,
        "asset_id": result.asset_id,
        "asset_type": "arc",
        "created_at": result.created_at,
        "resource_uri": asset_uri(map_id, result.asset_id) if result.asset_id else None,
    }
    extra = []
    if result.asset_id:
        extra.append(_resource_link(
            asset_uri(map_id, result.asset_id),
            name=name or f"arc-{result.asset_id}",
            mime_type="application/geo+json",
            description="The created arc asset's GeoJSON (EPSG:4326).",
        ))
    return _tool_result(structured, *extra)


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Add GeoTIFF",
        readOnlyHint=False,
        idempotentHint=False,
        openWorldHint=True,  # fetches a raster from an external URL
    ),
)
async def add_geotiff(
    map_id: str,
    url: str,
    mode: Literal["rgb", "singleband"] = "singleband",
    name: str | None = None,
    bands: list[int] | None = None,
    band: int = 1,
    colormap: str = "viridis",
    alpha: float = 1.0,
    vmin: float | None = None,
    vmax: float | None = None,
    nodata: float | None = None,
) -> CallToolResult:
    """Overlay a GeoTIFF raster on the map from a URL.

    Replaces add_geotiff_rgb / add_geotiff_singleband. Use mode='rgb' for a
    3-band composite (set `bands`, e.g. [1,2,3]) or mode='singleband' for a
    single band with a matplotlib `colormap` (viridis, magma, plasma, gray, ...).

    Args:
        map_id: The target map (from create_map).
        url: Public URL (or absolute server path) to the GeoTIFF.
        mode: 'rgb' or 'singleband'.
        name: Optional label.
        bands: RGB band indices (mode='rgb'); defaults to [1, 2, 3].
        band: Band index (mode='singleband').
        colormap: Matplotlib colormap name (mode='singleband').
        alpha: Overlay opacity 0..1.
        vmin/vmax: Optional value range for the colormap (mode='singleband').
        nodata: Optional nodata value to treat as transparent.
    """
    await _require_map(map_id)

    if mode == "rgb":
        event_type = "add_geotiff_rgb"
        data: dict[str, Any] = {"url": url, "bands": bands or [1, 2, 3], "alpha": alpha}
    else:
        event_type = "add_geotiff_singleband"
        data = {"url": url, "band": band, "colormap": colormap, "alpha": alpha}
        if vmin is not None:
            data["vmin"] = vmin
        if vmax is not None:
            data["vmax"] = vmax
    if name:
        data["name"] = name
    if nodata is not None:
        data["nodata"] = nodata

    result = await event_service.process_event(map_id, MapEvent(type=event_type, data=data))
    if result.error:
        raise ValueError(result.error)
    structured = {
        "event_id": result.event_id,
        "asset_id": result.asset_id,
        "mode": mode,
        "created_at": result.created_at,
        "resource_uri": asset_uri(map_id, result.asset_id) if result.asset_id else None,
        "raster_uri": raster_uri(map_id, result.asset_id) if result.asset_id else None,
    }
    extra = []
    if result.asset_id:
        # Link-only for rasters (no inline preview) — rendered overlays can be
        # large; the client reads map://.../rasters/{id} on demand.
        extra.append(_resource_link(
            raster_uri(map_id, result.asset_id),
            name=name or f"raster-{result.asset_id}",
            mime_type="image/png",
            description="Rendered PNG overlay of the GeoTIFF (read on demand).",
        ))
        extra.append(_resource_link(
            asset_uri(map_id, result.asset_id),
            name=f"{name or 'geotiff'}-footprint",
            mime_type="application/geo+json",
            description="Geographic footprint polygon of the raster (EPSG:4326).",
        ))
    return _tool_result(structured, *extra)


@mcp_server.tool(
    annotations=ToolAnnotations(title="Zoom", readOnlyHint=False, idempotentHint=True),
)
async def zoom(
    map_id: str,
    target: Literal["point", "assets", "bbox", "out"],
    lon: float | None = None,
    lat: float | None = None,
    zoom: int = 14,
    asset_ids: list[str] | None = None,
    bbox: list[float] | None = None,
    levels: int = 3,
) -> dict[str, Any]:
    """Move/frame the map view. Discriminated by `target`:

    - target='point': fly to (lon, lat) at `zoom`.
    - target='assets': fit the given `asset_ids` in view.
    - target='bbox': fit a bounding box `bbox` = [minLon, minLat, maxLon, maxLat].
    - target='out': zoom out by `levels`.

    All coordinates are EPSG:4326 (lon, lat).
    """
    await _require_map(map_id)

    if target == "point":
        if lon is None or lat is None:
            raise ValueError("zoom target='point' requires both lon and lat.")
        event_type, data = "zoom_to_point", {"lon": lon, "lat": lat, "zoom": zoom}
    elif target == "assets":
        if not asset_ids:
            raise ValueError("zoom target='assets' requires a non-empty asset_ids list.")
        event_type, data = "zoom_to_assets", {"asset_ids": asset_ids}
    elif target == "bbox":
        if not bbox or len(bbox) != 4:
            raise ValueError(
                "zoom target='bbox' requires bbox=[minLon, minLat, maxLon, maxLat]."
            )
        event_type, data = "zoom_to_bbox", {"bbox": bbox}
    else:  # out
        event_type, data = "zoom_out", {"levels": levels}

    result = await event_service.process_event(map_id, MapEvent(type=event_type, data=data))
    if result.error:
        raise ValueError(result.error)
    return {"event_id": result.event_id, "target": target, "created_at": result.created_at}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="List Assets", readOnlyHint=True, idempotentHint=True
    ),
)
async def list_assets(map_id: str) -> dict[str, Any]:
    """List all assets currently on the map (id, name, type, visibility).

    Read-only. GeoJSON is returned in EPSG:4326.
    """
    await _require_map(map_id)
    assets = await asset_service.list_assets(map_id)
    return {
        "map_id": map_id,
        "count": len(assets),
        "assets": [
            {
                "asset_id": a.asset_id,
                "name": a.name,
                "asset_type": a.asset_type,
                "visible": a.visible,
                "geojson": a.geojson,
                # Canonical map:// resource URIs (Resources layer, §6): read
                # the asset's GeoJSON / rendered raster directly via
                # resources/read instead of re-listing.
                "resource_uri": asset_uri(map_id, a.asset_id),
                **(
                    {"raster_uri": raster_uri(map_id, a.asset_id)}
                    if a.asset_type.startswith("geotiff") else {}
                ),
            }
            for a in assets
        ],
    }


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Delete Asset", readOnlyHint=False, destructiveHint=True, idempotentHint=True
    ),
)
async def delete_asset(map_id: str, asset_id: str) -> dict[str, Any]:
    """Remove an asset from the map by its asset_id (destructive)."""
    await _require_map(map_id)
    result = await event_service.process_event(
        map_id, MapEvent(type="delete_asset", data={"asset_id": asset_id})
    )
    if result.error:
        raise ValueError(result.error)
    return {"event_id": result.event_id, "asset_id": asset_id, "deleted": True}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Set Visibility", readOnlyHint=False, idempotentHint=True
    ),
)
async def set_visibility(map_id: str, asset_id: str, visible: bool = True) -> dict[str, Any]:
    """Show or hide an asset without deleting it (idempotent)."""
    await _require_map(map_id)
    result = await event_service.process_event(
        map_id,
        MapEvent(type="set_visibility", data={"asset_id": asset_id, "visible": visible}),
    )
    if result.error:
        raise ValueError(result.error)
    return {"event_id": result.event_id, "asset_id": asset_id, "visible": visible}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Update Style", readOnlyHint=False, idempotentHint=True
    ),
)
async def update_style(map_id: str, asset_id: str, style: AssetStyle) -> dict[str, Any]:
    """Change an asset's style (fill_color, stroke_color, stroke_width, line_dash).

    Also controls labels: style.label=True renders the asset name as map text
    (a string gives custom text; False removes the label); label_placement is
    'point' | 'center' | 'perimeter' (text along the polygon outline / line);
    label_color / label_size tune the text appearance.
    """
    await _require_map(map_id)
    result = await event_service.process_event(
        map_id,
        MapEvent(
            type="update_style",
            data={"asset_id": asset_id, "style": style.model_dump(exclude_none=True)},
        ),
    )
    if result.error:
        raise ValueError(result.error)
    return {"event_id": result.event_id, "asset_id": asset_id}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Set Basemap", readOnlyHint=False, idempotentHint=True
    ),
)
async def set_basemap(map_id: str, basemap: str) -> dict[str, Any]:
    """Switch the basemap (e.g. 'osm', 'satellite', 'carto_dark', or any
    configured key).

    The choice is pinned to the map (survives reloads) and saved as the
    authenticated user's preferred basemap for future create_map calls.
    """
    await _require_map(map_id)
    result = await event_service.process_event(
        map_id, MapEvent(type="set_basemap", data={"basemap": basemap})
    )
    if result.error:
        raise ValueError(result.error)
    await session_service.update_user_prefs(_current_user(), basemap=basemap)
    return {"event_id": result.event_id, "basemap": basemap}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Set Theme", readOnlyHint=False, idempotentHint=True
    ),
)
async def set_theme(
    map_id: str, theme: Literal["light", "dark", "auto"]
) -> dict[str, Any]:
    """Set the map's UI theme (light/dark mode) — takes effect live on all
    connected viewers, no reload needed.

    'dark' and 'light' pin the appearance; 'auto' follows each viewer's
    OS/browser color-scheme preference (prefers-color-scheme). Dark maps also
    default to a dark basemap on next load unless a basemap was explicitly
    chosen. The theme is map-level state (persists, applies to every viewer)
    AND is saved as the authenticated user's preference for future maps.
    """
    await _require_map(map_id)
    result = await event_service.process_event(
        map_id, MapEvent(type="set_theme", data={"theme": theme})
    )
    if result.error:
        raise ValueError(result.error)
    await session_service.update_user_prefs(_current_user(), theme=theme)
    return {"event_id": result.event_id, "theme": theme}


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Get Viewport", readOnlyHint=True, idempotentHint=True
    ),
)
async def get_viewport(map_id: str, user_session_id: str | None = None) -> dict[str, Any]:
    """Read the current map viewport (center, zoom, bbox) and basemap.

    Read-only. If user_session_id is omitted, returns the map-level snapshot
    (viewport computed from asset bounds when no session viewport is stored).
    Coordinates are EPSG:4326.
    """
    await _require_map(map_id)
    snapshot = await session_service.get_session_snapshot(map_id, user_session_id or "")
    vp = snapshot.viewport.model_dump(exclude_none=True) if snapshot.viewport else None
    return {
        "map_id": map_id,
        "viewport": vp,
        "basemap": snapshot.basemap,
        "terrain": snapshot.terrain,
        "asset_count": len(snapshot.assets),
    }


@mcp_server.tool(
    annotations=ToolAnnotations(
        title="Take Screenshot", readOnlyHint=True, idempotentHint=False
    ),
)
async def take_screenshot(
    map_id: str,
    user_session_id: str | None = None,
    width: int = 1280,
    height: int = 720,
) -> CallToolResult:
    """Capture a PNG screenshot of the map as currently rendered.

    Uses headless Chromium server-side (no open browser required). Returns the
    PNG inline (rendered directly in MCP clients like Claude), a resource link
    to its canonical map:// URI, and the legacy URL fields. Read-only with
    respect to map state.
    """
    await _require_map(map_id)
    base = _public_base_url()
    map_url = f"{base}/map/{map_id}"
    if user_session_id:
        map_url += f"?user_session={user_session_id}"

    result = await screenshot_service.take_screenshot_playwright(
        map_url, width=width, height=height
    )

    uri = screenshot_uri(map_id, result.screenshot_id)
    structured = {
        "screenshot_id": result.screenshot_id,
        "url": result.url,
        "full_url": f"{base}{result.url}",
        "filename": result.filename,
        "resource_uri": uri,
    }

    extra: list[Any] = []
    # Inline an image (base64 ImageContent) so clients render it in-chat.
    # Small PNGs go verbatim; oversized ones are re-encoded to JPEG (and
    # downscaled if needed) so a dense satellite capture still renders inline
    # instead of degrading to a bare URL. Full-res PNG stays behind the
    # resource link + full_url either way.
    png_path = Path(load_config().storage.file_dir) / result.filename
    try:
        png_bytes = png_path.read_bytes()
    except OSError:
        png_bytes = b""
    if png_bytes:
        inline, note = _inline_image_content(png_bytes)
        if inline is not None:
            extra.append(inline)
        if note:
            structured["inline_image"] = note
    else:
        structured["inline_image"] = "omitted: screenshot file unreadable."
    extra.append(_resource_link(
        uri,
        name=result.filename,
        mime_type="image/png",
        description=f"Map screenshot ({width}x{height} PNG).",
    ))
    return _tool_result(structured, *extra)
