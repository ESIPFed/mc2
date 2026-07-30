FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for rasterio/GDAL.
# (Chromium's shared-library deps are installed by `playwright install
# --with-deps` below — never hand-maintain that list; a stale one ships a
# browser that crashes at launch, e.g. missing libXfixes.so.3.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgdal-dev \
    gdal-bin \
    && rm -rf /var/lib/apt/lists/*

# Copy server source + config
COPY server/ server/

# Install server package (non-editable for production)
RUN pip install --no-cache-dir server/

# Download Chromium AND apt-install the exact system libraries it needs.
# --with-deps delegates the dependency list to Playwright itself, so it stays
# correct across Playwright/Chromium version bumps.
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

# Copy example GeoTIFFs for demos
COPY examples/data/ examples/data/

# Create data directories for SQLite + rendered files
RUN mkdir -p /app/data /app/server/data/files

EXPOSE 8000

WORKDIR /app/server

CMD ["uvicorn", "mapcontrol_server.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
