---
name: flyto-tour
description: Animate a smooth ballistic camera tour across a list of geographic waypoints on a MapControl map using Puppeteer and MapLibre's flyTo. Use when you want a cinematic city-to-city or site-to-site flythrough.
---

# Skill: Ballistic flyTo tour

Fly the camera between a sequence of waypoints with MapLibre's `flyTo` — the smooth
van Wijk zoom-out-then-in arc, so long hops don't tear through tiles.

## When to use

- A "world tour" or multi-site flythrough for a demo, header, or explainer.
- Any time you have an ordered list of `[lon, lat, zoom]` stops to visit.

## Recipe

1. Open the map with the shared helper and wait until it's ready.
2. For each waypoint, call `flyTo` and `await` `moveend` before the next hop.
3. Tune `speed`/`curve` for how aggressive the arc is; add a short hold at each stop.

The waypoints below are illustrative — swap in your own. See
[`animate.mjs`](animate.mjs) for the runnable version.

```js
import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const STOPS = [
  { name: "New York",  center: [-74.0060, 40.7128], zoom: 12 },
  { name: "London",    center: [-0.1276,  51.5074], zoom: 12 },
  { name: "Tokyo",     center: [139.6917, 35.6895], zoom: 12 },
  { name: "Sydney",    center: [151.2093, -33.8688], zoom: 12 },
];

const { page, close } = await openMap({ mapId: process.env.MAP_ID });

for (const stop of STOPS) {
  console.log(`→ ${stop.name}`);
  await cameraMove(page, "flyTo", {
    center: stop.center,
    zoom: stop.zoom,
    speed: 0.8,     // lower = slower, more cinematic
    curve: 1.42,    // arc "zoom-out" amount
    essential: true,
  });
  await sleep(1200); // hold on the destination
}

await close();
```

## Knobs

- `speed` — animation pace (default ~1.2). Lower is slower/dramatic.
- `curve` — how far the camera zooms out mid-flight for long hops.
- Hold time — the `sleep()` between stops.
- Combine with `terrain-orbit` to arrive and then orbit a destination.
