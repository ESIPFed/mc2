"""SQLite database setup and query helpers using aiosqlite."""

from __future__ import annotations

import aiosqlite
from pathlib import Path

from .config import AppConfig

# Module-level connection reference
_db: aiosqlite.Connection | None = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS maps (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    config TEXT
);

CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    last_active TEXT NOT NULL,
    viewport TEXT,
    basemap TEXT DEFAULT 'osm',
    terrain TEXT DEFAULT '2d',
    theme TEXT DEFAULT 'auto'
);

CREATE TABLE IF NOT EXISTS assets (
    id TEXT PRIMARY KEY,
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    name TEXT,
    asset_type TEXT NOT NULL,
    geojson TEXT NOT NULL,
    style TEXT,
    metadata TEXT,
    visible INTEGER DEFAULT 1,
    animated INTEGER DEFAULT 0,
    source_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    map_id TEXT NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    user_session_id TEXT REFERENCES user_sessions(id),
    event_type TEXT NOT NULL,
    event_data TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_prefs (
    username TEXT PRIMARY KEY,
    theme TEXT,
    basemap TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_assets_map ON assets(map_id);
CREATE INDEX IF NOT EXISTS idx_events_map ON events(map_id);
CREATE INDEX IF NOT EXISTS idx_sessions_map ON user_sessions(map_id);
"""


async def init_db(config: AppConfig) -> aiosqlite.Connection:
    """Initialize the database, creating tables if needed."""
    global _db

    db_path = Path(config.storage.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA foreign_keys=ON")
    await _db.executescript(SCHEMA)
    # Lightweight in-place migration: CREATE TABLE IF NOT EXISTS does not add
    # new columns to pre-existing databases, so bring old user_sessions tables
    # up to date. ADD COLUMN is a no-op error if it already exists.
    try:
        await _db.execute(
            "ALTER TABLE user_sessions ADD COLUMN theme TEXT DEFAULT 'auto'"
        )
    except aiosqlite.OperationalError:
        pass  # column already present
    # maps.owner: the authenticated principal (portal username in standalone
    # mode; "anonymous" when auth is off) that created the map via MCP.
    try:
        await _db.execute("ALTER TABLE maps ADD COLUMN owner TEXT")
    except aiosqlite.OperationalError:
        pass  # column already present
    await _db.commit()

    return _db


async def get_db() -> aiosqlite.Connection:
    """Get the current database connection."""
    if _db is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    return _db


async def close_db():
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
