#!/usr/bin/env python3
"""One-off: render a LAND MASK PNG at the exact pixel grid of the MEPS native
domain (same approach/why as build_coastline_overlay.py).

Output is web/static/landmask.png: white (opaque) over land, transparent
over sea and lakes, anti-aliased at the coast. The viewer colours the map in
two independently-selectable parts: a full-bleed sea-colour background with
this mask painted over it in a land colour (via CSS mask-image). Lakes read
as sea because they're transparent in the mask. This lets both land and sea
shades be tuned live in the browser without regenerating anything.

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
from PIL import Image

OUT = Path(__file__).resolve().parent.parent / "web" / "static" / "landmask.png"
DPI = 100


def native_polygons(category: str, name: str, resolution: str = "50m"):
    from mepscloud import config
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
    vertices, codes = [], []
    for ring in [poly.exterior, *poly.interiors]:
        lon, lat = np.asarray(ring.coords).T
        xs, ys = transformer.transform(lon, lat)
        pts = np.column_stack([xs, ys])
        vertices.append(pts)
        codes.append([MplPath.MOVETO] + [MplPath.LINETO] * (len(pts) - 2) + [MplPath.CLOSEPOLY])
    return MplPath(np.concatenate(vertices), np.concatenate(codes))


def main():
    from mepscloud import fetch
    run_path = fetch.latest_cached_run_path()
    if run_path is None:
        raise SystemExit("no cached run npz found -- need real x/y arrays to size this exactly")
    with np.load(run_path) as z:
        x, y = z["x"], z["y"]
    nx, ny = len(x), len(y)
    extent = [x.min(), x.max(), y.min(), y.max()]
    print(f"[landmask] grid {nx}x{ny}, extent={extent}")

    print("[landmask] projecting land polygons...")
    land_paths = native_polygons("physical", "land")
    print("[landmask] projecting lakes (finer 10m -- Finland is lake-dense)...")
    lake_paths = native_polygons("physical", "lakes", resolution="10m")

    # Render land white / lakes black on a black background (opaque), so the
    # resulting luminance is exactly the land coverage (with anti-aliased
    # coasts). Then turn that luminance into the alpha of a white image ->
    # a clean land mask that keeps soft edges. (Can't punch transparent holes
    # for lakes directly -- drawing "transparent" over white erases nothing --
    # so go via luminance-on-black instead.)
    fig = plt.figure(figsize=(nx / DPI, ny / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    ax.set_facecolor("black")
    fig.patch.set_facecolor("black")
    for path in land_paths:
        ax.add_patch(PathPatch(path, facecolor="white", edgecolor="none"))
    for path in lake_paths:
        ax.add_patch(PathPatch(path, facecolor="black", edgecolor="none"))

    fig.canvas.draw()
    rgb = np.asarray(fig.canvas.buffer_rgba())[..., :3]
    plt.close(fig)
    lum = rgb.mean(axis=2).astype(np.uint8)  # 255 = land, 0 = sea/lake

    mask = np.zeros((*lum.shape, 4), dtype=np.uint8)
    mask[..., :3] = 255      # white
    mask[..., 3] = lum       # alpha = land coverage
    OUT.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(mask, mode="RGBA").save(OUT)
    print(f"[landmask] wrote {OUT}  size={Image.open(OUT).size} (grid was {nx}x{ny})")


if __name__ == "__main__":
    main()
