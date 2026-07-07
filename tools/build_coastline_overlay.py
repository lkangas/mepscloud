#!/usr/bin/env python3
"""One-off: render map reference layers (coastlines+borders, and one PNG per
road class) as transparent PNGs at the exact pixel grid of the MEPS native
domain, so the browser just stacks whichever it wants over the cloud frames
-- no per-update projection cost.

This is the ONLY place cartopy/pyproj/matplotlib are needed in this repo;
the periodic fetch+render pipeline never imports them. Re-run this only if
the grid definition changes (it won't -- MEPS's native grid is fixed).

Roads come from Natural Earth 10m; ferries are dropped (they're tagged
featurecla='Ferry' and otherwise draw as lines across open sea). Each road
class (Natural Earth's `type`: Major Highway / Secondary Highway / Road /
Unknown) is written to its own PNG so the viewer can toggle them separately.

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

STATIC = Path(__file__).resolve().parent.parent / "web" / "static"

# Road classes to split into separate toggleable PNGs, in draw/legend order
# (most to least prominent), with per-class line width + a starting colour.
# All the same warm tone for now -- colour tuning is a later pass.
ROAD_CLASSES = [
    ("Major Highway",     "roads_major.png",     1.1, "#e8b060"),
    ("Secondary Highway", "roads_secondary.png", 0.7, "#e0c080"),
    ("Road",              "roads_road.png",      0.5, "#d8c8a0"),
    ("Unknown",           "roads_unknown.png",   0.4, "#c8c0a8"),
]
ROADS_BBOX = (-20, 49, 56, 76)  # generous, whole map domain not just Finland


def _transformer():
    return Transformer.from_crs("EPSG:4326", config.NATIVE_PROJ4, always_xy=True)


def native_lines(category: str, name: str, resolution: str = "50m"):
    reader = shpreader.Reader(shpreader.natural_earth(resolution=resolution, category=category, name=name))
    to_native = _transformer()
    lines = []
    for rec in reader.records():
        for part in _parts(rec.geometry):
            lines.append(_project(part, to_native))
    return lines


def road_lines_by_class(bbox):
    """{class_name: [(xs, ys), ...]} for non-ferry roads whose centroid is in
    bbox. Natural Earth roads is a global ~56k-feature set, so filter."""
    reader = shpreader.Reader(shpreader.natural_earth(resolution="10m", category="cultural", name="roads"))
    to_native = _transformer()
    out = {name: [] for name, *_ in ROAD_CLASSES}
    dropped_ferry = 0
    for rec in reader.records():
        attrs = rec.attributes
        if attrs.get("featurecla") == "Ferry":
            dropped_ferry += 1
            continue
        cls = attrs.get("type")
        if cls not in out:
            continue
        c = rec.geometry.centroid
        if not (bbox[0] <= c.x <= bbox[2] and bbox[1] <= c.y <= bbox[3]):
            continue
        for part in _parts(rec.geometry):
            out[cls].append(_project(part, to_native))
    print(f"[overlay]   dropped {dropped_ferry} ferry features")
    return out


def _parts(geom):
    return geom.geoms if hasattr(geom, "geoms") else [geom]


def _project(part, to_native):
    lon, lat = np.asarray(part.coords).T
    xs, ys = to_native.transform(lon, lat)
    return xs, ys


def render_png(layers, out_path, nx, ny, extent):
    """layers = [(lines, color, lw), ...]; drawn in order (first = bottom)."""
    dpi = 100
    fig = plt.figure(figsize=(nx / dpi, ny / dpi), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for lines, color, lw in layers:
        for xs, ys in lines:
            ax.plot(xs, ys, color=color, lw=lw, solid_capstyle="round")
    fig.savefig(out_path, dpi=dpi, transparent=True)
    plt.close(fig)
    from PIL import Image
    assert Image.open(out_path).size == (nx, ny), "PNG size must match grid exactly"
    print(f"[overlay] wrote {out_path.name}")


def main():
    # grid extent comes from an actual cached run so every layer lines up
    # pixel-for-pixel with real frames (same x/y arrays fetch.py wrote).
    run_path = fetch.latest_cached_run_path()
    if run_path is None:
        raise SystemExit("no cached run npz found -- run fetch.fetch_latest_run() first "
                          "(need real x/y arrays to size these overlays exactly)")
    with np.load(run_path) as z:
        x, y = z["x"], z["y"]
    nx, ny = len(x), len(y)
    extent = [x.min(), x.max(), y.min(), y.max()]
    print(f"[overlay] grid {nx}x{ny}, extent={extent}")
    STATIC.mkdir(parents=True, exist_ok=True)

    print("[overlay] coastlines + borders...")
    coast = native_lines("physical", "coastline")
    borders = native_lines("cultural", "admin_0_boundary_lines_land")
    render_png(
        [(coast, "#00e5ff", 0.8), (borders, "#ffee00", 0.5)],
        STATIC / "overlay.png", nx, ny, extent,
    )

    print("[overlay] roads (one PNG per class, ferries dropped)...")
    by_class = road_lines_by_class(ROADS_BBOX)
    for cls, fname, lw, color in ROAD_CLASSES:
        lines = by_class[cls]
        print(f"[overlay]   {cls}: {len(lines)} segments")
        render_png([(lines, color, lw)], STATIC / fname, nx, ny, extent)


if __name__ == "__main__":
    main()
