#!/usr/bin/env node
// Reference skill: 3D globe terrain orbit around a peak (Matterhorn).
//
//   MAP_ID=<map_id> node animate.mjs
//
// Requires a running server and an existing map_id. See ../README.md.

import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const MAP_ID = process.env.MAP_ID;
if (!MAP_ID) {
  console.error("Set MAP_ID=<map_id> (see docs/puppeteer-skills/README.md)");
  process.exit(1);
}

const MATTERHORN = [7.6586, 45.9763]; // lon, lat

const { page, close } = await openMap({ mapId: MAP_ID, headless: true });

// Enable globe projection + terrain + atmospheric sky, framed on the peak.
console.log("enabling 3D terrain + sky");
await page.evaluate((center) => {
  const map = window.__esipInternals.map;
  map.setProjection({ type: "globe" });
  if (!map.getSource("terrain-dem")) {
    map.addSource("terrain-dem", {
      type: "raster-dem",
      url: "https://demotiles.maplibre.org/terrain-tiles/tiles.json",
      tileSize: 256,
    });
  }
  map.setTerrain({ source: "terrain-dem", exaggeration: 1.5 });
  // MapLibre uses setSky(), NOT a `type: 'sky'` layer (that is Mapbox's API).
  map.setSky({
    "sky-color": "#199EF3",
    "sky-horizon-blend": 0.5,
    "horizon-color": "#ffffff",
    "fog-color": "#ffffff",
    "fog-ground-blend": 0.5,
  });
  map.jumpTo({ center, zoom: 12.5, pitch: 70, bearing: 0 });
}, MATTERHORN);

// Let terrain tiles settle before spinning.
await sleep(2000);

console.log("orbiting");
for (let bearing = 30; bearing <= 360; bearing += 30) {
  await cameraMove(page, "easeTo", { bearing, duration: 1000, essential: true });
}

console.log("orbit complete");
await close();
