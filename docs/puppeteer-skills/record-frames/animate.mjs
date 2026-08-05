#!/usr/bin/env node
// Reference skill: capture a numbered frame sequence during an orbit.
//
//   MAP_ID=<map_id> node animate.mjs
//
// Writes frame_0000.png ... into ./frames/. Encode afterwards, e.g.:
//   ffmpeg -framerate 24 -i frames/frame_%04d.png -vf scale=800:-1 orbit.gif

import { mkdir } from "node:fs/promises";
import { openMap, sleep } from "../lib/esip-map.mjs";

const MAP_ID = process.env.MAP_ID;
if (!MAP_ID) {
  console.error("Set MAP_ID=<map_id> (see docs/puppeteer-skills/README.md)");
  process.exit(1);
}

const MATTERHORN = [7.6586, 45.9763];
const FRAMES = 72; // one frame per 5° → a full 360° orbit

await mkdir("frames", { recursive: true });

const { page, close } = await openMap({ mapId: MAP_ID, headless: true, viewport: [1280, 720] });

// Set up the 3D scene (globe + terrain + sky), framed on the peak.
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
  map.setSky({ "sky-color": "#199EF3", "horizon-color": "#ffffff", "fog-color": "#ffffff" });
  map.jumpTo({ center, zoom: 12.5, pitch: 70, bearing: 0 });
}, MATTERHORN);

await sleep(2000); // let terrain tiles settle before the first frame

console.log(`capturing ${FRAMES} frames`);
for (let i = 0; i < FRAMES; i++) {
  const bearing = (i / FRAMES) * 360;
  await page.evaluate((b) => window.__esipInternals.map.setBearing(b), bearing);
  await sleep(60); // paint budget
  const name = `frames/frame_${String(i).padStart(4, "0")}.png`;
  await page.screenshot({ path: name });
  if (i % 12 === 0) console.log(`  ${i}/${FRAMES}`);
}

console.log("done — encode frames/ with ffmpeg (see SKILL.md)");
await close();
