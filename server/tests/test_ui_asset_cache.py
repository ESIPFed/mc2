"""Map shells must not mix cached UI code from different deployments."""

import hashlib
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient

from mapcontrol_server import main


@pytest.mark.parametrize("root_path", ["", "/services/maps"])
@pytest.mark.parametrize("query,filenames", [
    ("ui=default", {"esip-contract.js", "esip-embed.js", "esip-embed.css", "esip-inspector.js", "esip-inspector.css"}),
    ("ui=controls&inspect=1", {"esip-contract.js", "esip-inspector.js", "esip-inspector.css"}),
    ("ui=none", {"esip-contract.js"}),
    ("ui=default&inspect=0", {"esip-contract.js", "esip-embed.js", "esip-embed.css"}),
])
async def test_map_shell_versions_all_owned_assets(tmp_path, monkeypatch, root_path, query, filenames):
    monkeypatch.setenv("MAPCONTROL_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MAPCONTROL_FILE_DIR", str(tmp_path / "files"))
    # Only the database is needed for map HTML/static routes; do not start the
    # unrelated single-use MCP session manager for every parameter combination.
    config = main.load_config()
    monkeypatch.setattr(main, "_config", config)
    await main.init_db(config)
    try:
        transport = ASGITransport(app=main.app, root_path=root_path)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post("/api/maps")
            map_id = created.json()["map_id"]
            response = await client.get(f"/map/{map_id}?{query}")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            urls = re.findall(r'(?:src|href)="([^"]*/static/esip-[^"]+)"', response.text)
            assert {urlsplit(url).path.rsplit("/", 1)[1] for url in urls} == filenames
            assert len(urls) == len(filenames)
            for url in urls:
                parsed = urlsplit(url)
                filename = parsed.path.rsplit("/", 1)[1]
                assert parsed.path == f"{root_path}/static/{filename}"
                expected = hashlib.sha256((main.STATIC_DIR / filename).read_bytes()).hexdigest()[:16]
                assert parse_qs(parsed.query) == {"v": [expected]}
                # The version is a cache key, not a different static route.
                asset = await client.get(url)
                assert asset.status_code == 200
                assert asset.content == (main.STATIC_DIR / filename).read_bytes()
    finally:
        await main.close_db()


def test_asset_version_changes_only_when_content_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "STATIC_DIR", tmp_path)
    source = tmp_path / "esip-contract.js"
    source.write_text("previous contract")
    before = main._ui_asset_url("/maps", source.name)
    assert main._ui_asset_url("/maps", source.name) == before
    source.write_text("current contract")
    assert main._ui_asset_url("/maps", source.name) != before
