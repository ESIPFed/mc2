# Map Engine Comparison: MapLibre GL JS vs Cesium vs Deck.gl

> **Last updated:** April 2026
>
> This document provides an extensive feature-by-feature comparison of three leading open-source web map rendering engines. It is intended to inform the MapControl project's choice of rendering frontend(s) and to document trade-offs for the ESIP community.

---

## Quick Overview

| | **MapLibre GL JS** | **Cesium (CesiumJS)** | **Deck.gl** |
|---|---|---|---|
| **Primary focus** | 2D vector maps with tilt/3D extrusion | 3D globe & geospatial visualization | Large-scale data visualization layers |
| **Rendering** | WebGL 2 | WebGL 1/2 | WebGL 2 / WebGPU |
| **License** | BSD-3-Clause | Apache 2.0 | MIT |
| **Maintained by** | MapLibre community | Cesium (company) + community | Vis.gl / OpenJS Foundation |
| **Language** | TypeScript | JavaScript / TypeScript | TypeScript |
| **npm package** | `maplibre-gl` (~800 KB) | `cesium` (~30 MB with assets) | `deck.gl` (~2 MB core) |
| **Default basemap** | Any Mapbox-style JSON | Cesium Ion, Bing, OSM | None (bring your own or use with MapLibre/Google Maps) |

---

## Rendering & Core Capabilities

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **2D map rendering** | ✅ Native | ⚠️ Via 2D Columbus view | ⚠️ Requires basemap (MapLibre, Google Maps) |
| **3D globe** | ❌ | ✅ Native | ⚠️ Via `GlobeView` (experimental) |
| **Mercator projection** | ✅ | ✅ | ✅ |
| **Non-Mercator projections** | ⚠️ Limited (globe view in progress) | ✅ Full (EPSG support via proj4) | ⚠️ Limited |
| **3D terrain / elevation** | ✅ Terrain RGB tiles | ✅ Quantized mesh, heightmaps | ⚠️ Via `TerrainLayer` (limited) |
| **Terrain exaggeration** | ✅ | ✅ | ⚠️ Manual |
| **Underground / subsurface** | ❌ | ✅ Native clipping | ❌ |
| **Vector tile rendering** | ✅ Core strength | ⚠️ Via 3D Tiles or custom | ⚠️ Via `MVTLayer` |
| **Raster tile rendering** | ✅ Native | ✅ Native imagery layers | ⚠️ Via `BitmapLayer` / `TileLayer` |
| **WebGL rendering** | ✅ WebGL 2 | ✅ WebGL 1 & 2 | ✅ WebGL 2 |
| **WebGPU support** | ❌ (planned) | ❌ (planned) | ✅ Experimental (`@deck.gl/gpu`) |
| **Framerate / perf target** | 60 fps | 60 fps | 60 fps |
| **Anti-aliasing** | ✅ MSAA | ✅ FXAA / MSAA | ⚠️ Depends on layer |
| **Retina / HiDPI** | ✅ | ✅ | ✅ |

---

## Layer Types

| Layer / Viz Type | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **GeoJSON polygons** | ✅ `fill` layer | ✅ `GeoJsonDataSource` | ✅ `GeoJsonLayer` |
| **GeoJSON lines** | ✅ `line` layer | ✅ | ✅ `PathLayer` |
| **GeoJSON points** | ✅ `circle` / `symbol` | ✅ | ✅ `ScatterplotLayer` |
| **3D polygon extrusion** | ✅ `fill-extrusion` | ✅ Native 3D entities | ✅ `PolygonLayer` (extruded) |
| **Arc layers** | ❌ | ❌ (custom required) | ✅ `ArcLayer` |
| **Great circle arcs** | ❌ | ✅ Polyline with geodesic | ✅ `ArcLayer` (greatCircle) |
| **Heatmap** | ✅ `heatmap` layer | ❌ (plugin/custom) | ✅ `HeatmapLayer` |
| **Hexagon binning** | ❌ | ❌ | ✅ `HexagonLayer` / `H3HexagonLayer` |
| **H3 cells** | ❌ | ❌ | ✅ `H3HexagonLayer`, `H3ClusterLayer` |
| **Grid aggregation** | ❌ | ❌ | ✅ `GridLayer`, `GPUGridLayer` |
| **Contour / isoline** | ❌ | ❌ | ✅ `ContourLayer` |
| **Screen-grid** | ❌ | ❌ | ✅ `ScreenGridLayer` |
| **Point cloud** | ❌ | ✅ 3D Tiles point clouds | ✅ `PointCloudLayer` |
| **Icon / marker layers** | ✅ `symbol` layer (SDF icons) | ✅ Billboard entities | ✅ `IconLayer` |
| **Text labels** | ✅ `symbol` layer | ✅ Label entities | ✅ `TextLayer` |
| **Bitmap / image overlay** | ✅ `raster` source | ✅ `SingleTileImageryProvider` | ✅ `BitmapLayer` |
| **Animated trips / paths** | ❌ (custom) | ✅ CZML `path` | ✅ `TripsLayer` |
| **Column / bar chart on map** | ❌ | ❌ | ✅ `ColumnLayer` |
| **Scatterplot (sized circles)** | ✅ `circle` layer | ✅ Point entities | ✅ `ScatterplotLayer` |
| **3D mesh / model (glTF)** | ⚠️ Experimental `model` layer | ✅ Native glTF/glb | ✅ `SimpleMeshLayer`, `ScenegraphLayer` |
| **Particle / flow viz** | ❌ | ⚠️ Custom shaders | ⚠️ Custom shaders or `ParticleLayer` (community) |
| **Cluster layer** | ✅ Built-in clustering | ⚠️ Custom via `EntityCluster` | ❌ (manual, or use Supercluster externally) |
| **Line width (pixel & meters)** | ✅ Pixel-based | ✅ Pixel or meters | ✅ Both pixel & meter widths |
| **Dashed / patterned lines** | ✅ `line-dasharray` | ⚠️ Material-based | ⚠️ `PathStyleExtension` |
| **Fill patterns** | ✅ `fill-pattern` | ⚠️ Material | ❌ |
| **Shadows** | ❌ | ✅ Native shadow maps | ❌ |

---

## Tiled Data & Massive Datasets

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Vector tiles (MVT)** | ✅ Core | ⚠️ Custom integration | ✅ `MVTLayer` |
| **Raster tiles (XYZ/TMS)** | ✅ Core | ✅ Multiple imagery providers | ✅ `TileLayer` + `BitmapLayer` |
| **WMTS** | ✅ Via raster source | ✅ `WebMapTileServiceImageryProvider` | ⚠️ Via `TileLayer` (custom) |
| **WMS** | ✅ Via raster source | ✅ `WebMapServiceImageryProvider` | ⚠️ Via `TileLayer` (custom) |
| **OGC 3D Tiles** | ❌ | ✅ Native (pioneered it) | ✅ `Tile3DLayer` (via loaders.gl) |
| **3D Tiles Next (1.1)** | ❌ | ✅ | ✅ (via loaders.gl) |
| **PMTiles** | ✅ Via `pmtiles` protocol | ⚠️ Custom | ✅ Via `pmtiles` + `MVTLayer` |
| **COG (Cloud Optimized GeoTIFF)** | ⚠️ Plugin / custom | ⚠️ Custom imagery provider | ⚠️ Via loaders.gl or custom |
| **MBTiles** | ❌ (server-side only) | ❌ (server-side only) | ❌ (server-side only) |
| **Millions of points** | ⚠️ ~500K before slowdown | ⚠️ Depends on entity type | ✅ Designed for this (GPU aggregation) |
| **Tiled loading / LOD** | ✅ Built-in for tiles | ✅ Advanced LOD (3D Tiles, terrain) | ✅ `TileLayer` with LOD |
| **Streaming / progressive load** | ⚠️ Tile-based only | ✅ Streaming 3D Tiles | ✅ Via data update pattern |
| **Data-driven styling** | ✅ Expressions | ⚠️ Entity properties | ✅ Accessor functions |

---

## Geospatial Data Format Support

| Format | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **GeoJSON** | ✅ | ✅ | ✅ |
| **TopoJSON** | ⚠️ Convert to GeoJSON | ✅ `GeoJsonDataSource` handles it | ⚠️ Convert to GeoJSON |
| **KML / KMZ** | ❌ | ✅ `KmlDataSource` | ❌ (convert externally) |
| **CZML** | ❌ | ✅ Native (Cesium's own format) | ❌ |
| **GPX** | ❌ | ✅ `GpxDataSource` | ❌ |
| **Shapefile** | ❌ | ❌ (convert) | ⚠️ Via loaders.gl `ShapefileLoader` |
| **GeoTIFF / COG** | ⚠️ Plugin | ⚠️ Custom provider | ⚠️ Via loaders.gl |
| **GeoParquet** | ❌ | ❌ | ⚠️ Via loaders.gl (experimental) |
| **FlatGeobuf** | ⚠️ Via custom source | ❌ | ⚠️ Via loaders.gl |
| **GeoArrow** | ❌ | ❌ | ✅ Native support (v9+) |
| **CSV / TSV (lat/lon)** | ❌ | ❌ | ✅ Via loaders.gl |
| **glTF / glb** | ⚠️ Experimental | ✅ Native | ✅ `ScenegraphLayer` |
| **3D Tiles (.b3dm, .pnts, .i3dm)** | ❌ | ✅ | ✅ (via loaders.gl) |
| **LAS / LAZ (point cloud)** | ❌ | ✅ Via 3D Tiles | ⚠️ Via loaders.gl |
| **Terrain (quantized mesh)** | ✅ | ✅ | ⚠️ `TerrainLayer` |
| **Terrain RGB** | ✅ Native | ⚠️ Custom | ⚠️ Custom |

---

## Camera & Navigation

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Smooth pan / zoom** | ✅ | ✅ | ✅ |
| **Fly-to animation** | ✅ `flyTo()` | ✅ `flyTo()` | ✅ `viewState` transitions |
| **Ballistic / eased zoom** | ✅ Easing functions | ✅ | ✅ Transition easing |
| **Pitch / tilt** | ✅ 0–85° | ✅ Full 3D camera | ✅ 0–90° |
| **Bearing / rotation** | ✅ | ✅ | ✅ |
| **First-person view** | ❌ | ✅ Native | ✅ `FirstPersonView` |
| **Orbit view** | ❌ | ✅ | ✅ `OrbitView` |
| **Camera along path** | ⚠️ Manual | ✅ CZML camera path | ⚠️ Manual |
| **Min/max zoom constraints** | ✅ | ✅ | ✅ |
| **Min/max pitch constraints** | ✅ | ✅ | ⚠️ |
| **Bounding box fit** | ✅ `fitBounds()` | ✅ `flyToBoundingSphere()` | ✅ `WebMercatorViewport.fitBounds()` |
| **Keyboard navigation** | ✅ | ✅ | ⚠️ Manual |
| **Touch gestures** | ✅ Full mobile support | ✅ | ✅ (via `mjolnir.js`) |
| **Gamepad support** | ❌ | ⚠️ Community plugin | ❌ |
| **Scroll zoom** | ✅ | ✅ | ✅ |
| **Double-click zoom** | ✅ | ✅ | ✅ |
| **Inertia / momentum** | ✅ | ✅ | ⚠️ |

---

## Interactivity & Events

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Click events on features** | ✅ `queryRenderedFeatures` | ✅ `scene.pick()` | ✅ `onClick` per layer |
| **Hover events** | ✅ | ✅ | ✅ `onHover` per layer |
| **Tooltips** | ⚠️ Build your own (popup API) | ⚠️ Build your own (InfoBox) | ⚠️ Build your own (`getTooltip`) |
| **Drag events** | ✅ | ✅ | ✅ `onDrag` |
| **Drawing / editing** | ⚠️ Via `maplibre-gl-draw` | ⚠️ Via `DrawHandler` | ⚠️ Via `nebula.gl` (EditableGeoJsonLayer) |
| **Measure distance** | ⚠️ Plugin | ⚠️ Custom | ⚠️ Custom or nebula.gl |
| **Right-click / context menu** | ✅ `contextmenu` event | ✅ | ⚠️ Manual |
| **Feature highlighting** | ✅ Feature state API | ⚠️ Entity color change | ✅ `highlightedObjectIndex` |
| **Cursor change on hover** | ✅ | ✅ | ⚠️ Manual CSS |
| **postMessage for iframe embed** | ✅ (custom implementation) | ✅ (custom implementation) | ✅ (custom implementation) |

---

## Styling & Theming

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Style specification** | ✅ Mapbox Style Spec | ❌ Programmatic only | ❌ Programmatic only |
| **JSON-based style** | ✅ Full style JSON | ❌ | ❌ |
| **Runtime style changes** | ✅ `setPaintProperty`, etc. | ✅ Entity property updates | ✅ Layer prop updates |
| **Data-driven styling** | ✅ Expressions (`["get", "prop"]`) | ⚠️ `CallbackProperty` | ✅ Accessor functions |
| **Color ramps / interpolation** | ✅ Style expressions | ⚠️ Manual | ✅ `colorRange` props |
| **Opacity control** | ✅ | ✅ | ✅ |
| **Blend modes** | ⚠️ Limited | ⚠️ Limited | ✅ `blendMode` parameter |
| **Custom shaders** | ⚠️ Experimental (custom layers) | ✅ `CustomShaderMode`, appearances | ✅ Shader modules & extensions |
| **Dark mode / light mode basemaps** | ✅ Swap style JSON | ⚠️ Swap imagery | N/A (depends on basemap) |
| **Sprite / icon atlases** | ✅ Built-in | ⚠️ Manual billboard management | ✅ `iconAtlas` prop |
| **SDF icons (scalable)** | ✅ | ❌ | ✅ `IconLayer` (SDF mode) |
| **Font rendering (glyphs)** | ✅ Glyph PBF | ⚠️ Label entity with CSS font | ✅ `TextLayer` with font atlas |

---

## Time & Animation

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Built-in timeline / clock** | ❌ | ✅ `Clock`, `Timeline` widget | ❌ |
| **Temporal data playback** | ❌ (custom) | ✅ CZML time-dynamic data | ⚠️ `TripsLayer`, manual |
| **Keyframe animation** | ❌ | ✅ CZML / SampledProperty | ❌ (manual) |
| **Animated layer transitions** | ⚠️ Paint property transitions | ✅ Property interpolation | ✅ Layer transition props |
| **Time-series raster** | ❌ (swap sources) | ⚠️ (swap imagery) | ❌ (swap data) |
| **Animated camera paths** | ⚠️ Chain `flyTo` calls | ✅ CZML camera + `clock` | ⚠️ Transition callbacks |
| **Real-time data update** | ✅ `setData` on source | ✅ Entity updates | ✅ Prop / data updates at 60fps |

---

## 3D & Scientific Visualization

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **3D buildings** | ✅ Extruded polygons + OSM buildings | ✅ 3D Tiles | ✅ Extruded `PolygonLayer` |
| **3D models (glTF)** | ⚠️ Experimental | ✅ Native | ✅ `ScenegraphLayer` |
| **BIM / IFC** | ❌ | ⚠️ Via 3D Tiles conversion | ⚠️ Via loaders.gl |
| **Satellite imagery** | ✅ Raster tiles | ✅ Multiple providers (Bing, Sentinel, etc.) | ⚠️ Via basemap |
| **Atmospheric effects** | ❌ | ✅ Sky atmosphere, sun, fog | ❌ |
| **Day/night cycle** | ❌ | ✅ Sun position, lighting | ❌ |
| **Weather / wind viz** | ❌ | ⚠️ Custom particle system | ⚠️ Custom particle layers |
| **Volumetric rendering** | ❌ | ⚠️ Custom | ❌ |
| **Bathymetry** | ⚠️ Via terrain | ✅ Via terrain | ⚠️ Via terrain layer |
| **Cross-section / slicing** | ❌ | ✅ Clipping planes | ❌ |
| **Measurement tools** | ⚠️ Plugin | ⚠️ Custom | ❌ |
| **Coordinate reference systems** | ⚠️ EPSG:3857 / 4326 | ✅ Flexible CRS support | ⚠️ EPSG:3857 / 4326 |
| **Geodesic calculations** | ⚠️ External lib (Turf.js) | ✅ Built-in `EllipsoidGeodesic` | ⚠️ External lib |

---

## Framework Integration

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Vanilla JS** | ✅ | ✅ | ✅ |
| **React wrapper** | ✅ `react-map-gl` | ✅ `resium` | ✅ `@deck.gl/react` (native) |
| **Vue wrapper** | ✅ `vue-maplibre-gl` | ⚠️ Community | ⚠️ Community |
| **Angular wrapper** | ⚠️ Community | ✅ `angular-cesium` | ⚠️ Community |
| **Svelte wrapper** | ⚠️ Community | ⚠️ Community | ⚠️ Community |
| **Jupyter / Python** | ✅ `maplibre` (ipywidget) | ⚠️ Limited | ✅ `pydeck` |
| **R (Shiny)** | ✅ `mapgl` / `rdeck` | ⚠️ Limited | ✅ `rdeck` |
| **Observable / D3** | ✅ | ⚠️ | ✅ |
| **Server-side rendering** | ❌ | ❌ | ⚠️ Experimental |

---

## Developer Experience

| Feature | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **TypeScript types** | ✅ Built-in | ✅ Built-in | ✅ Built-in |
| **API documentation** | ✅ Excellent | ✅ Excellent (Sandcastle) | ✅ Good (vis.gl site) |
| **Examples / demos** | ✅ 200+ examples | ✅ Sandcastle (150+) | ✅ 80+ examples |
| **Declarative API** | ⚠️ Style JSON is declarative | ❌ Mostly imperative | ✅ Fully declarative layers |
| **Imperative API** | ✅ | ✅ | ✅ |
| **Plugin / extension system** | ✅ `addControl`, custom layers | ✅ Plugins, viewers | ✅ Layer extensions, shader modules |
| **Custom layer API** | ✅ WebGL custom layers | ✅ Primitives, custom shaders | ✅ Subclass `Layer` |
| **Debug tools** | ⚠️ `showTileBoundaries`, etc. | ✅ Inspector, debug viz | ⚠️ `pickingRadius` debug |
| **Bundle size (gzipped)** | ~200 KB | ~800 KB (no assets) | ~300 KB (core) |
| **Tree-shakeable** | ❌ | ❌ | ✅ |
| **CDN / script tag** | ✅ | ✅ | ✅ |
| **npm install** | ✅ | ✅ | ✅ |

---

## Performance Characteristics

| Metric | MapLibre GL JS | CesiumJS | Deck.gl |
|---|:---:|:---:|:---:|
| **Vector tile rendering speed** | ✅ Optimized | ⚠️ Not primary use case | ✅ `MVTLayer` fast |
| **Millions of points** | ⚠️ Up to ~500K comfortably | ⚠️ ~100K entities (more via 3D Tiles) | ✅ Millions via GPU layers |
| **GPU aggregation** | ❌ | ❌ | ✅ `GPUGridLayer`, `GPUHexagonLayer` |
| **Instanced rendering** | ⚠️ Limited | ✅ 3D Tiles instancing | ✅ All layers use instancing |
| **Web Workers** | ✅ Tile parsing in workers | ✅ Terrain & tile decoding | ✅ Data processing in workers |
| **Off-screen rendering** | ⚠️ | ✅ | ✅ |
| **Memory efficiency** | ✅ | ⚠️ Higher baseline | ✅ Columnar memory layout |
| **Startup time** | ✅ Fast | ⚠️ Slower (large runtime) | ✅ Fast |

---

## Composability & Interop

One of the most powerful patterns is **combining engines together**. Here's what works:

| Combination | How | Notes |
|---|---|---|
| **Deck.gl + MapLibre** | ✅ `@deck.gl/mapbox` interleaved | First-class integration. Deck.gl layers render inside MapLibre's WebGL context. Interleaved rendering means deck layers can appear between maplibre layers. |
| **Deck.gl + Google Maps** | ✅ `@deck.gl/google-maps` | Official overlay integration. |
| **Deck.gl + Cesium** | ⚠️ Experimental | Not officially supported, but possible with `@deck.gl/carto` or custom. |
| **MapLibre + Cesium** | ❌ | Different paradigms; pick one. |
| **MapLibre as Cesium imagery** | ⚠️ Custom `ImageryProvider` | Render MapLibre tiles as Cesium imagery (hacky). |

---

## Use Case Suitability Matrix

| Use Case | Best Engine | Runner-up | Notes |
|---|---|---|---|
| **Classic 2D web map** | MapLibre | Deck.gl + MapLibre | MapLibre excels at cartographic rendering |
| **3D globe / planet view** | Cesium | — | Only true globe engine |
| **Large-scale point data** | Deck.gl | MapLibre (clustered) | Deck.gl handles millions natively |
| **Arc / flow visualization** | Deck.gl | — | `ArcLayer`, `TripsLayer` are unique |
| **Heatmap / aggregation** | Deck.gl | MapLibre | GPU aggregation in Deck.gl |
| **3D city models / BIM** | Cesium | Deck.gl (3D Tiles) | Cesium pioneered 3D Tiles |
| **Terrain / elevation** | Cesium | MapLibre | Both support terrain, Cesium is richer |
| **Satellite imagery** | Cesium | MapLibre | Cesium has more providers |
| **Time-series / animation** | Cesium | Deck.gl | CZML is purpose-built for this |
| **Scientific visualization** | Deck.gl | Cesium | Custom shaders + aggregation |
| **Embeddable iframe map** | MapLibre | Deck.gl + MapLibre | Smallest footprint, fastest load |
| **Python / Jupyter** | Deck.gl (pydeck) | MapLibre | pydeck is mature |
| **Real-time dashboards** | Deck.gl + MapLibre | MapLibre | Deck.gl's declarative updates are ideal |
| **Indoor mapping** | MapLibre | Cesium (3D Tiles indoor) | MapLibre has floor plan styling support |
| **Offline / air-gapped** | MapLibre | Cesium (self-hosted) | MapLibre has smallest dependency footprint |

---

## Relevance to MapControl

For the MapControl project specifically:

| MapControl Requirement | MapLibre | Cesium | Deck.gl |
|---|:---:|:---:|:---:|
| **Pure embeddable map (no chrome)** | ✅ Ideal | ⚠️ Has widgets to strip | ✅ Ideal |
| **Event-driven mutations** | ✅ Programmatic API | ✅ Programmatic API | ✅ Declarative props |
| **Smooth animated transitions** | ✅ `flyTo`, easing | ✅ `flyTo` | ✅ `viewState` transitions |
| **postMessage communication** | ✅ Easy (DOM-based) | ✅ Easy | ✅ Easy |
| **GeoTIFF / COG overlay** | ⚠️ Needs custom layer | ⚠️ Custom provider | ⚠️ Via loaders.gl |
| **Asset layers (markers, polygons)** | ✅ Native | ✅ Native | ✅ Native |
| **Lightweight bundle** | ✅ ~200 KB | ❌ ~800 KB+ | ✅ ~300 KB |
| **Works in iframe** | ✅ | ✅ | ✅ |
| **Multiple basemap support** | ✅ Style JSON swap | ✅ Imagery swap | N/A (uses basemap engine) |

### Recommendation for MapControl

**Primary: MapLibre GL JS** — lightweight, fast, Mapbox Style Spec, ideal for a pure rendering surface.

**Secondary / overlay: Deck.gl** — when advanced visualization layers (arcs, heatmaps, hexagons, large point sets) are needed, layer Deck.gl on top of MapLibre via `@deck.gl/mapbox`.

**Future / optional: CesiumJS** — if 3D globe or true 3D terrain becomes a requirement, Cesium can be offered as an alternative rendering backend.

---

## Feature Roadmap: MapLibre → Deck.gl → Cesium Bridge

The following is a phased feature plan designed so that each tier naturally creates the motivation for the next. Features start in MapLibre (lightweight, already in the frontend), then escalate to Deck.gl when MapLibre hits its limits, and finally to Cesium when a true 3D globe is needed.

### 🟢 Tier 1: Pure MapLibre Enhancements

These features work in MapLibre today. They expand the existing `Style` model and add new layer types to the inline frontend.

| # | Feature | MapLibre Property | Current State | Escalation Path |
|---|---------|-------------------|---------------|-----------------|
| 1 | **Dashed lines** | `line-dasharray: [4, 2]` | ❌ Not exposed | Deck.gl `PathStyleExtension` for animated marching ants |
| 2 | **Fill patterns** | `fill-pattern` + sprite sheet | ❌ Not exposed | Deck.gl custom shaders for dynamic/data-driven patterns |
| 3 | **Fill opacity** | `fill-opacity` | ⚠️ Hardcoded at 0.5 | Make it a `Style` property |
| 4 | **Stroke opacity** | `line-opacity` | ❌ Not exposed | Separate from fill opacity |
| 5 | **3D polygon extrusion** | `fill-extrusion-height`, `fill-extrusion-color` | ❌ Not implemented | Cesium native 3D, Deck.gl `PolygonLayer(extruded)` |
| 6 | **Circle radius** | `circle-radius` | ⚠️ Hardcoded at 6 | Data-driven: `["get", "magnitude"]` → Deck.gl `ScatterplotLayer` at scale |
| 7 | **Point clustering** | Source option `cluster: true` | ❌ Not implemented | Deck.gl GPU aggregation when MapLibre clustering hits limits (~500K pts) |
| 8 | **Text labels** | `symbol` layer + `text-field` | ❌ Not implemented | Deck.gl `TextLayer` for dense labeling at scale |
| 9 | **Heatmap layer** | MapLibre native `heatmap` type | ❌ New event type needed | Deck.gl `HeatmapLayer` for GPU-accelerated rendering |
| 10 | **Color ramps (data-driven fill)** | `["interpolate", ["get", "value"]]` | ❌ Not implemented | Deck.gl accessor functions for complex aggregation |
| 11 | **Great circle arcs (flat)** | Turf.js `greatCircle()` → `line` layer | ❌ Not implemented | Deck.gl `ArcLayer` for 3D rising arcs (see deep dive below) |

#### Suggested Implementation Sprints

**Sprint 1 — Expand Style Model** (low effort, high impact):
- `line_dasharray` — add to Style, wire to `line-dasharray` paint property
- `fill_opacity` / `stroke_opacity` — add to Style, wire to paint properties
- `circle_radius` — already hardcoded at 6, make it a Style property
- `fill_pattern` — requires generating/hosting a sprite, but very impactful for geological/zoning maps

**Sprint 2 — New MapLibre Layer Types** (medium effort):
- `add_heatmap` event type — new event, new frontend handler, MapLibre native heatmap
- `add_extruded_polygon` event — `fill-extrusion` layer, needs `height` in Style
- `add_arc` event — compute great circle via Turf.js, render as styled `line` layer
- Point clustering toggle — on the GeoJSON source, not the layer

### 🔶 Tier 2: Deck.gl Overlay Features

These features **don't exist in MapLibre** — they're the reason to bring in Deck.gl. Deck.gl renders interleaved inside MapLibre's WebGL context via `@deck.gl/mapbox`, so you keep your basemap and asset layers while adding Deck.gl visualization layers on top.

| # | Feature | Deck.gl Layer | Why It's Compelling | ESIP Use Case |
|---|---------|---------------|---------------------|---------------|
| 12 | **3D Arc layers** (origin → destination) | `ArcLayer` | 3D curves rising above the map, color gradients, GPU-instanced | Flights, data flows between stations, satellite links |
| 13 | **Animated trip paths** | `TripsLayer` | Objects moving along routes over time with trailing effect | Vehicle tracking, satellite ground tracks, hurricane paths |
| 14 | **Hexagon binning** | `HexagonLayer` / `H3HexagonLayer` | Aggregate 100K+ points into beautiful hex grids | Observation density, measurement distribution |
| 15 | **GPU grid aggregation** | `GPUGridLayer` | Real-time density grids, fully GPU-accelerated | Sensor reading heatmaps, coverage analysis |
| 16 | **Contour / isolines** | `ContourLayer` | Continuous field visualization with isolines | Temperature, pressure, elevation, precipitation contours |
| 17 | **3D columns on map** | `ColumnLayer` | Bar charts rising from geographic locations | Station-by-station comparison, measurement magnitude |
| 18 | **Scatterplot (millions)** | `ScatterplotLayer` | GPU-instanced circles, handles millions of points at 60fps | Large observation datasets, global sensor networks |
| 19 | **Screen-space grid** | `ScreenGridLayer` | Viewport-relative density grid, updates as you pan | Real-time data exploration |

#### Implementation Sprint

**Sprint 3 — Deck.gl Integration** (the big move):
1. Add `deck.gl` + `@deck.gl/mapbox` to the frontend build
2. Create `MapboxOverlay` instance, attach to MapLibre map
3. `add_arc` event → upgrade from Turf.js LineString to Deck.gl `ArcLayer`
4. `add_hexbin` event type → Deck.gl `HexagonLayer`
5. `add_trip` event type → Deck.gl `TripsLayer` with time animation
6. `add_contour` event type → Deck.gl `ContourLayer`

### 🔴 Tier 3: Cesium (Alternative Rendering Backend)

These features **require a 3D globe** or advanced 3D capabilities that MapLibre can't provide.

| # | Feature | Cesium Capability | Use Case |
|---|---------|-------------------|----------|
| 20 | **3D globe view** | Native globe rendering | Zoom from space → street level, polar regions without Mercator distortion |
| 21 | **Terrain draping** | Quantized mesh + clamp-to-ground | Hiking trails on real terrain, flood modeling |
| 22 | **Timeline / temporal playback** | CZML + Clock widget | Satellite orbits, weather progression, historical data |
| 23 | **3D models (glTF)** | Native glTF/glb placement | Buildings, instruments, vehicles placed on terrain |
| 24 | **Atmospheric effects** | Sky, sun, fog | Realistic globe rendering for public-facing demos |
| 25 | **Underground / cross-section** | Clipping planes | Geological visualization, subsurface sensors |
| 26 | **Satellite imagery providers** | Bing, Sentinel, Cesium Ion | Multi-provider imagery with global coverage |

---

## Deep Dive: Arc Layers — MapLibre vs Deck.gl

Arcs are a great example of the **progressive enhancement** strategy. You can start with MapLibre and upgrade to Deck.gl when ready — the SDK API stays the same.

### What MapLibre CAN Do

- **Great circle lines** — compute a geodesic curve between two points using Turf.js `turf.greatCircle(start, end)`, which produces a curved GeoJSON LineString. Render as a normal `line` layer.
- **Bezier curves** — compute smooth curves client-side, render as LineStrings.
- **Styled lines** — `line-dasharray`, `line-color`, `line-width`, `line-opacity`, `line-gradient` all work.
- All of these are **flat on the map surface** — curved lines drawn on the 2D plane.

### What MapLibre CANNOT Do

- **3D rising arcs** — the signature Deck.gl `ArcLayer` look where lines arc UP above the map surface, rising from origin to destination with height proportional to distance. This is a fundamentally different visual.

### Visual Comparison

```
Deck.gl ArcLayer:              MapLibre great circle:

       ╱‾‾‾‾‾‾╲
      ╱        ╲                  _______________
     ╱          ╲                ╱               ╲
    A            B              A                 B
    ▪────────────▪              ▪─────────────────▪
    (3D arc rises above map)    (flat curve on map surface)
```

### Phased Implementation

**Phase 1 — MapLibre (works today):**
```
SDK call:  session.add_arc(origin=[lon, lat], destination=[lon, lat], style={...})
Event:     add_arc → { origin, destination, style }
Frontend:  Turf.js greatCircle() → GeoJSON LineString → MapLibre line layer
Result:    Flat curved line with dashes, colors, opacity
```

**Phase 2 — Deck.gl (when integrated):**
```
SDK call:  session.add_arc(origin=[lon, lat], destination=[lon, lat], style={...})  ← SAME API
Event:     add_arc → { origin, destination, style }  ← SAME EVENT
Frontend:  Deck.gl ArcLayer({ data: [{ source, target }] })
Result:    3D rising arc with source/target colors, height, GPU-instanced
```

The beauty: **the SDK API and event format don't change** — only the frontend renderer upgrades. This is the core architectural principle of MapControl: the event system is the interface.

### Style Properties for Arcs

| Property | MapLibre Phase | Deck.gl Phase |
|----------|---------------|---------------|
| `source_color` | `line-color` (single color) | `getSourceColor` accessor |
| `target_color` | N/A (single color only) | `getTargetColor` accessor |
| `width` | `line-width` | `getWidth` accessor |
| `height` | N/A (flat) | `getHeight` (arc rise) |
| `dash_array` | `line-dasharray` | `PathStyleExtension` |
| `opacity` | `line-opacity` | `opacity` prop |
| `great_circle` | Always (Turf.js) | `greatCircle: true` prop |
| `num_segments` | Turf.js `npoints` option | `numSegments` prop |

---

## Legend

| Symbol | Meaning |
|---|---|
| ✅ | Native / first-class support |
| ⚠️ | Partial support, requires plugin, extension, or workaround |
| ❌ | Not supported or not applicable |

---

## References

- [MapLibre GL JS Docs](https://maplibre.org/maplibre-gl-js/docs/)
- [CesiumJS Docs](https://cesium.com/learn/cesiumjs/ref-doc/)
- [Deck.gl Docs](https://deck.gl/docs)
- [loaders.gl](https://loaders.gl/) — Deck.gl's data loading framework
- [Mapbox Style Specification](https://maplibre.org/maplibre-style-spec/)
- [OGC 3D Tiles](https://www.ogc.org/standard/3dtiles/)
- [vis.gl](https://vis.gl/) — Deck.gl's parent framework
