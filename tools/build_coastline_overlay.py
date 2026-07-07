#!/usr/bin/env python3
"""One-off: render map reference layers (coastlines+borders, and one PNG per
road class) as transparent PNGs at the exact pixel grid of the MEPS native
domain, so the browser just stacks whichever it wants over the cloud frames
-- no per-update projection cost.

This is the ONLY place cartopy/pyproj/matplotlib are needed in this repo;
the periodic fetch+render pipeline never imports them. Re-run this only if
the grid definition changes (it won't -- MEPS's native grid is fixed).

Roads come from Natural Earth 10m, clipped to Finland's national polygon
(only Finnish roads are of interest); ferries are dropped (they're tagged
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

# figsize is (nx/DPI, ny/DPI) at DPI below, so 1 matplotlib point spans
# DPI/72 pixels; invert to specify line widths directly in target pixels.
DPI = 100
PX = 72.0 / DPI  # matplotlib-points per output pixel

# All map lines are 1px (matching the coastline weight); road class is
# conveyed by BRIGHTNESS (alpha) instead of width -- major brightest, then
# incrementally dimmer. Same warm tone for all; colour is a later tuning pass.
LINE_PX = 1 * PX
ROAD_COLOR = "#ffd27f"

# All road classes render into ONE roads.png (single viewer toggle); class is
# conveyed by alpha. (class name, alpha), dimmest first so the brighter major
# roads paint on top at junctions.
ROAD_CLASSES = [
    ("Unknown",           0.25),
    ("Road",              0.25),
    ("Secondary Highway", 0.50),
    ("Major Highway",     1.00),
]
FINLAND_BBOX = (19, 59, 32, 70.6)  # quick prefilter before the polygon clip


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


def _finland_polygon():
    """Finland's national land polygon (incl. Aland) from Natural Earth 10m,
    to clip roads to Finland only."""
    reader = shpreader.Reader(shpreader.natural_earth(
        resolution="10m", category="cultural", name="admin_0_countries"))
    for rec in reader.records():
        if rec.attributes.get("ADMIN") == "Finland" or rec.attributes.get("NAME") == "Finland":
            return rec.geometry
    raise RuntimeError("Finland not found in admin_0_countries")


def road_lines_by_class(bbox):
    """{class_name: [(xs, ys), ...]} for non-ferry roads clipped to Finland's
    polygon. Natural Earth roads is a global ~56k-feature set; a cheap bbox
    prefilter avoids running the (much costlier) polygon intersection on every
    feature worldwide."""
    finland = _finland_polygon()
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
        clipped = rec.geometry.intersection(finland)  # keep only the part in Finland
        if clipped.is_empty:
            continue
        for part in _parts(clipped):
            if part.geom_type == "LineString" and len(part.coords) >= 2:
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
    """layers = [(lines, color, lw, alpha), ...]; drawn in order (first = bottom)."""
    fig = plt.figure(figsize=(nx / DPI, ny / DPI), dpi=DPI)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for lines, color, lw, alpha in layers:
        for xs, ys in lines:
            ax.plot(xs, ys, color=color, lw=lw, alpha=alpha, solid_capstyle="round")
    fig.savefig(out_path, dpi=DPI, transparent=True)
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
        [(coast, "#00e5ff", LINE_PX, 1.0), (borders, "#ffee00", LINE_PX, 1.0)],
        STATIC / "overlay.png", nx, ny, extent,
    )

    print("[overlay] roads (merged into one PNG, clipped to Finland, ferries dropped)...")
    by_class = road_lines_by_class(FINLAND_BBOX)
    road_layers = []
    for cls, alpha in ROAD_CLASSES:
        lines = by_class[cls]
        print(f"[overlay]   {cls}: {len(lines)} segments (alpha {alpha})")
        road_layers.append((lines, ROAD_COLOR, LINE_PX, alpha))
    render_png(road_layers, STATIC / "roads.png", nx, ny, extent)


if __name__ == "__main__":
    main()
