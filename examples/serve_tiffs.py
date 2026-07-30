#!/usr/bin/env python3
"""Simple file server for testing GeoTIFF URL capability.

Serves files from the examples/data/ directory over HTTP.

Usage:
    python serve_tiffs.py [port]

Default port: 9999

Then use URLs like:
    http://localhost:9999/sample.tif
    http://localhost:9999/VCDWD_L3_F2_NRT.A2026084.h00v01.002.tif
"""

import http.server
import os
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
DIRECTORY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)


if __name__ == "__main__":
    print(f"Serving GeoTIFF files from: {DIRECTORY}")
    print(f"Available files:")
    for f in sorted(os.listdir(DIRECTORY)):
        size_mb = os.path.getsize(os.path.join(DIRECTORY, f)) / (1024 * 1024)
        print(f"  http://localhost:{PORT}/{f}  ({size_mb:.1f} MB)")
    print(f"\nListening on http://localhost:{PORT}")

    with http.server.HTTPServer(("", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down.")
