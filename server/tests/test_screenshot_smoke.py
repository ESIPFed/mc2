"""Screenshot smoke gate — asserts headless Chromium actually launches in the image.

Regression guard. The Docker image once hand-listed Chromium's system
libraries in the Dockerfile and the list was missing libXfixes.so.3, so the
bundled chrome-headless-shell crashed at launch ("error while loading shared
libraries") and every `take_screenshot` MCP tool call failed in production —
while all other CI gates stayed green, because none of them ever started the
browser. The Dockerfile now uses `playwright install --with-deps chromium`
(Playwright installs its own dependency set), and this gate proves the shipped
artifact can really render a page to PNG.

Run standalone inside the built image (same pattern as the other gates):

    docker run --rm --workdir /app/server IMAGE python tests/test_screenshot_smoke.py

No network access is required: the test drives the real production code path
(`screenshot_service.take_screenshot_playwright`) against a `data:` URL, so it
exercises browser launch, page navigation, PNG capture, and file persistence
without depending on external tile servers or CDNs.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

# Self-contained page — no network fetches, renders instantly.
SMOKE_PAGE = (
    "data:text/html,"
    "<body style='margin:0;background:%23224466'>"
    "<h1 style='color:%23fff;font:32px sans-serif;padding:2em'>screenshot smoke</h1>"
    "</body>"
)


async def main() -> None:
    from mapcontrol_server.config import load_config
    from mapcontrol_server.services.screenshot_service import (
        take_screenshot_playwright,
    )

    # Drive the REAL service function (browser launch args, file naming,
    # storage dir) — not a bespoke Playwright invocation.
    result = await take_screenshot_playwright(
        SMOKE_PAGE, width=640, height=480, wait_ms=100
    )

    config = load_config()
    file_path = Path(config.storage.file_dir) / result.filename
    assert file_path.is_file(), f"screenshot file missing: {file_path}"

    data = file_path.read_bytes()
    assert data.startswith(PNG_MAGIC), f"not a PNG: {data[:16]!r}"
    assert len(data) > 1000, f"suspiciously small PNG ({len(data)} bytes)"

    print(f"screenshot OK: {result.filename} ({len(data)} bytes)")
    print("RESULT: ALL PASSED")


if __name__ == "__main__":
    asyncio.run(main())
    # Same convention as the other standalone gates: exit immediately so no
    # lingering non-daemon thread can hang interpreter shutdown in CI.
    os._exit(0)
