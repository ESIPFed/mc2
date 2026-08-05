#!/usr/bin/env node
// Reference skill: capture PNG stills at scripted keyframes.
//
//   MAP_ID=<map_id> node animate.mjs
//
// Writes one PNG per keyframe into the current directory.

import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const MAP_ID = process.env.MAP_ID;
if (!MAP_ID) {
  console.error("Set MAP_ID=<map_id> (see docs/puppeteer-skills/README.md)");
  process.exit(1);
}

const KEYFRAMES = [
  { name: "matterhorn-3d", center: [7.6586, 45.9763], zoom: 12.5, pitch: 70, bearing: 20 },
  { name: "zermatt-town", center: [7.7491, 46.0207], zoom: 14, pitch: 45, bearing: 0 },
  { name: "alps-wide", center: [8.0, 46.2], zoom: 8, pitch: 30, bearing: 0 },
];

const { page, close } = await openMap({ mapId: MAP_ID, headless: true, viewport: [1600, 900] });

for (const kf of KEYFRAMES) {
  console.log(`framing ${kf.name}`);
  await cameraMove(page, "flyTo", {
    center: kf.center,
    zoom: kf.zoom,
    pitch: kf.pitch,
    bearing: kf.bearing,
    essential: true,
  });
  await sleep(1500); // let tiles finish loading before the snap
  await page.screenshot({ path: `${kf.name}.png` });
  console.log(`saved ${kf.name}.png`);
}

await close();
