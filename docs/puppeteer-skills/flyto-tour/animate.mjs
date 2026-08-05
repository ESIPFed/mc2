#!/usr/bin/env node
// Reference skill: ballistic flyTo tour across waypoints.
//
//   MAP_ID=<map_id> node animate.mjs
//
// Requires a running server (default http://localhost:8000, override with
// MAPCONTROL_SERVER) and an existing map_id. See ../README.md.

import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const MAP_ID = process.env.MAP_ID;
if (!MAP_ID) {
  console.error("Set MAP_ID=<map_id> (see docs/puppeteer-skills/README.md)");
  process.exit(1);
}

// Waypoints — swap in your own [lon, lat, zoom] stops.
const STOPS = [
  { name: "New York City", center: [-74.006, 40.7128], zoom: 12 },
  { name: "London", center: [-0.1276, 51.5074], zoom: 12 },
  { name: "Tokyo", center: [139.6917, 35.6895], zoom: 12 },
  { name: "Sydney", center: [151.2093, -33.8688], zoom: 12 },
  { name: "Cape Town", center: [18.4241, -33.9249], zoom: 12 },
];

const { page, close } = await openMap({ mapId: MAP_ID, headless: true });

// Start planted on the first stop, then fly the rest.
await cameraMove(page, "jumpTo", { center: STOPS[0].center, zoom: STOPS[0].zoom });
console.log(`start: ${STOPS[0].name}`);
await sleep(800);

for (let i = 1; i < STOPS.length; i++) {
  const stop = STOPS[i];
  console.log(`fly → ${stop.name}`);
  await cameraMove(page, "flyTo", {
    center: stop.center,
    zoom: stop.zoom,
    speed: 0.8,
    curve: 1.42,
    essential: true,
  });
  await sleep(1200);
}

console.log("tour complete");
await close();
