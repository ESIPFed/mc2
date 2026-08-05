---
name: terrain-orbit
description: Fly to a mountain or landmark, enable 3D globe terrain and sky, then slowly orbit the camera around it using Puppeteer and MapLibre. Use for a dramatic 3D hero shot (e.g. the Matterhorn) for a docs header or demo.
---

# Skill: 3D terrain orbit

Frame a peak in 3D — globe projection, terrain exaggeration, atmospheric sky — then rotate
the camera bearing around it for a slow orbit. This is the "hero shot" scenario.

## When to use

- A dramatic 3D flythrough of dramatic relief (the Matterhorn is the canonical subject).
- Any landmark that reads best tilted and rotating rather than flat.

## How it works

The map page already supports 3D via the server shell's terrain path (globe projection +
`terrain-dem` source + `setSky`). This skill turns that on through the raw MapLibre map,
tilts the camera (`pitch`), then steps the `bearing` in a loop with `easeTo` to orbit.

> Terrain here is driven directly on the MapLibre map for a self-contained example. If your
> deployment prefers to flip terrain through the server (so the mode is part of session
> state), send the `set_terrain` event instead and just do the pitch/bearing orbit here.

## Recipe

See [`animate.mjs`](animate.mjs). The core:

```js
import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const MATTERHORN = [7.6586, 45.9763]; // lon, lat

const { page, close } = await openMap({ mapId: process.env.MAP_ID });

// Enable globe + terrain + sky, then frame the peak tilted.
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

await sleep(1500);

// Orbit: step the bearing a full turn.
for (let bearing = 0; bearing <= 360; bearing += 30) {
  await cameraMove(page, "easeTo", { bearing, duration: 1000, essential: true });
}

await close();
```

## Knobs

- `exaggeration` — terrain height multiplier (1.5 is punchy; 1.0 is true-scale).
- `pitch` — camera tilt (0 = top-down, ~70 = dramatic).
- Orbit step / `duration` — smaller steps + shorter durations = smoother spin.
- `zoom` — how tightly you frame the peak.
- Swap `MATTERHORN` for any `[lon, lat]`.
