"""Regression: headless screenshots self-navigate over loopback, not the public URL.

Guards the container-networking bug where `take_screenshot` built the URL that
Chromium navigates to from `_public_base_url()` (MAPCONTROL_PUBLIC_URL). When
the public URL is an external address or reverse-proxy origin, it does not
resolve back to this process from inside the container — e.g. a host that maps
`8080:8000` sets MAPCONTROL_PUBLIC_URL=...:8080, but inside the container the
service listens on 8000, so navigating to :8080 fails and every screenshot
errors out.

The fix: Chromium self-navigates via `_internal_base_url()` — loopback on the
server's *bound* port — while `_public_base_url()` stays in use for the URLs
returned to external clients. Both screenshot call sites (the MCP
`take_screenshot` tool here in mcp_tools.py and the Playwright fallback in
main.py) share `_internal_base_url()`.

This test sets an intentionally unreachable public URL, stubs the browser layer
so it records the URL it was asked to open (no real Chromium, no network), and
asserts the tool still succeeds by driving loopback.

Runnable two ways:
  * python tests/test_screenshot_loopback.py   (standalone; prints PASS/FAIL, exits 1 on failure)
  * pytest tests/test_screenshot_loopback.py   (test_screenshot_loopback_url is collected)
"""

from __future__ import annotations

import asyncio
import os
import tempfile

# A public URL that is NOT reachable from inside the container. Must be set
# before importing the app (config/env are read on demand, but this mirrors the
# other gates and keeps the intent obvious).
os.environ["MAPCONTROL_PUBLIC_URL"] = "http://unreachable.invalid:9999"

# Simulate the container: the image binds this port (Dockerfile ENV
# MAPCONTROL_PORT + --port), while the working-dir config.toml carries a
# different dev port (7777). This asserts the internal URL follows the *bound*
# port, not config.toml — the mismatch that would otherwise silently break
# screenshots in the container.
os.environ["MAPCONTROL_PORT"] = "8000"

_tmp = tempfile.mkdtemp(prefix="screenshot_loopback_")
os.environ.setdefault("MAPCONTROL_DB_PATH", os.path.join(_tmp, "test.db"))
os.environ.setdefault("MAPCONTROL_FILE_DIR", os.path.join(_tmp, "files"))

from mapcontrol_server import mcp_tools  # noqa: E402
from mapcontrol_server.config import load_config  # noqa: E402
from mapcontrol_server.services import screenshot_service  # noqa: E402
from mapcontrol_server.services.screenshot_service import ScreenshotResult  # noqa: E402

UNREACHABLE = "http://unreachable.invalid:9999"


async def _run() -> None:
    captured: dict[str, str] = {}

    async def fake_playwright(map_url, width=1280, height=720, wait_ms=2000):
        # Record the URL Chromium would open, then return a stub result. The
        # file need not exist — take_screenshot handles an unreadable PNG.
        captured["map_url"] = map_url
        return ScreenshotResult(
            screenshot_id="sid123", url="/api/files/sid123.png", filename="sid123.png"
        )

    async def fake_require_map(map_id):  # avoid needing a real map in the DB
        return None

    orig_pw = screenshot_service.take_screenshot_playwright
    orig_rm = mcp_tools._require_map
    screenshot_service.take_screenshot_playwright = fake_playwright  # type: ignore[assignment]
    mcp_tools._require_map = fake_require_map  # type: ignore[assignment]
    try:
        result = await mcp_tools.take_screenshot(
            map_id="map-123", user_session_id="sess-abc"
        )
    finally:
        screenshot_service.take_screenshot_playwright = orig_pw  # type: ignore[assignment]
        mcp_tools._require_map = orig_rm  # type: ignore[assignment]

    port = load_config().server.port
    nav = captured.get("map_url", "")

    # The tool completed (did not raise) despite the unreachable public URL.
    assert result is not None, "take_screenshot returned nothing"

    # The bound port (MAPCONTROL_PORT) wins over config.toml's dev port (7777).
    assert port == 8000, f"expected bound port 8000, got {port}"

    # Chromium was pointed at loopback:bound_port, NOT the public URL and NOT
    # config.toml's dev port.
    expected = f"http://127.0.0.1:{port}/map/map-123?user_session=sess-abc"
    assert nav == expected, f"navigation URL was {nav!r}, expected {expected!r}"
    assert "unreachable.invalid" not in nav, f"public host leaked into nav URL: {nav!r}"
    assert ":7777" not in nav, f"config.toml dev port leaked into nav URL: {nav!r}"

    # The public/internal split is intact: public links still use the public
    # base; self-navigation uses loopback. (Both screenshot call sites rely on
    # this invariant.)
    assert mcp_tools._public_base_url() == UNREACHABLE
    assert mcp_tools._internal_base_url() == f"http://127.0.0.1:{port}"

    print(f"screenshot loopback OK: navigated to {nav}")
    print("RESULT: ALL PASSED")


def test_screenshot_loopback_url() -> None:
    """pytest entry point."""
    asyncio.run(_run())


if __name__ == "__main__":
    import sys

    try:
        asyncio.run(_run())
    except AssertionError as e:
        print(f"RESULT: FAILED — {e}")
        sys.stdout.flush()
        raise SystemExit(1)
    sys.stdout.flush()  # os._exit skips buffer flush; force it under redirection
    os._exit(0)
