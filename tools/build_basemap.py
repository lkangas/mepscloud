#!/usr/bin/env python3
"""One-off: render a filled land/water basemap PNG at the exact pixel grid
of the MEPS native domain (same approach as build_coastline_overlay.py --
see that file for why this only runs here, not in the production pipeline).

Land polygons + lakes (punched back to water colour) from Natural Earth,
projected with the same confirmed native transform used everywhere else in
this repo, so it's pixel-perfect under the coastline overlay and cloud
frames without any scaling/alignment math in the browser.

Usage (from the WSL venv that has cartopy/pyproj installed):
    python tools/build_basemap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import PathPatch
import cartopy.io.shapereader as shpreader
from pyproj import Transformer

from mepscloud import config, fetch

OUT = Path(__file__).resolve().parent.parent / "web" / "static" / "basemap.png"

# Placeholder palette -- tuning pass comes later, once this is composited
# with real (transparent-clear) cloud frames in the viewer.
WATER = "#0b1d3a"
LAND = "#1c2b1e"


def native_polygons(category: str, name: str, resolution: str = "50m"):
    reader = shpreader.Reader(shpreader.natural_earth(resolution=resolution, category=category, name=name))
    to_native = Transformer.from_crs("EPSG:4326", config.NATIVE_PROJ4, always_xy=True)
    paths = []
    for rec in reader.records():
        geom = rec.geometry
        polys = geom.geoms if hasattr(geom, "geoms") else [geom]
        for poly in polys:
            paths.append(_polygon_to_path(poly, to_native))
    return paths


def _polygon_to_path(poly, transformer) -> MplPath:
    """Exterior + interior rings (holes) as one compound Path, so lakes /
    enclosed seas render as holes rather than needing separate fill calls."""
    vertices, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        lon, lat = np.asarray(ring.coords).T
        xs, ys = transformer.transform(lon, lat)
        pts = np.column_stack([xs, ys])
        vertices.append(pts)
        codes.append([MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY])
    return MplPath(np.concatenate(vertices), np.concatenate(codes))


def main():
    run_path = fetch.latest_cached_run_path()
    if run_path is None:
        raise SystemExit("no cached run npz found -- need real x/y arrays to size this exactly")
    with np.load(run_path) as z:
        x, y = z["x"], z["y"]
    nx, ny = len(x), len(y)
    extent = [x.min(), x.max(), y.min(), y.max()]
    print(f"[basemap] grid {nx}x{ny}, extent={extent}")

    print("[basemap] projecting land polygons...")
    land_paths = native_polygons("physical", "land")
    print(f"[basemap] projecting lakes (finer 10m -- Finland is lake-dense)...")
    lake_paths = native_polygons("physical", "lakes", resolution="10m")

    dpi = 100
    fig = plt.figure(figsize=(nx / dpi, ny / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    ax.set_facecolor(WATER)
    fig.patch.set_facecolor(WATER)

    for path in land_paths:
        ax.add_patch(PathPatch(path, facecolor=LAND, edgecolor="none"))
    for path in lake_paths:
        ax.add_patch(PathPatch(path, facecolor=WATER, edgecolor="none"))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # savefig's facecolor defaults to the *figure's* facecolor at save time
    # regardless of ax.set_facecolor() -- pass it explicitly too, or the
    # background comes out white.
    fig.savefig(OUT, dpi=dpi, facecolor=WATER)  # opaque -- this is the bottom layer
    print(f"[basemap] wrote {OUT}")

    from PIL import Image
    im = Image.open(OUT)
    print(f"[basemap] PNG size: {im.size} (grid was {nx}x{ny})")


if __name__ == "__main__":
    main()
