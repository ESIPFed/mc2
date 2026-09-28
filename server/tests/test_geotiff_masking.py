"""Which GeoTIFF pixels render transparent: nodata tags, mask bands, alpha bands."""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.transform import from_origin

from mapcontrol_server.services.geotiff_service import _process_rgb, _process_singleband


def _write(path, data, *, nodata=None, mask=None, alpha=False):
    data = np.asarray(data)
    profile = dict(
        driver="GTiff", count=data.shape[0], height=data.shape[1], width=data.shape[2],
        dtype=data.dtype, crs="EPSG:4326", transform=from_origin(0, 10, 1, 1),
        nodata=nodata,
    )
    if alpha:
        profile["photometric"] = "RGB"
        profile["alpha"] = "YES"
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
        with rasterio.open(path, "w", **profile) as dst:
            dst.write(data)
            if mask is not None:
                dst.write_mask(mask)
    return str(path)


def _rgb_alpha(path, **kwargs):
    rgba, ds = _process_rgb(path, kwargs.pop("bands", [1, 2, 3]), 1.0, kwargs.pop("nodata", None))
    ds.close()
    return rgba[:, :, 3]


def _scene():
    data = np.full((3, 4, 4), 120, dtype=np.uint8)
    data[:, 0, 0] = 0               # fill: zero in every band
    data[:, 1, 1] = (255, 0, 0)     # pure red: one-band zeros are data
    data[:, 2, 2] = (0, 0, 40)      # deep shadow
    return data


def test_rgb_nodata_requires_all_bands(tmp_path):
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", _scene(), nodata=0))
    assert alpha[0, 0] == 0
    assert alpha[1, 1] == 255 and alpha[2, 2] == 255
    assert (alpha[3, :] == 255).all()


def test_rgb_nodata_override_uses_the_same_rule(tmp_path):
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", _scene()), nodata=0)
    assert alpha[0, 0] == 0 and alpha[1, 1] == 255 and alpha[2, 2] == 255


def test_rgb_nan_in_any_band_is_transparent(tmp_path):
    data = np.full((3, 4, 4), 0.2, dtype=np.float32)
    data[1, 3, 3] = np.nan
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", data))
    assert alpha[3, 3] == 0 and alpha[0, 0] == 255


def test_rgb_honors_internal_mask_band(tmp_path):
    mask = np.full((4, 4), 255, dtype=np.uint8)
    mask[:2, :] = 0
    data = np.full((3, 4, 4), 90, dtype=np.uint8)  # no tag, no zeros
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", data, mask=mask))
    assert (alpha[:2, :] == 0).all() and (alpha[2:, :] == 255).all()
    assert not list(tmp_path.glob("*.msk"))


def test_rgb_honors_alpha_band(tmp_path):
    data = np.full((4, 4, 4), 90, dtype=np.uint8)
    data[3] = 255
    data[3, :, :2] = 0
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", data, alpha=True))
    assert (alpha[:, :2] == 0).all() and (alpha[:, 2:] == 255).all()


def test_rgb_without_nodata_or_mask_is_fully_opaque(tmp_path):
    alpha = _rgb_alpha(_write(tmp_path / "a.tif", _scene()))
    assert (alpha == 255).all()


def test_singleband_honors_mask_and_nodata(tmp_path):
    data = np.arange(16, dtype=np.float32).reshape(1, 4, 4)
    mask = np.full((4, 4), 255, dtype=np.uint8)
    mask[3, :] = 0
    path = _write(tmp_path / "s.tif", data, nodata=5.0, mask=mask)
    rgba, ds = _process_singleband(path, 1, "viridis", 1.0, None, None, None, None, None)
    ds.close()
    alpha = rgba[:, :, 3]
    assert (alpha[3, :] == 0).all()
    assert alpha[1, 1] == 0            # value 5 = nodata tag
    assert alpha[0, 0] == 255 and alpha[2, 3] == 255
