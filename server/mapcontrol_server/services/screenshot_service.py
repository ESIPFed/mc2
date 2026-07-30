"""Screenshot service — captures map screenshots using Playwright (headless Chromium).

Primary method: Playwright opens the map URL in headless Chrome, waits for tiles
to load, captures the canvas as PNG. Works everywhere — local, Docker, cloud.

Secondary method (interactive): WebSocket canvas capture from a connected browser.
Used by the demo page's screenshot button for instant captures.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..config import load_config

logger = logging.getLogger(__name__)


@dataclass
class ScreenshotResult:
    """Result of a completed screenshot."""
    screenshot_id: str
    url: str
    filename: str


# ─── Primary: Playwright headless screenshot ─────────────────────────────────

# Deterministic capture wait: how long we're willing to wait for MapLibre to
# report loaded()+areTilesLoaded() before capturing anyway. Generous because
# satellite tiles over a fresh viewport can take a while on a cold box.
_TILES_LOADED_TIMEOUT_MS = 15_000
# Small settle delay after tiles report loaded so the final frame paints.
_PAINT_SETTLE_MS = 300


async def take_screenshot_playwright(
    map_url: str,
    width: int = 1280,
    height: int = 720,
    wait_ms: int = 2000,
) -> ScreenshotResult:
    """Take a screenshot using headless Chromium via Playwright.

    This is the primary screenshot method. It works everywhere — no browser
    needs to be open. The server navigates to the map URL, waits for tiles
    to load, and captures the canvas.

    The wait is DETERMINISTIC when the page is an ESIP map: it polls
    ``window.__esipInternals.map`` for ``loaded() && areTilesLoaded()`` (the
    same readiness signal the in-browser WS capture path uses), so the capture
    can't race the tile fetch anymore. Non-map pages (e.g. the CI smoke test's
    ``data:`` URL) simply fall back to the fixed ``wait_ms`` delay.

    Args:
        map_url: Full URL to the map page (with user_session param)
        width: Viewport width in pixels
        height: Viewport height in pixels
        wait_ms: Fallback milliseconds to wait after networkidle when the
            deterministic tiles-loaded signal is unavailable

    Returns:
        ScreenshotResult with the saved PNG URL.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "Playwright is not installed. Install with: "
            "pip install playwright && playwright install chromium"
        )

    screenshot_id = str(uuid.uuid4())
    config = load_config()
    file_dir = Path(config.storage.file_dir)
    file_dir.mkdir(parents=True, exist_ok=True)

    filename = f"screenshot-{screenshot_id}.png"
    file_path = file_dir / filename

    logger.info(f"Taking Playwright screenshot: {map_url} ({width}x{height})")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
        )
        try:
            page = await browser.new_page(
                viewport={"width": width, "height": height},
            )
            await page.goto(map_url, wait_until="networkidle")

            # Deterministic wait: MapLibre knows when it's actually done.
            # The served map publishes window.__esipInternals.map (set
            # synchronously in its inline script, so it exists by networkidle);
            # poll it for loaded() + areTilesLoaded(). networkidle alone is
            # NOT enough — the session_restore snapshot arrives over WebSocket
            # after networkidle and can trigger a whole new round of tile
            # loads. Non-map pages (CI smoke test's data: URL, arbitrary URLs)
            # have no __esipInternals → immediate fallback to the fixed delay.
            is_esip_map = False
            try:
                is_esip_map = bool(await page.evaluate(
                    "() => !!(window.__esipInternals && window.__esipInternals.map)"
                ))
            except Exception:
                is_esip_map = False

            if is_esip_map:
                try:
                    await page.wait_for_function(
                        """() => {
                            const m = window.__esipInternals && window.__esipInternals.map;
                            if (!m) return false;
                            try { return m.loaded() && m.areTilesLoaded(); }
                            catch (e) { return false; }
                        }""",
                        timeout=_TILES_LOADED_TIMEOUT_MS,
                    )
                    # Tiles are loaded; give the final frame a beat to paint.
                    await page.wait_for_timeout(_PAINT_SETTLE_MS)
                    logger.info("Screenshot wait: tiles loaded (deterministic)")
                except Exception:
                    # Tiles never settled within the budget — capture what we
                    # have after the legacy fixed delay.
                    logger.info(
                        "Screenshot wait: tiles-loaded signal timed out; "
                        f"falling back to fixed {wait_ms}ms wait"
                    )
                    await page.wait_for_timeout(wait_ms)
            else:
                await page.wait_for_timeout(wait_ms)

            # Take the screenshot
            png_bytes = await page.screenshot(type="png")
            file_path.write_bytes(png_bytes)

            logger.info(
                f"Playwright screenshot saved: {file_path} ({len(png_bytes)} bytes)"
            )
        finally:
            await browser.close()

    url = f"/api/files/{filename}"
    return ScreenshotResult(
        screenshot_id=screenshot_id,
        url=url,
        filename=filename,
    )


# ─── Secondary: WebSocket canvas capture (for interactive demo) ──────────────
# These functions support the browser-side canvas capture flow used by the
# demo page's "Take Screenshot" button.

_pending: dict[str, _PendingScreenshot] = {}


@dataclass
class _PendingScreenshot:
    """Internal tracker for a pending WebSocket screenshot request."""
    event: asyncio.Event
    result_path: str | None = None
    error: str | None = None


async def request_ws_screenshot() -> tuple[str, _PendingScreenshot]:
    """Create a pending WebSocket screenshot request.

    Returns (correlation_id, pending) — send the correlation_id to the
    browser via WebSocket, then await the pending event.
    """
    correlation_id = str(uuid.uuid4())
    pending = _PendingScreenshot(event=asyncio.Event())
    _pending[correlation_id] = pending
    return correlation_id, pending


async def resolve_ws_screenshot(correlation_id: str, base64_png: str) -> str | None:
    """Called when the browser sends back a screenshot via WebSocket."""
    pending = _pending.get(correlation_id)
    if not pending:
        logger.warning(f"Unknown screenshot correlation_id: {correlation_id}")
        return None

    try:
        config = load_config()
        file_dir = Path(config.storage.file_dir)
        file_dir.mkdir(parents=True, exist_ok=True)

        filename = f"screenshot-{correlation_id}.png"
        file_path = file_dir / filename

        data = base64_png
        if data.startswith("data:"):
            data = data.split(",", 1)[1]

        png_bytes = base64.b64decode(data)
        file_path.write_bytes(png_bytes)

        pending.result_path = str(file_path)
        logger.info(f"WS screenshot saved: {file_path} ({len(png_bytes)} bytes)")
    except Exception as e:
        pending.error = str(e)
        logger.error(f"Failed to save WS screenshot {correlation_id}: {e}")
    finally:
        pending.event.set()

    return pending.result_path


async def resolve_ws_screenshot_error(correlation_id: str, error: str):
    """Called when the browser reports a screenshot error."""
    pending = _pending.get(correlation_id)
    if pending:
        pending.error = error
        pending.event.set()


async def await_ws_screenshot(
    correlation_id: str, timeout: float = 15.0
) -> ScreenshotResult:
    """Wait for a WebSocket screenshot to complete."""
    pending = _pending.get(correlation_id)
    if not pending:
        raise RuntimeError(f"Unknown screenshot request: {correlation_id}")

    try:
        await asyncio.wait_for(pending.event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        raise TimeoutError(
            "Screenshot timed out — is a browser connected to this session?"
        )
    finally:
        _pending.pop(correlation_id, None)

    if pending.error:
        raise RuntimeError(f"Screenshot failed: {pending.error}")

    filename = f"screenshot-{correlation_id}.png"
    url = f"/api/files/{filename}"

    return ScreenshotResult(
        screenshot_id=correlation_id,
        url=url,
        filename=filename,
    )
