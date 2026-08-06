---
name: keyframe-screenshots
description: Move a MapControl map camera to a set of scripted keyframes and capture a PNG still at each one using Puppeteer. Use to generate documentation stills or thumbnails, or to visually verify the map renders a given view.
---

# Skill: Keyframe screenshots

Drive the camera to named keyframes and snapshot each. This is the scenario for producing
docs imagery, README thumbnails, or a quick visual regression check (e.g. confirming the 3D
view renders with a clean console after the sky fix).

## When to use

- You want a handful of PNG stills of specific views, not a full animation.
- You want to assert "this view renders" in CI without a running human.

## Recipe

Capture with Puppeteer's own `page.screenshot()` (browser-side, no server round-trip). See
[`animate.mjs`](animate.mjs).

```js
import { openMap, cameraMove, sleep } from "../lib/esip-map.mjs";

const KEYFRAMES = [
  { name: "matterhorn-3d", center: [7.6586, 45.9763], zoom: 12.5, pitch: 70 },
  { name: "zermatt-town",  center: [7.7491, 46.0207], zoom: 14,   pitch: 45 },
];

const { page, close } = await openMap({ mapId: process.env.MAP_ID });

for (const kf of KEYFRAMES) {
  await cameraMove(page, "flyTo", { ...kf, essential: true });
  await sleep(1500);                       // let tiles finish
  await page.screenshot({ path: `${kf.name}.png` });
  console.log(`saved ${kf.name}.png`);
}

await close();
```

## Notes

- `page.screenshot()` grabs exactly what the viewport shows — set the viewport in `openMap`
  to control output resolution.
- The server also has its own screenshot endpoint
  (`POST /api/maps/{map_id}/sessions/{user_session_id}/screenshot`) if you'd rather capture
  server-side; this skill stays fully client-side so it needs no session id.
- To turn keyframes into a visual check, compare each PNG against a committed baseline.
