#!/usr/bin/env python3
"""One-off: render coastlines + borders as a transparent PNG at the exact
pixel grid of the MEPS native domain, so the browser can just stack it over
whichever cloud-layer frame is showing -- no per-update projection cost.

This is the ONLY place cartopy/pyproj/matplotlib are needed in this repo;
the periodic fetch+render pipeline never imports them. Re-run this only if
the grid definition changes (it won't -- MEPS's native grid is fixed).

Usage (from the WSL venv that has cartopy/pyproj installed):
    python tools/build_coastline_overlay.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import cartopy.io.shapereader as shpreader
from pyproj import Transformer

from mepscloud import config, fetch

OUT = Path(__file__).resolve().parent.parent / "web" / "static" / "overlay.png"


def native_lines(category: str, name: str, resolution: str = "50m", bbox=None):
    """bbox = (lon_min, lat_min, lon_max, lat_max), filters by centroid --
    used for roads (a much denser global dataset) to skip projecting/plotting
    the ~56k features nowhere near our domain."""
    reader = shpreader.Reader(shpreader.natural_earth(resolution=resolution, category=category, name=name))
    to_native = Transformer.from_crs("EPSG:4326", config.NATIVE_PROJ4, always_xy=True)
    lines = []
    for rec in reader.records():
        geom = rec.geometry
        if bbox is not None:
            c = geom.centroid
            if not (bbox[0] <= c.x <= bbox[2] and bbox[1] <= c.y <= bbox[3]):
                continue
        parts = geom.geoms if hasattr(geom, "geoms") else [geom]
        for part in parts:
            lon, lat = np.asarray(part.coords).T
            xs, ys = to_native.transform(lon, lat)
            lines.append((xs, ys))
    return lines


def main():
    # grid extent comes from an actual cached run so the overlay lines up
    # pixel-for-pixel with real frames (same x/y arrays fetch.py wrote).
    run_path = fetch.latest_cached_run_path()
    if run_path is None:
        raise SystemExit("no cached run npz found -- run fetch.fetch_latest_run() first "
                          "(need real x/y arrays to size this overlay exactly)")
    with np.load(run_path) as z:
        x, y = z["x"], z["y"]
    nx, ny = len(x), len(y)
    extent = [x.min(), x.max(), y.min(), y.max()]
    print(f"[overlay] grid {nx}x{ny}, extent={extent}")

    print("[overlay] projecting coastlines/borders/roads into native x/y...")
    coast = native_lines("physical", "coastline")
    borders = native_lines("cultural", "admin_0_boundary_lines_land")
    # generous bbox around the full map domain (not just Finland) so Norway/
    # Sweden/Baltic roads that happen to be in view come along too -- Natural
    # Earth's roads dataset is global, ~56k features, so always filter it.
    roads = native_lines("cultural", "roads", resolution="10m", bbox=(-20, 49, 56, 76))
    print(f"[overlay]   {len(coast)} coastline, {len(borders)} border, {len(roads)} road segments")

    dpi = 100
    fig = plt.figure(figsize=(nx / dpi, ny / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for xs, ys in roads:
        ax.plot(xs, ys, color="#e0c080", lw=0.5, solid_capstyle="round", alpha=0.8, zorder=1)
    for xs, ys in coast:
        ax.plot(xs, ys, color="#00e5ff", lw=0.8, solid_capstyle="round", zorder=2)
    for xs, ys in borders:
        ax.plot(xs, ys, color="#ffee00", lw=0.5, solid_capstyle="round", zorder=2)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT, dpi=dpi, transparent=True)
    print(f"[overlay] wrote {OUT}")

    # sanity: confirm pixel size matches the grid exactly (so the browser can
    # position it with a plain absolute-fill, no scaling math needed)
    from PIL import Image
    im = Image.open(OUT)
    print(f"[overlay] PNG size: {im.size} (grid was {nx}x{ny})")


if __name__ == "__main__":
    main()
