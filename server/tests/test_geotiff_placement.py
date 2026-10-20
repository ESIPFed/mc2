"""Where GeoTIFF pixels land on the map: rendered images must be on a Web Mercator grid.

The browser places an overlay by its four lon/lat corners and stretches it
linearly in Web Mercator. A UTM raster rendered on its native grid and placed
by its lon/lat bounding box lands rotated and stretched — kilometres off for a
large raster at high latitude (eo-gpt's Enontekiö runs, 68-69°N).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin
from rasterio.warp import transform as warp_points

from mapcontrol_server.services import geotiff_service



class _Points:
    """lon/lat <-> projected point conversion (scalars or sequences) via rasterio."""

    def __init__(self, src, dst):
        self.src, self.dst = src, dst

    def transform(self, x, y):
        scalar = np.isscalar(x)
        xs, ys = warp_points(self.src, self.dst, np.atleast_1d(x).tolist(), np.atleast_1d(y).tolist())
        return (xs[0], ys[0]) if scalar else (xs, ys)


TO_MERC = _Points("EPSG:4326", "EPSG:3857")
FROM_MERC = _Points("EPSG:3857", "EPSG:4326")
TO_UTM = _Points("EPSG:4326", "EPSG:32634")

# Three markers spread over an 80 x 60 km UTM 34N raster at 100 m (Enontekiö).
MARKERS = {0.4: (21.2, 68.95), 0.7: (22.0, 68.75), 1.0: (22.6, 68.6)}
RES = 100.0


def _utm_raster(path):
    x0, y0 = TO_UTM.transform(21.0, 69.05)
    width, height = 800, 600
    data = np.zeros((1, height, width), dtype=np.float32)
    for value, (lon, lat) in MARKERS.items():
        x, y = TO_UTM.transform(lon, lat)
        data[0, int((y0 - y) // RES), int((x - x0) // RES)] = value
    with rasterio.open(path, "w", driver="GTiff", count=1, height=height, width=width,
                       dtype="float32", crs="EPSG:32634", transform=from_origin(x0, y0, RES, RES),
                       nodata=np.nan) as dst:
        dst.write(data)
    return str(path)


def _config(tmp_path):
    return SimpleNamespace(storage=SimpleNamespace(file_dir=str(tmp_path / "files")))


def _displayed_lonlat(bounds, shape, row, col):
    """Where the browser draws PNG pixel (row, col): linear in Web Mercator between the corners."""
    (mx0, mx1), (my0, my1) = TO_MERC.transform([bounds[0], bounds[2]], [bounds[1], bounds[3]])
    height, width = shape
    mx = mx0 + (col + 0.5) / width * (mx1 - mx0)
    my = my1 - (row + 0.5) / height * (my1 - my0)
    return FROM_MERC.transform(mx, my)


def _ground_m(lon1, lat1, lon2, lat2):
    dx = (lon2 - lon1) * 111_320 * np.cos(np.radians((lat1 + lat2) / 2))
    return float(np.hypot(dx, (lat2 - lat1) * 110_570))


def test_utm_singleband_markers_land_where_they_belong(tmp_path):
    result = asyncio.run(geotiff_service.process_geotiff_singleband(
        _utm_raster(tmp_path / "utm.tif"), "asset-utm", band=1, colormap="gray",
        vmin=0.0, vmax=1.0, config=_config(tmp_path)))
    assert isinstance(result, geotiff_service.GeoTIFFResult), result
    rgba = np.asarray(Image.open(result.png_path))
    gray = rgba[:, :, 0].astype(int)
    for value, (lon, lat) in MARKERS.items():
        rows, cols = np.nonzero((np.abs(gray - round(value * 255)) <= 2) & (rgba[:, :, 3] == 255))
        assert len(rows), f"marker {value} missing"
        shown = _displayed_lonlat(result.bounds, gray.shape, rows.mean(), cols.mean())
        # Within ~2 source pixels of the truth (was 1-4 km off on the native grid).
        assert _ground_m(*shown, lon, lat) < 2.5 * RES, (value, shown, (lon, lat))


def test_native_grid_placement_would_have_been_kilometres_off(tmp_path):
    """The failure mode this guards against, measured on the same raster."""
    path = _utm_raster(tmp_path / "utm.tif")
    with rasterio.open(path) as ds:
        bounds = geotiff_service._compute_bounds_4326(ds)
        data = ds.read(1)
    errors = []
    for value, (lon, lat) in MARKERS.items():
        rows, cols = np.nonzero(data == np.float32(value))
        shown = _displayed_lonlat(bounds, data.shape, rows.mean(), cols.mean())
        errors.append(_ground_m(*shown, lon, lat))
    assert max(errors) > 1000, errors


def test_rgb_footprint_outside_is_transparent_and_inside_opaque(tmp_path):
    x0, y0 = TO_UTM.transform(21.0, 69.05)
    path = tmp_path / "rgb.tif"
    with rasterio.open(path, "w", driver="GTiff", count=3, height=300, width=400, dtype="uint8",
                       crs="EPSG:32634", transform=from_origin(x0, y0, RES, RES)) as dst:
        dst.write(np.full((3, 300, 400), 120, dtype=np.uint8))
    result = asyncio.run(geotiff_service.process_geotiff_rgb(
        str(path), "asset-rgb", config=_config(tmp_path)))
    alpha = np.asarray(Image.open(result.png_path))[:, :, 3]
    # The rotated UTM footprint leaves at least one corner of the Mercator box empty.
    assert min(alpha[0, 0], alpha[0, -1], alpha[-1, 0], alpha[-1, -1]) == 0
    assert alpha[alpha.shape[0] // 2, alpha.shape[1] // 2] == 255


def test_web_mercator_input_is_not_resampled(tmp_path):
    path = tmp_path / "merc.tif"
    mx, my = TO_MERC.transform(10.0, 50.0)
    with rasterio.open(path, "w", driver="GTiff", count=1, height=40, width=50, dtype="float32",
                       crs="EPSG:3857", transform=from_origin(mx, my, 250, 250)) as dst:
        dst.write(np.random.default_rng(0).random((1, 40, 50), dtype=np.float32))
    result = asyncio.run(geotiff_service.process_geotiff_singleband(
        str(path), "asset-merc", config=_config(tmp_path)))
    assert (result.width, result.height) == (50, 40)
    assert result.bounds[0] == pytest.approx(10.0) and result.bounds[3] == pytest.approx(50.0)
