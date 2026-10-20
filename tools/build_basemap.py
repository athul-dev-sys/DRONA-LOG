#!/usr/bin/env python3
"""Build the offline satellite basemap assets for DRONA-LOG.

Fetches Esri World Imagery tiles and AWS terrarium DEM tiles for the Ladakh
bounding box, stitches them into two georeferenced images, and writes them to
frontend/public/satellite/. Run once at build time; the frontend ships the
resulting JPEG/PNG so the demo makes zero runtime network calls.

Usage:
    python3 tools/build_basemap.py            # both assets
    python3 tools/build_basemap.py --sat      # satellite only
    python3 tools/build_basemap.py --hill     # hillshade only

Attribution (must stay visible in the UI):
    Imagery: Esri, Maxar, Earthstar Geographics
    Terrain: Mapzen/AWS terrain tiles (SRTM, GMTED, ETOPO1)
"""
import argparse
import io
import json
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "frontend" / "public" / "satellite"

# Bounding box: covers every seeded node with margin (grid in TacticalMap is 74-81 / 31.5-36)
LON_MIN, LON_MAX, LAT_MIN, LAT_MAX = 73.0, 82.0, 31.0, 37.0
SAT_ZOOM = 10
DEM_ZOOM = 9

ESRI_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TERRARIUM_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"


def lon_to_tilex(lon, z):
    return (lon + 180.0) / 360.0 * (1 << z)


def lat_to_tiley(lat, z):
    r = math.radians(lat)
    return (1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * (1 << z)


def tilex_to_lon(x, z):
    return x / (1 << z) * 360.0 - 180.0


def tiley_to_lat(y, z):
    n = math.pi - 2.0 * math.pi * y / (1 << z)
    return math.degrees(math.atan(math.sinh(n)))


def tile_range(z):
    x0 = math.floor(lon_to_tilex(LON_MIN, z))
    x1 = math.floor(lon_to_tilex(LON_MAX, z))
    y0 = math.floor(lat_to_tiley(LAT_MAX, z))  # top of bbox = smaller y
    y1 = math.floor(lat_to_tiley(LAT_MIN, z))
    return x0, x1, y0, y1


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "drona-log-basemap-builder/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def stitch(z, url_tpl, fetch_one, array_mode=False):
    """Fetch and mosaic tiles. fetch_one returns bytes (image) or, in
    array_mode, a 2-D float array per tile (e.g. decoded DEM heights)."""
    x0, x1, y0, y1 = tile_range(z)
    cols, rows = x1 - x0 + 1, y1 - y0 + 1
    print(f"zoom {z}: {cols}x{rows} = {cols * rows} tiles")

    grid = {}
    def get(xy):
        x, y = xy
        try:
            grid[xy] = fetch_one(url_tpl.format(z=z, x=x, y=y))
        except Exception as e:  # noqa: BLE001 - retry once below
            print(f"  retry {x}/{y}: {e}", file=sys.stderr)
            try:
                grid[xy] = fetch_one(url_tpl.format(z=z, x=x, y=y))
            except Exception as e2:  # noqa: BLE001
                print(f"  FAILED {x}/{y}: {e2}", file=sys.stderr)
                grid[xy] = None

    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(get, [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]))

    missing = [k for k, v in grid.items() if v is None]
    if missing:
        sys.exit(f"{len(missing)} tiles failed; aborting (bbox would have holes)")

    if array_mode:
        ts = grid[(x0, y0)].shape[0]
        canvas = np.zeros((rows * ts, cols * ts), dtype=np.float64)
        for (x, y), arr in grid.items():
            canvas[(y - y0) * ts:(y - y0 + 1) * ts, (x - x0) * ts:(x - x0 + 1) * ts] = arr
    else:
        first = Image.open(io.BytesIO(grid[(x0, y0)]))
        ts = first.size[0]
        canvas = Image.new(first.mode, (cols * ts, rows * ts))
        for (x, y), data in grid.items():
            canvas.paste(Image.open(io.BytesIO(data)), ((x - x0) * ts, (y - y0) * ts))

    # Exact geographic corners of the stitched pixel area
    bounds = {
        "west": tilex_to_lon(x0, z),
        "east": tilex_to_lon(x1 + 1, z),
        "south": tiley_to_lat(y1 + 1, z),
        "north": tiley_to_lat(y0, z),
    }
    return canvas, bounds


def hillshade(heights, resolution_m, altitude_deg=45.0, azimuth_deg=315.0, z_factor=1.6):
    """Classic horn-style hillshade from a height grid."""
    h = heights.astype(np.float64)
    dy, dx = np.gradient(h, resolution_m)
    slope = np.arctan(z_factor * np.hypot(dx, dy))
    aspect = np.arctan2(-dy, dx)
    alt, az = math.radians(altitude_deg), math.radians(azimuth_deg - 90.0)
    shade = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(shade * 255.0, 0, 255).astype(np.uint8)


def build_satellite():
    def fetch_one(url):
        return fetch(url)

    canvas, bounds = stitch(SAT_ZOOM, ESRI_URL, fetch_one)
    OUT.mkdir(parents=True, exist_ok=True)
    # Cap the long edge so the repo asset stays reasonable (~2-4 MB JPEG)
    w, h = canvas.size
    scale = min(1.0, 5120.0 / max(w, h))
    if scale < 1.0:
        canvas = canvas.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    path = OUT / "ladakh_sat.jpg"
    canvas.save(path, "JPEG", quality=78, optimize=True)
    meta = {"image": path.name, "width": canvas.size[0], "height": canvas.size[1], **bounds}
    (OUT / "ladakh_sat.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {path} ({path.stat().st_size // 1024} KB), bounds={json.dumps(bounds, indent=2)}")


def build_hillshade():
    def fetch_one(url):
        img = Image.open(io.BytesIO(fetch(url)))
        # terrarium encoding: height = (R*256 + G + B/256) - 32768
        arr = np.asarray(img, dtype=np.float64)
        return arr[..., 0] * 256 + arr[..., 1] + arr[..., 2] / 256 - 32768

    canvas, bounds = stitch(DEM_ZOOM, TERRARIUM_URL, fetch_one, array_mode=True)
    heights = canvas

    # Ground resolution per pixel at bbox centre latitude
    lat_mid = (LAT_MIN + LAT_MAX) / 2.0
    res = 156543.03392 * math.cos(math.radians(lat_mid)) / (1 << DEM_ZOOM)
    shade = hillshade(heights, res)

    img = Image.fromarray(shade, "L")
    path = OUT / "ladakh_hillshade.jpg"
    img.save(path, "JPEG", quality=80, optimize=True)
    meta = {"image": path.name, "width": img.size[0], "height": img.size[1], **bounds}
    (OUT / "ladakh_hillshade.json").write_text(json.dumps(meta, indent=2))
    print(f"wrote {path} ({path.stat().st_size // 1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sat", action="store_true", help="build satellite mosaic only")
    ap.add_argument("--hill", action="store_true", help="build hillshade only")
    args = ap.parse_args()
    if args.sat or not (args.sat or args.hill):
        build_satellite()
    if args.hill or not (args.sat or args.hill):
        build_hillshade()


if __name__ == "__main__":
    main()
