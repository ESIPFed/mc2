"""Session and map lifecycle management."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from ..database import get_db
from ..models import (
    AssetResponse,
    AssetStyle,
    AssetMetadata,
    MapCreateResponse,
    MapInfo,
    SessionCreateResponse,
    SessionSnapshot,
    Viewport,
)


VALID_THEMES = ("light", "dark", "auto")


def _normalize_theme(theme: str | None) -> str:
    """Clamp a theme value to light|dark|auto ('auto' on anything unknown)."""
    t = (theme or "auto").strip().lower()
    return t if t in VALID_THEMES else "auto"


async def create_map(
    base_url: str,
    theme: str = "auto",
    basemap: str | None = None,
    owner: str | None = None,
) -> MapCreateResponse:
    """Create a new map workspace.

    ``theme`` sets the map-level UI theme: ``light``, ``dark``, or ``auto``
    (default — the served page follows the viewer's OS/browser
    ``prefers-color-scheme``). Stored in the map's config JSON so it survives
    restarts and is changeable later via the ``set_theme`` event.

    ``basemap`` optionally pins the map-level starting basemap (a key in the
    server's configured basemaps). When None the served page falls back to the
    theme-appropriate server default.

    ``owner`` records the authenticated principal that created the map
    (portal username in standalone auth mode; None/"anonymous" otherwise).
    """
    db = await get_db()
    map_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    theme = _normalize_theme(theme)

    config: dict = {"theme": theme}
    if basemap:
        config["basemap"] = basemap

    await db.execute(
        "INSERT INTO maps (id, created_at, updated_at, config, owner)"
        " VALUES (?, ?, ?, ?, ?)",
        (map_id, now, now, json.dumps(config), owner),
    )
    await db.commit()

    url = f"{base_url}/map/{map_id}"
    return MapCreateResponse(map_id=map_id, url=url, created_at=now, theme=theme)


async def get_map_theme(map_id: str) -> str:
    """The map-level theme ('light' | 'dark' | 'auto'); 'auto' when unset."""
    db = await get_db()
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    if row is None or not row["config"]:
        return "auto"
    try:
        return _normalize_theme(json.loads(row["config"]).get("theme"))
    except (ValueError, TypeError):
        return "auto"


async def get_map_basemap(map_id: str) -> str | None:
    """The map-level basemap key pinned at create_map / set_basemap, if any."""
    db = await get_db()
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    if row is None or not row["config"]:
        return None
    try:
        return json.loads(row["config"]).get("basemap")
    except (ValueError, TypeError):
        return None


async def set_map_basemap(map_id: str, basemap: str) -> None:
    """Persist a map-level basemap so it survives reloads and new viewers."""
    db = await get_db()
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    config: dict = {}
    if row is not None and row["config"]:
        try:
            config = json.loads(row["config"])
        except (ValueError, TypeError):
            config = {}
    config["basemap"] = basemap
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE maps SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, map_id),
    )
    await db.commit()


# ─── Per-user sticky preferences ─────────────────────────────────────────────
# Customizations are keyed to the authenticated principal (portal username in
# standalone auth mode; "anonymous" when auth is off). create_map applies them
# as defaults when the caller doesn't pass explicit args; explicit choices win
# and are written back so they become the user's defaults going forward.

async def get_user_prefs(username: str) -> dict:
    """The user's sticky preferences: {'theme': ..., 'basemap': ...} (values may be None)."""
    db = await get_db()
    cursor = await db.execute(
        "SELECT theme, basemap FROM user_prefs WHERE username = ?", (username,)
    )
    row = await cursor.fetchone()
    if row is None:
        return {"theme": None, "basemap": None}
    return {"theme": row["theme"], "basemap": row["basemap"]}


async def update_user_prefs(
    username: str, theme: str | None = None, basemap: str | None = None
) -> None:
    """Upsert sticky preferences; only non-None fields are written."""
    if theme is None and basemap is None:
        return
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO user_prefs (username, theme, basemap, updated_at)"
        " VALUES (?, ?, ?, ?)"
        " ON CONFLICT(username) DO UPDATE SET"
        "   theme = COALESCE(excluded.theme, user_prefs.theme),"
        "   basemap = COALESCE(excluded.basemap, user_prefs.basemap),"
        "   updated_at = excluded.updated_at",
        (username, theme, basemap, now),
    )
    await db.commit()


# ─── Map-level viewport (persisted camera intent) ────────────────────────────
# zoom_* events (MCP `zoom` tool / REST events) persist the requested view
# here, in the map's config JSON — the same materialization pattern as
# theme/basemap. Per-session viewports (user_sessions.viewport, phoned home by
# live browsers on moveend) remain the per-viewer truth; this map-level value
# is what makes camera intent survive for NEW viewers — most importantly the
# headless screenshot page, which previously always booted at the world
# default because it starts a fresh session.

async def set_map_viewport(map_id: str, viewport: Viewport) -> None:
    """Persist the map-level viewport (with a timestamp for newest-wins)."""
    db = await get_db()
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    config: dict = {}
    if row is not None and row["config"]:
        try:
            config = json.loads(row["config"])
        except (ValueError, TypeError):
            config = {}
    now = datetime.now(timezone.utc).isoformat()
    config["viewport"] = viewport.model_dump(exclude_none=True)
    config["viewport_updated_at"] = now
    await db.execute(
        "UPDATE maps SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, map_id),
    )
    await db.commit()


async def get_map_viewport(map_id: str) -> tuple[Viewport | None, str | None]:
    """The map-level viewport and its update timestamp, if one was persisted."""
    db = await get_db()
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    if row is None or not row["config"]:
        return None, None
    try:
        config = json.loads(row["config"])
        vp = config.get("viewport")
        if not vp:
            return None, None
        return Viewport(**vp), config.get("viewport_updated_at")
    except (ValueError, TypeError):
        return None, None


async def _latest_session_viewport(
    db, map_id: str
) -> tuple[Viewport | None, str | None]:
    """The most recently active session's viewport for this map (any session).

    This is the map's 'what a human last looked at' view — live browsers phone
    home their camera on every moveend, so any fresh/headless viewer can
    inherit it instead of starting at the world default.
    """
    cursor = await db.execute(
        "SELECT viewport, last_active FROM user_sessions"
        " WHERE map_id = ? AND viewport IS NOT NULL"
        " ORDER BY last_active DESC LIMIT 1",
        (map_id,),
    )
    row = await cursor.fetchone()
    if row is None or not row["viewport"]:
        return None, None
    try:
        return Viewport.model_validate_json(row["viewport"]), row["last_active"]
    except (ValueError, TypeError):
        return None, None


async def _shared_map_viewport(db, map_id: str) -> Viewport | None:
    """Best shared (non-session) viewport: map-level (zoom_* events) vs the
    latest live session's phone-home — whichever is newer wins. Both
    timestamps are UTC isoformat, so lexicographic comparison is correct."""
    map_vp, map_ts = await get_map_viewport(map_id)
    sess_vp, sess_ts = await _latest_session_viewport(db, map_id)
    if map_vp is not None and sess_vp is not None:
        return map_vp if (map_ts or "") >= (sess_ts or "") else sess_vp
    return map_vp or sess_vp


async def get_effective_viewport(
    map_id: str, user_session_id: str = ""
) -> Viewport | None:
    """The viewport a (possibly brand-new) viewer of this map should get.

    Fallback chain: the requesting session's own stored viewport → the shared
    map viewport (map-level zoom_* persistence vs latest-active session,
    newest wins) → the bounding box of the map's assets → None (config
    default). Used by serve_map to render the INITIAL camera server-side so
    the first tiles a headless screenshot page requests are already the right
    ones."""
    db = await get_db()
    viewport: Viewport | None = None
    if user_session_id:
        cursor = await db.execute(
            "SELECT viewport FROM user_sessions WHERE id = ? AND map_id = ?",
            (user_session_id, map_id),
        )
        row = await cursor.fetchone()
        if row and row["viewport"]:
            try:
                viewport = Viewport.model_validate_json(row["viewport"])
            except (ValueError, TypeError):
                viewport = None
    if viewport is None:
        viewport = await _shared_map_viewport(db, map_id)
    if viewport is None:
        cursor = await db.execute(
            "SELECT geojson FROM assets WHERE map_id = ?", (map_id,)
        )
        rows = await cursor.fetchall()
        bbox = _compute_bbox_from_geojsons([r["geojson"] for r in rows])
        if bbox:
            viewport = Viewport(bbox=bbox)
    return viewport


async def set_map_theme(map_id: str, theme: str) -> str:
    """Persist a new map-level theme; returns the normalized value."""
    db = await get_db()
    theme = _normalize_theme(theme)
    cursor = await db.execute("SELECT config FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    config: dict = {}
    if row is not None and row["config"]:
        try:
            config = json.loads(row["config"])
        except (ValueError, TypeError):
            config = {}
    config["theme"] = theme
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "UPDATE maps SET config = ?, updated_at = ? WHERE id = ?",
        (json.dumps(config), now, map_id),
    )
    await db.commit()
    return theme


async def get_map(map_id: str) -> MapInfo | None:
    """Get map info."""
    db = await get_db()
    cursor = await db.execute("SELECT * FROM maps WHERE id = ?", (map_id,))
    row = await cursor.fetchone()
    if row is None:
        return None

    # Count assets
    cursor2 = await db.execute(
        "SELECT COUNT(*) as cnt FROM assets WHERE map_id = ?", (map_id,)
    )
    count_row = await cursor2.fetchone()
    asset_count = count_row[0] if count_row else 0

    return MapInfo(
        map_id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        asset_count=asset_count,
    )


async def list_maps() -> list[MapInfo]:
    """List all maps with their asset counts (read-only; oldest first).

    Added for the MCP Resources layer (map://maps) — a pure reader over the
    same tables, mirroring get_map's shape.
    """
    db = await get_db()
    cursor = await db.execute(
        """SELECT m.id, m.created_at, m.updated_at,
                  (SELECT COUNT(*) FROM assets a WHERE a.map_id = m.id) AS cnt
           FROM maps m ORDER BY m.created_at ASC"""
    )
    rows = await cursor.fetchall()
    return [
        MapInfo(
            map_id=row["id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            asset_count=row["cnt"],
        )
        for row in rows
    ]


async def delete_map(map_id: str) -> bool:
    """Delete a map and all associated data."""
    db = await get_db()
    cursor = await db.execute("DELETE FROM maps WHERE id = ?", (map_id,))
    await db.commit()
    return cursor.rowcount > 0


async def create_session(map_id: str, base_url: str) -> SessionCreateResponse | None:
    """Create a user session for a map."""
    from ..config import load_config
    db = await get_db()

    # Verify map exists
    cursor = await db.execute("SELECT id FROM maps WHERE id = ?", (map_id,))
    if await cursor.fetchone() is None:
        return None

    session_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Set basemap/terrain from config so session_restore returns the right defaults
    cfg = load_config()
    await db.execute(
        "INSERT INTO user_sessions (id, map_id, created_at, last_active, basemap, terrain) VALUES (?, ?, ?, ?, ?, ?)",
        (session_id, map_id, now, now, cfg.map.default_basemap, cfg.map.default_terrain),
    )
    await db.commit()

    url = f"{base_url}/map/{map_id}?user_session={session_id}"
    return SessionCreateResponse(
        user_session_id=session_id, map_id=map_id, url=url, created_at=now
    )


async def update_viewport(
    map_id: str, user_session_id: str, viewport: Viewport,
    basemap: str | None = None, terrain: str | None = None,
):
    """Update the stored viewport for a user session."""
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    viewport_json = viewport.model_dump_json()

    # Build dynamic SET clause based on what's provided
    set_parts = ["viewport = ?", "last_active = ?"]
    params: list = [viewport_json, now]

    if basemap:
        set_parts.append("basemap = ?")
        params.append(basemap)
    if terrain:
        set_parts.append("terrain = ?")
        params.append(terrain)

    params.extend([user_session_id, map_id])
    sql = f"UPDATE user_sessions SET {', '.join(set_parts)} WHERE id = ? AND map_id = ?"
    await db.execute(sql, tuple(params))
    await db.commit()


async def get_session_snapshot(map_id: str, user_session_id: str) -> SessionSnapshot:
    """Build a full state snapshot for session restore."""
    db = await get_db()

    # Get all assets for this map
    cursor = await db.execute(
        "SELECT * FROM assets WHERE map_id = ? ORDER BY created_at ASC", (map_id,)
    )
    rows = await cursor.fetchall()

    assets = []
    for row in rows:
        style = None
        if row["style"]:
            style = AssetStyle.model_validate_json(row["style"])

        metadata = None
        if row["metadata"]:
            metadata = AssetMetadata.model_validate_json(row["metadata"])

        assets.append(
            AssetResponse(
                asset_id=row["id"],
                map_id=row["map_id"],
                name=row["name"],
                asset_type=row["asset_type"],
                geojson=row["geojson"],
                style=style,
                metadata=metadata,
                visible=bool(row["visible"]),
                animated=bool(row["animated"]),
                source_url=row["source_url"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        )

    # Get viewport for this session — use config defaults (not hardcoded "osm"/"2d")
    from ..config import load_config
    _cfg = load_config()
    viewport = None
    basemap = _cfg.map.default_basemap
    terrain = _cfg.map.default_terrain
    if user_session_id:
        cursor2 = await db.execute(
            "SELECT viewport, basemap, terrain FROM user_sessions WHERE id = ? AND map_id = ?",
            (user_session_id, map_id),
        )
        session_row = await cursor2.fetchone()
        if session_row and session_row["viewport"]:
            viewport = Viewport.model_validate_json(session_row["viewport"])
        if session_row and session_row["basemap"]:
            basemap = session_row["basemap"]
        if session_row and session_row["terrain"]:
            terrain = session_row["terrain"]

    # Theme is MAP-level state (set at create_map / via set_theme) so every
    # viewer of the map shares it; sessions don't override it in v1.
    theme = await get_map_theme(map_id)

    # No viewport stored for THIS session → inherit the shared map view
    # (map-level zoom_* persistence or the latest live session's phone-home,
    # newest wins). This is what lets a fresh headless screenshot session see
    # the same Budapest a human just flew to, instead of the default globe.
    if viewport is None:
        viewport = await _shared_map_viewport(db, map_id)

    # Still nothing but there are assets → compute a bounding box so the
    # frontend can zoom-to-fit.
    if viewport is None and assets:
        bbox = _compute_assets_bbox(assets)
        if bbox:
            viewport = Viewport(bbox=bbox)

    return SessionSnapshot(
        assets=assets, viewport=viewport, basemap=basemap, terrain=terrain, theme=theme
    )


def _compute_assets_bbox(assets: list[AssetResponse]) -> list[float] | None:
    """Compute a bounding box [minLon, minLat, maxLon, maxLat] from all assets' GeoJSON."""
    return _compute_bbox_from_geojsons([a.geojson for a in assets])


def _compute_bbox_from_geojsons(geojsons: list) -> list[float] | None:
    """Bounding box [minLon, minLat, maxLon, maxLat] from raw GeoJSON strings/dicts."""
    import json as _json

    min_lon = float("inf")
    min_lat = float("inf")
    max_lon = float("-inf")
    max_lat = float("-inf")
    found = False

    for geojson in geojsons:
        if isinstance(geojson, str):
            try:
                geojson = _json.loads(geojson)
            except (ValueError, TypeError):
                continue

        coords = _extract_coords(geojson)
        for lon, lat in coords:
            min_lon = min(min_lon, lon)
            min_lat = min(min_lat, lat)
            max_lon = max(max_lon, lon)
            max_lat = max(max_lat, lat)
            found = True

    if not found:
        return None
    return [min_lon, min_lat, max_lon, max_lat]


def _extract_coords(geojson: dict) -> list[tuple[float, float]]:
    """Recursively extract [lon, lat] coordinate pairs from any GeoJSON structure."""
    coords = []
    geom = geojson

    # If it's a Feature, unwrap to geometry
    if geojson.get("type") == "Feature":
        geom = geojson.get("geometry", {})
    elif geojson.get("type") == "FeatureCollection":
        for feature in geojson.get("features", []):
            coords.extend(_extract_coords(feature))
        return coords

    raw = geom.get("coordinates", [])
    coords.extend(_flatten_coords(raw))
    return coords


def _flatten_coords(item) -> list[tuple[float, float]]:
    """Flatten nested coordinate arrays into a list of (lon, lat) tuples."""
    if not item:
        return []
    # If it's a coordinate pair [lon, lat]
    if isinstance(item[0], (int, float)):
        return [(item[0], item[1])]
    # Otherwise recurse
    result = []
    for sub in item:
        result.extend(_flatten_coords(sub))
    return result
