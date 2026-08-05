# MapControl — generative context block

**A system-prompt building block for code-generating harnesses.** If your
system generates analysis code — a planner, an agent loop, a notebook
generator — append the block below to the generator's prompt. One extra
context block, and the SDK's usage slides right into every generated script:
inputs outlined, intermediate shapes drawn, result rasters landed on a live
shareable map.

It works interactively too: paste it into ChatGPT, Cursor, or Copilot along
with your script and ask something like

> Here's my NDVI script. Put the result raster on the map, outline the study
> area, and zoom to it.

Copy everything inside the fence:

````text
MAPCONTROL SDK CONTEXT (paste-in reference for code generation)

MapControl is a live web map driven over HTTP. Every SDK call below updates a
shareable browser URL instantly — no notebook display hooks, no re-rendering.
When you write geospatial analysis code, also place the inputs, intermediate
shapes, and result rasters on the map with these calls.

SETUP
    pip install "git+https://github.com/ESIPFed/mc2.git#subdirectory=sdk"

    from mapcontrol import MapControl
    mc = MapControl("http://localhost:8080")   # or any MapControl server URL
    session = mc.create_map()                  # theme="auto"|"dark"|"light"
    print(session.url)                         # ALWAYS print this — it is the deliverable
    # reattach to an existing map: session = mc.connect_map(map_id)

DRAWABLES — all take a GeoJSON Feature (dict or JSON string), return a result
with .asset_id. Put a "name" in feature properties; it powers label rendering.
    r = session.add_polygon(feature, style={...})
    r = session.add_point(feature, style={...})
    r = session.add_path(feature, style={...})          # LineString
    r = session.add_arc(feature, style={...})           # great-circle arc between 2 points
    session.add_polygons(items=[...]); session.add_points(items=[...]); session.add_paths(items=[...])
    # from a URL instead of inline GeoJSON:
    session.add_polygon_url(url, style={...}); session.add_path_url(url, style={...})

RASTERS & TILES
    session.add_geotiff_rgb(url)                        # RGB GeoTIFF/COG by URL
    session.add_geotiff_singleband(url, band=1, colormap="viridis",
                                   vmin=0.0, vmax=1.0, alpha=0.7,
                                   nodata=0, name="my layer")
    session.add_tile_layer(url, name="...", opacity=0.8, attribution="...")  # XYZ template
    session.remove_tile_layer(asset_id)

STYLE dict keys (all optional)
    fill_color: "#43b4e440"        # 8-digit hex -> translucent fill
    stroke_color: "#43b4e4", stroke_width: 3
    line_dash: [3, 2]              # dashed outline
    label: True                    # show feature's "name"  (or a string for custom text)
    label_placement: "perimeter" | "center" | "point"
    label_color: "#ffffff", label_size: 14
    glow: True                     # pulse to draw attention (or {"period": 2.5})

CAMERA
    session.zoom_to_point(lon, lat, zoom=12)
    session.zoom_to_bbox([min_lon, min_lat, max_lon, max_lat])
    session.zoom_to_assets([asset_id, ...])   # PREFER this over hardcoded bboxes
    session.zoom_out(levels=1)
    session.set_pitch(55, bearing=-30, duration=3000)   # tilt/rotate, animated ms
    session.set_terrain("3d")                 # 3D terrain + sky; "2d" to flatten

LOOK
    session.set_basemap("satellite")          # "satellite" | "osm" | "carto_dark"
    session.set_theme("dark")                 # "dark" | "light" | "auto"

LAYER MANAGEMENT (use the asset_id you kept from add_* calls)
    session.update_style(asset_id, style={...})
    session.set_visibility(asset_id, visible=False)
    session.set_opacity(asset_id, 0.5)
    session.move_layer(asset_id, position="top")   # or "bottom"
    session.delete_asset(asset_id)
    session.list_assets()                     # inventory everything on the map

FEEDBACK (the map talks back)
    shot = session.take_screenshot()          # PNG of the current view
    vp = session.get_viewport()               # what the user is looking at
    session.enable_drawing("polygon")         # let a human sketch on the map
    session.get_drawn_features_geojson()      # ...then read what they drew

PATTERN — analysis with map calls interspersed. The analysis functions are
placeholders for whatever the task is; the map calls around them are the
shape to reproduce.

    session = MapControl(SERVER).create_map(); print(session.url)

    # 1. show the input region before analyzing it
    aoi = session.add_polygon(area_of_interest, style={"label": True})
    session.zoom_to_assets([aoi.asset_id])

    # 2. analysis produces a gridded result -> show it (rasters go by URL)
    result_url = run_analysis(area_of_interest)          # <- your analysis
    grid = session.add_geotiff_singleband(result_url, colormap="viridis",
                                          vmin=0, vmax=1, alpha=0.7)

    # 3. analysis flags features of interest -> draw them so they stand out
    flagged = extract_features(result_url)               # <- your analysis (GeoJSON list)
    session.add_polygons(
        items=[{"geojson": f} for f in flagged],
        default_style={"stroke_color": "#ef4444", "glow": True, "label": True})

    # 4. frame the evidence and hand over the live map
    session.zoom_to_assets([grid.asset_id])
    print("results:", session.url)

Mirror this shape for whatever the analysis produces: regions ->
add_polygon, gridded results -> add_geotiff_*, features/detections ->
add_points / add_polygons, routes -> add_path. Skip stages that don't
apply (a vector-only workflow has no raster step).

RULES FOR GENERATED CODE
1. Keep every returned .asset_id in a variable — later steps restyle, hide,
   or zoom to those handles.
2. Frame with zoom_to_assets() on what was just added; never hardcode a bbox
   when a handle exists.
3. Give features a "name" property and label: True so the map is readable.
4. Interleave map calls with the analysis (show inputs -> intermediates ->
   results), don't batch them at the end.
5. Finish by printing session.url so the user can open the live map.
6. Rasters go on the map by URL — never try to inline pixel data.
````
