#!/usr/bin/env python3
"""Smoke test for GeoTIFF processing service."""

import asyncio
import sys
import os

# Add server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "server"))

from mapcontrol_server.services.geotiff_service import (
    process_geotiff_singleband,
    process_geotiff_rgb,
    GeoTIFFResult,
    GeoTIFFError,
)


async def test():
    # Test 1: Single-band with viridis
    print("=== Test 1: Single-band (viridis) on sample.tif ===")
    result = await process_geotiff_singleband(
        source="data/sample.tif",
        asset_id="test-single-1",
        band=1,
        colormap="viridis",
        alpha=0.8,
    )
    if isinstance(result, GeoTIFFResult):
        print(f"  OK: {result.width}x{result.height} px")
        print(f"  Bounds: {result.bounds}")
        print(f"  CRS: {result.crs}")
        print(f"  Bands in file: {result.band_count}")
        print(f"  PNG: {result.png_path}")
        print(f"  URL: {result.image_url}")
    else:
        print(f"  ERROR: {result.error} -- {result.detail}")

    # Test 2: RGB
    print()
    print("=== Test 2: RGB on sample.tif ===")
    result2 = await process_geotiff_rgb(
        source="data/sample.tif",
        asset_id="test-rgb-1",
        bands=[1, 2, 3],
        alpha=1.0,
    )
    if isinstance(result2, GeoTIFFResult):
        print(f"  OK: {result2.width}x{result2.height} px")
        print(f"  Bounds: {result2.bounds}")
        print(f"  CRS: {result2.crs}")
    else:
        print(f"  ERROR: {result2.error} -- {result2.detail}")

    # Test 3: VCDWD file with magma and percentile thresholds
    print()
    print("=== Test 3: VCDWD single-band (magma, p2-p98) ===")
    result3 = await process_geotiff_singleband(
        source="data/VCDWD_L3_F2_NRT.A2026084.h00v01.002.tif",
        asset_id="test-vcdwd-1",
        band=1,
        colormap="magma",
        percentile_min=2,
        percentile_max=98,
    )
    if isinstance(result3, GeoTIFFResult):
        print(f"  OK: {result3.width}x{result3.height} px")
        print(f"  Bounds: {result3.bounds}")
        print(f"  CRS: {result3.crs}")
    else:
        print(f"  ERROR: {result3.error} -- {result3.detail}")

    # Test 4: file:// URI
    print()
    print("=== Test 4: file:// URI ===")
    abs_path = os.path.abspath("data/sample.tif")
    result4 = await process_geotiff_singleband(
        source=f"file://{abs_path}",
        asset_id="test-file-uri",
        band=1,
        colormap="gray",
    )
    if isinstance(result4, GeoTIFFResult):
        print(f"  OK: {result4.width}x{result4.height} px")
    else:
        print(f"  ERROR: {result4.error} -- {result4.detail}")

    print()
    print("All smoke tests complete.")


if __name__ == "__main__":
    asyncio.run(test())
