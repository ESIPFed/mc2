# Puppeteer animation skills (reference examples)

These are **reference skills** — illustrative, copy-and-adapt examples that show how to
drive the MapControl web map with [Puppeteer](https://pptr.dev) to produce animations
for different scenarios. They are documentation, not a shipped/tested package; treat each
`SKILL.md` as a recipe and each `animate.mjs` as a starting point.

Each skill drives a **live map page** the same way a browser user would: it navigates to a
map URL, waits for the map to be ready, then scripts camera moves. Nothing here reaches
into private server internals — animation goes through the in-page MapLibre map object the
page already publishes.

## What the page gives you

The served map page publishes two hooks the moment it is ready (see
[`server/mapcontrol_server/static/esip-contract.js`](../../server/mapcontrol_server/static/esip-contract.js)):

| Hook | What it is | Use it for |
|---|---|---|
| `window.__esipInternals.map` | the raw **MapLibre GL JS** `Map` instance | camera animation — `flyTo`, `easeTo`, `rotateTo`, `setBearing`, `setPitch` |
| `window.ESIPMap` | the **public command surface** | basemap, visibility, `zoomToAssets`, reading the asset registry |
| `esip:ready` event | fired once the contract is live | knowing when the hooks exist |

Because animation just calls MapLibre's own camera methods, everything MapLibre supports
is available — including the smooth van Wijk `flyTo` and 3D globe + terrain (the same
terrain/sky path fixed in the server shell).

## Prerequisites

```bash
npm install puppeteer
```

You also need a **map to point at**. Create one first (any of the usual ways) and grab its
`map_id`:

```bash
# Minimal: create a map over REST and read back the id
curl -s -X POST http://localhost:8080/api/maps | python3 -c "import sys,json; print(json.load(sys.stdin)['map_id'])"
```

or from the Python SDK:

```python
from mapcontrol import MapControl
session = MapControl("http://localhost:8080").create_map()
print(session.map_id)   # feed this to MAP_ID below
```

The map URL every skill opens is:

```
http://localhost:8080/map/<MAP_ID>?ui=none
```

`ui=none` serves the **naked canvas** (no picker, no draw tools) — the cleanest frame for a
recording. Drop it if you want the chrome. If `user_session` is omitted the page
auto-creates one, which is fine for a throwaway animation.

## Shared helper

All skills import [`lib/esip-map.mjs`](lib/esip-map.mjs), a tiny helper that launches a
browser, opens a map URL, and resolves once `window.__esipInternals.map` exists and the
style has loaded. Read it once; the per-skill scripts stay short.

## The skills

| Skill | Scenario |
|---|---|
| [`flyto-tour/`](flyto-tour/SKILL.md) | Ballistic **city-to-city tour** — smooth `flyTo` between waypoints |
| [`terrain-orbit/`](terrain-orbit/SKILL.md) | **3D globe orbit** around a peak (Matterhorn) with terrain + sky |
| [`keyframe-screenshots/`](keyframe-screenshots/SKILL.md) | Capture **PNG stills** at scripted keyframes |
| [`record-frames/`](record-frames/SKILL.md) | Capture a **frame sequence** during an animation (→ GIF/MP4) |

Each folder has a `SKILL.md` (when to use it + the recipe) and a runnable `animate.mjs`.
