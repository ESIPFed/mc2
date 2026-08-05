// Shared helper for the Puppeteer animation skills.
//
// Launches a browser, opens a MapControl map page, and resolves once the map
// is genuinely ready to animate: the page's `window.__esipInternals.map`
// (the raw MapLibre GL JS instance) exists AND its style has loaded.
//
// This is reference/example code — adapt freely.

import puppeteer from "puppeteer";

const DEFAULT_SERVER = process.env.MAPCONTROL_SERVER || "http://localhost:8000";

/**
 * Open a map and wait until it is ready to animate.
 *
 * @param {object} opts
 * @param {string} opts.mapId        - the map_id to open (required)
 * @param {string} [opts.server]     - server base URL
 * @param {boolean} [opts.uiNone]    - serve the naked canvas (default true)
 * @param {boolean} [opts.headless]  - run headless (default true)
 * @param {[number, number]} [opts.viewport] - [width, height], default 1280x720
 * @returns {Promise<{browser, page, close}>}
 */
export async function openMap({
  mapId,
  server = DEFAULT_SERVER,
  uiNone = true,
  headless = true,
  viewport = [1280, 720],
} = {}) {
  if (!mapId) throw new Error("openMap: mapId is required");

  const browser = await puppeteer.launch({
    headless,
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
  });
  const page = await browser.newPage();
  await page.setViewport({ width: viewport[0], height: viewport[1] });

  const url = `${server}/map/${mapId}${uiNone ? "?ui=none" : ""}`;
  await page.goto(url, { waitUntil: "networkidle2" });

  // Wait for the map object to be published and its style to finish loading.
  await page.waitForFunction(
    () => {
      const m = window.__esipInternals && window.__esipInternals.map;
      return !!m && m.isStyleLoaded();
    },
    { timeout: 30000 },
  );

  return {
    browser,
    page,
    close: () => browser.close(),
  };
}

/**
 * Run a MapLibre camera call and resolve when the camera comes to rest.
 * `method` is any camera method name ('flyTo' | 'easeTo' | 'jumpTo' | ...).
 *
 * Resolves on the map's 'moveend' event so callers can `await` a move instead
 * of guessing a sleep duration.
 */
export async function cameraMove(page, method, options) {
  await page.evaluate(
    (method, options) =>
      new Promise((resolve) => {
        const map = window.__esipInternals.map;
        map.once("moveend", () => resolve());
        map[method](options);
      }),
    method,
    options,
  );
}

/** Small await-able sleep for pacing between moves. */
export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
