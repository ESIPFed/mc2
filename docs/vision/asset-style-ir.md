# Asset + Style IR — a tabled architecture direction

**Status: TABLED / NOT SCHEDULED.** This document records an architecture
discussion (July 2026) so the ideas aren't lost. Nothing here is committed
work. The near-term glyph roadmap (labels, arcs, hover/gradients) proceeds
on the *current* architecture, with one soft constraint noted at the bottom.

---

## The observation that started this

Every new way of visualizing something on the map ("glyph") currently costs
updates in **five hand-maintained mirrors**:

1. Frontend renderer (`addGeoJSON()` in `main.py`'s embedded JS + `mcp-app.html`)
2. `event_service.py` + `models.py` (persistence / session restore / screenshots)
3. `mcp_tools.py` (MCP tool surface)
4. SDK (`mapcontrol/session.py`)
5. EOGPT chat handler (`handlers/map_control.py` TOOLS list, in the eo-gpt repo)

No new HTTP routes are ever needed — `POST /api/maps/{map_id}/events` is a
generic pipe — but the *event vocabulary* is mirrored five times. The
question was: is there a gestalt shift that makes glyphs cheap?

## Background: the taxonomy of map visualizations

The taxonomy of *rendering primitives* is finite; the taxonomy of *map
features* is infinite, because features are **compositions** of primitives.

MapLibre's closed primitive set: `fill`, `line`, `circle`, `symbol`
(text + icons), `heatmap`, `fill-extrusion`, `raster`, `hillshade`, `sky`.
deck.gl adds another closed set (`ArcLayer`, `TripsLayer`, custom WebGL
layers, ...). Everything ever seen on a web map — labeled cities, flow maps,
contour bands, choropleths — is data + a combination of those primitives +
styling expressions. Cartographers don't invent new primitives; they compose.

Consequence: our system doesn't need an open-ended verb list. It needs a
small set of primitives and a *composition mechanism*.

## Candidate shifts (in increasing order of radicalism)

### Shift 1 — Collapse the verbs: "everything is a Feature + Style"

There is really only one asset-creating operation:

```
add_asset(geojson, style, name)
```

The geometry type is already *inside* the GeoJSON — the frontend's
`detectGeomTypes()` proves it: `add_polygon` / `add_path` / `add_point` all
funnel into the same `addGeoJSON()` today. The verb split is a fiction
maintained for the LLM's benefit.

Under this shift the vocabulary becomes stable CRUD
(`add_asset / update_asset / delete_asset` + camera + map-level events) and
**new visualization capability = new field in the Style schema, zero new
verbs**. Labels, gradients, hover all become style/behavior properties:

```json
{
  "geojson": "...",
  "style": {
    "label":    { "text": "$name", "placement": "perimeter" },
    "color_by": { "property": "day", "ramp": "viridis" },
    "hoverable": true
  }
}
```

This is how Felt's API and kepler.gl configs work. Migration is cheap: keep
the old verbs as aliases that compile down to `add_asset`.

### Shift 2 — Single-source contract + codegen

Orthogonal to vocabulary: define the event/style schema **once**
(JSON Schema or pydantic) and *generate* the other mirrors — SDK methods,
LLM tool definitions, TypeScript types for the frontend. The seed already
exists in `static/esip-contract.js`. After this, adding a glyph is: edit
schema, implement one renderer function; the other four mirrors update
mechanically. Highest payoff-to-risk ratio of all the shifts.

### Shift 3 — The map is a document, not an event stream

Invert today's event-sourced-ish model: the map is a single declarative
**document** (assets, viewport, basemap, theme); the server holds the
canonical document; clients render `f(document)`; there is exactly one
mutation verb: `patch` (JSON Patch). Undo/redo, session restore,
screenshots, and multi-viewer sync fall out for free (the Figma/CRDT model).
Most elegant long-term, but a real rewrite of the sync layer — and LLMs are
bad at emitting raw JSON Patch, so verb-shaped tools would still be needed
on top.

### Shift 4 — Grammar of graphics for maps

The full Vega-Lite move: layers are `data + encodings` (`geometry`,
`color: {field, scale}`, `label: {field}`, `height`, ...). Composition
becomes first-class and the taxonomy question dissolves — there is no
taxonomy, only a grammar. Arcs = arc geometry encoding over point-pair
data; curtains = height encoding over a line. Maximum elegance, but a
research project, and it moves complexity into exactly the place LLMs
handle worst (deeply nested declarative specs).

## The honest tension

**What's elegant for the engine is hostile to the LLM.**
`add_point(lon, lat, name)` is trivially callable by a small model; a
grammar spec or JSON Patch is not.

The resolution, if/when this is picked up, is a two-layer architecture:

- **Core (elegant):** one asset model, one rich declarative Style, one
  generic event pipe — Shift 1, backed by Shift 2's single-source schema.
- **Surface (ergonomic):** LLM-facing tools stay flat and specific
  (`add_point`, `label_asset`, `add_arc`), but they are **thin macros that
  compile to the core** — generated from the schema, not hand-mirrored.

> Stop treating the tool list as the architecture; treat it as sugar over a
> stable IR.

Shifts 3 and 4 remain available later because the IR doesn't preclude them.

## Why it was tabled

It's a big-bang refactor (Phase-0-style verb unification + schema
extraction) landing while feature demand is hot. Decision (July 2026):
document it, ship the glyph roadmap on the current architecture, revisit
when the 5-mirror tax becomes painful enough to justify the migration.

## The one constraint we honor NOW

To keep the eventual migration cheap, new glyph capabilities should land as
**Style-schema extensions** (fields on `AssetStyle`) rather than new verbs,
wherever possible:

- Labels (sketches 001/002) → `label*` fields on `AssetStyle` ✅ no new verb
- Gradient lines (sketch 005) → `color_by` field on `AssetStyle` ✅ no new verb
- Hover highlight (sketch 005) → map-level behavior, at most one event
- Arcs (sketch 003) → does need `add_arc`, but the server tessellates it
  into an ordinary LineString asset, so it's a macro over an existing
  primitive — exactly the "surface macro over core" shape described above
- **UPDATE (July 2026): the deck.gl trigger has been PULLED for arcs.**
  The map shell now loads the deck.gl standalone bundle and attaches an
  interleaved `MapboxOverlay`; `add_arc` renders through a true 3D
  `deck.ArcLayer` (height from `curvature`), with MapLibre endpoint
  dots/labels and a flat-LineString fallback when deck fails to load.
  This confirmed the "additive, not a rewrite" prediction.
- Curtain layers (sketch 004) → still parked; now cheap to add since the
  deck overlay machinery exists (add a layer type to renderDeck*)

## Arcs under globe projection — investigated options (July 2026)

deck's `MapboxOverlay` cannot draw over MapLibre's globe projection in
EITHER mode — both verified headless in the shipped container
(maplibre-gl 5.23.0, deck.gl 9.3.7, latest at time of writing):

| approach | mercator | globe | probe result |
|---|---|---|---|
| deck interleaved (current) | ✅ ribbon | ❌ 0 px | `arc_interleave_test` |
| deck non-interleaved (separate canvas) | ✅ 1853 px | ❌ 37 px | `deck_globe_probe` |
| MapLibre `line-elevation-reference` + z-coords | ❌ | ❌ | style validation rejects the property in 5.23/5.24; not in the 6.0.0 bundle either |

Where people HAVE seen arcs on a globe: deck's own `_GlobeView` (no
MapLibre), and Mapbox GL v3's globe (deck 9 supports interleaving with
Mapbox's globe, not MapLibre's). Neither transfers to this stack.

Remaining paths, in order of preference:
1. **Custom MapLibre layer (CustomLayerInterface)** — v5 custom layers
   receive globe projection matrices, so a hand-rolled arc ribbon can
   render in both projections. Shader work; the real fix.
2. Watch MapLibre's globe-support work in deck.gl upstream
   (deck.gl issue tracker: MapLibre globe interleaving) and the
   MapLibre elevated-lines roadmap; adopt whichever lands first.
3. Status quo (shipped): globe mode shows a **dotted great-circle
   geodesic** (syncArcMode suppression). The stored fallback LineString
   is the slerp geodesic — not the old sideways Bézier bow, which read
   as a mistake when drawn flat — and arc assets default to
   `line_dash: [0.5, 2]` (dotted). The deck ribbon ignores line_dash,
   so mercator's solid 3D ribbon is unchanged.
