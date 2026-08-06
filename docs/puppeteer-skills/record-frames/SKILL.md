---
name: record-frames
description: Capture a numbered sequence of PNG frames while a MapControl map animates, so the frames can be assembled into a GIF or MP4 with ffmpeg. Use to produce a shareable animated clip (e.g. a header animation) from a Puppeteer-driven camera move.
---

# Skill: Record a frame sequence

Capture frames on a fixed cadence while the camera animates, then hand the sequence to
`ffmpeg` to make a GIF or MP4. This is the scenario for producing an actual animated clip
(a docs header, a social preview) rather than stills.

## When to use

- You need a looping GIF/MP4 of a camera move, not a live page.
- You want deterministic frames (grab N frames, one every M ms) you can re-encode.

## How it works

Rather than screen-record, this steps the animation in small time slices and calls
`page.screenshot()` for each — giving evenly spaced, artifact-free frames. It pairs well
with `terrain-orbit` (record an orbit) or `flyto-tour` (record a flythrough).

## Recipe

See [`animate.mjs`](animate.mjs). Sketch:

```js
import { openMap, sleep } from "../lib/esip-map.mjs";

const { page, close } = await openMap({ mapId: process.env.MAP_ID });

// Kick off a non-blocking orbit inside the page, then sample frames from Node.
await page.evaluate(() => {
  const map = window.__esipInternals.map;
  map.setProjection({ type: "globe" });
  map.jumpTo({ center: [7.6586, 45.9763], zoom: 12.5, pitch: 70 });
});

const FRAMES = 72;           // 72 frames * 30ms bearing step ≈ one full turn
for (let i = 0; i < FRAMES; i++) {
  await page.evaluate((b) => window.__esipInternals.map.setBearing(b), (i / FRAMES) * 360);
  await sleep(60);           // let the frame paint
  await page.screenshot({ path: `frame_${String(i).padStart(4, "0")}.png` });
}

await close();
```

Then encode:

```bash
# GIF
ffmpeg -framerate 24 -i frame_%04d.png -vf "scale=800:-1:flags=lanczos" orbit.gif
# MP4
ffmpeg -framerate 24 -i frame_%04d.png -c:v libx264 -pix_fmt yuv420p orbit.mp4
```

## Knobs

- `FRAMES` × bearing step — total rotation and smoothness.
- `sleep()` per frame — paint budget; raise it if frames look half-drawn.
- `-framerate` on encode — playback speed, independent of capture cadence.
- Swap the in-page move for a `flyTo` path to record a flythrough instead of an orbit.
