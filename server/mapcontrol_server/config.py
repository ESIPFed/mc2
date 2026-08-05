"""Configuration loader. Reads from TOML file with environment variable overrides."""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from dataclasses import dataclass, field


logger = logging.getLogger("mapcontrol")

CONFIG_PATH_ENV = "MAPCONTROL_CONFIG_PATH"
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    # ASGI mount prefix for deployment behind a single-origin reverse proxy
    # that exposes this server under a sub-path (e.g. "/service/map"). Empty =
    # served at root. Dual-deployability invariant (EOGPT-Roadmap ADR-0001): the
    # DEFAULT is "" so local dev / docker-compose.local.yml / direct-port and
    # internal M2M access are byte-for-byte unchanged; the CLOUD env-file sets
    # MAPCONTROL_ROOT_PATH=/service/map. Fed to uvicorn's --root-path so
    # request.base_url is auto-prefixed and incoming prefixed/un-prefixed paths
    # both route; serve_map reads request.scope["root_path"] to prefix the
    # URLs that bypass base_url (WebSocket + /static asset tags).
    root_path: str = ""


@dataclass
class SessionConfig:
    ttl_seconds: int = 86400


@dataclass
class StorageConfig:
    file_dir: str = "./data/files"
    database_path: str = "./data/mapcontrol.db"


@dataclass
class ProviderConfig:
    """A basemap tile provider. Currently just carries the env var name for
    the provider's API key — extend here when a provider needs more (e.g.
    a username for thunderforest)."""
    api_key_env: str | None = None


@dataclass
class BasemapEntry:
    """A single selectable basemap. `provider` (optional) names a key in
    `MapConfig.providers`; if set, the entry's `url` may contain a
    `{api_key}` placeholder which is substituted at load time from the
    provider's env var. Entries whose provider has no resolvable key are
    silently dropped during load — keyless dev installs see only the
    keyless basemaps in the picker.

    `group` controls which section the basemap appears under in the
    picker dropdown ("satellite" | "navigation" | "data"). Unknown
    groups fall through to "navigation"."""
    label: str = ""
    kind: str = "raster"  # "raster" | "vector"
    url: str = ""
    attribution: str = ""
    tile_size: int = 256
    max_zoom: int = 22
    provider: str | None = None
    group: str = "navigation"


@dataclass
class MapDefaultsConfig:
    center_lon: float = 0.0
    center_lat: float = 0.0
    zoom: int = 2


@dataclass
class MapStyleConfig:
    fill_color: str = "#3388ff40"
    stroke_color: str = "#3388ff"
    stroke_width: int = 2


@dataclass
class GeoTIFFConfig:
    max_file_size_mb: int = 50
    tile_server_message: str = (
        "Tile server support for large rasters is coming soon — stay tuned!"
    )


def _default_basemaps() -> dict[str, BasemapEntry]:
    return {
        "osm": BasemapEntry(
            label="OpenStreetMap",
            kind="raster",
            url="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            attribution="&copy; OpenStreetMap contributors",
            tile_size=256,
            max_zoom=19,
            group="navigation",
        ),
        "satellite": BasemapEntry(
            label="Esri Satellite",
            kind="raster",
            url="https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            attribution="&copy; Esri",
            tile_size=256,
            max_zoom=19,
            group="satellite",
        ),
    }


@dataclass
class MapConfig:
    default_basemap: str = "osm"
    # Basemap used instead of default_basemap when a map's theme is dark and
    # no basemap was explicitly chosen. "" disables the coupling. Validated /
    # cleared in _resolve_basemaps if the key is unavailable.
    default_dark_basemap: str = ""
    default_terrain: str = "2d"  # "2d" or "3d" (globe with terrain tiles + sky)
    # Which UI the served map renders by default.
    #   "none"     = naked: a bare, chrome-less canvas that only emits
    #                interaction events (the canonical ESIP design).
    #   "controls" = native map controls (basemap picker + Geoman draw
    #                tools + attribution) but NO embed outfit — for
    #                embedders that bring their own management UI (EOGPT).
    #   "default"  = controls PLUS the built-in layer panel + hover cards
    #                (the reference "outfit").
    # A ?ui=none|controls|default query param on /map/{id} overrides this
    # per request. Naked is the default so the vision's "pure map, no
    # chrome" promise holds unless opted out of.
    default_ui: str = "none"  # "none" | "controls" | "default"

    providers: dict[str, ProviderConfig] = field(default_factory=dict)
    basemaps: dict[str, BasemapEntry] = field(default_factory=_default_basemaps)
    defaults: MapDefaultsConfig = field(default_factory=MapDefaultsConfig)
    style: MapStyleConfig = field(default_factory=MapStyleConfig)


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    map: MapConfig = field(default_factory=MapConfig)
    geotiff: GeoTIFFConfig = field(default_factory=GeoTIFFConfig)


def _merge_dict(dc_instance, data: dict):
    """Merge a dict into a dataclass instance, only setting known fields.

    Dataclass-typed fields recurse; dict-typed fields holding nested
    dataclasses (providers, basemaps) are rebuilt from the TOML payload so
    user-defined keys actually appear instead of being filtered out by the
    `hasattr` check used for regular fields."""
    for key, value in data.items():
        if not hasattr(dc_instance, key):
            continue
        current = getattr(dc_instance, key)
        if isinstance(value, dict) and hasattr(current, "__dataclass_fields__"):
            _merge_dict(current, value)
        elif isinstance(value, dict) and key == "providers":
            setattr(dc_instance, key, {
                name: ProviderConfig(**entry) for name, entry in value.items()
            })
        elif isinstance(value, dict) and key == "basemaps":
            setattr(dc_instance, key, {
                name: BasemapEntry(**entry) for name, entry in value.items()
            })
        else:
            setattr(dc_instance, key, value)


def _resolve_basemaps(map_cfg: MapConfig) -> None:
    """Substitute provider API keys into basemap URLs and drop entries
    whose provider has no key configured. Mutates `map_cfg` in place."""
    resolved: dict[str, BasemapEntry] = {}
    for name, entry in map_cfg.basemaps.items():
        if entry.provider is None:
            resolved[name] = entry
            continue
        provider = map_cfg.providers.get(entry.provider)
        if provider is None:
            logger.warning(
                "Basemap %r references unknown provider %r — dropping",
                name, entry.provider,
            )
            continue
        api_key = os.environ.get(provider.api_key_env or "", "")
        if not api_key:
            logger.info(
                "Basemap %r needs %s — env var unset, skipping",
                name, provider.api_key_env,
            )
            continue
        resolved[name] = BasemapEntry(
            label=entry.label,
            kind=entry.kind,
            url=entry.url.replace("{api_key}", api_key),
            attribution=entry.attribution,
            tile_size=entry.tile_size,
            max_zoom=entry.max_zoom,
            provider=entry.provider,
            group=entry.group,
        )
    map_cfg.basemaps = resolved

    if map_cfg.default_basemap not in resolved:
        fallback = next(iter(resolved), "osm")
        logger.warning(
            "Default basemap %r is unavailable — falling back to %r",
            map_cfg.default_basemap, fallback,
        )
        map_cfg.default_basemap = fallback

    # The dark-theme default is optional; clear it if it points at a basemap
    # that didn't survive resolution so consumers can trust the value.
    if map_cfg.default_dark_basemap and map_cfg.default_dark_basemap not in resolved:
        logger.warning(
            "Dark-theme default basemap %r is unavailable — disabling the "
            "theme→basemap coupling", map_cfg.default_dark_basemap,
        )
        map_cfg.default_dark_basemap = ""


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from TOML file with env var overrides."""
    if config_path is None:
        config_path = os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH
        # When the package is pip-installed (site-packages), the __file__-based
        # default points nowhere — fall back to config.toml in the working
        # directory. This matches the container layout (WORKDIR /app/server
        # holds the repo's config.toml) and makes behavior identical whether
        # the package is imported from the source tree (uvicorn CLI adds CWD
        # to sys.path → source import) or from site-packages (test gates run
        # `python tests/...` → installed import). Source-tree resolution is
        # untouched since its default path exists.
        if not Path(config_path).exists():
            cwd_candidate = Path.cwd() / "config.toml"
            if cwd_candidate.exists():
                config_path = cwd_candidate

    config = AppConfig()
    path = Path(config_path)

    if path.exists():
        with open(path, "rb") as f:
            data = tomllib.load(f)
        _merge_dict(config, data)

    # Environment variable overrides
    if port := os.environ.get("MAPCONTROL_PORT"):
        config.server.port = int(port)
    if host := os.environ.get("MAPCONTROL_HOST"):
        config.server.host = host
    if db_path := os.environ.get("MAPCONTROL_DB_PATH"):
        config.storage.database_path = db_path
    if file_dir := os.environ.get("MAPCONTROL_FILE_DIR"):
        config.storage.file_dir = file_dir
    # Reverse-proxy mount prefix. Normalized to a leading-slash, no-trailing-slash
    # form ("/service/map") to match what uvicorn/ASGI expect; "" stays "" (root).
    if (root_path := os.environ.get("MAPCONTROL_ROOT_PATH")) is not None:
        rp = root_path.strip().rstrip("/")
        if rp and not rp.startswith("/"):
            rp = "/" + rp
        config.server.root_path = rp

    _resolve_basemaps(config.map)

    return config

