#!/usr/bin/env python3
"""One-off: render coastline/border/road overlays at ZOOM_K x the native grid
resolution, for use ONLY while the map is zoomed (see web/index.html's
ZOOM_K and applyOverlayAsset) -- the low-res web/static/overlay.png/roads.png
stay in use whenever the map isn't zoomed, since a hi-res image downsampled
back to on-screen size looks worse than one purpose-built for that size.

Line width is fixed at LINE_PX_HIRES (3 output pixels in the 5x-resolution
image): picked by live comparison against 5px (proportionally the same as
the low-res look, just crisp) and 1px (the low-res line's own absolute
width, relatively thinner once the canvas is 5x bigger) -- 3px was the
chosen middle ground.

Usage (from a venv with cartopy/pyproj/matplotlib/netCDF4/requests installed,
e.g. a WSL venv -- see tools/requirements.txt):
    python tools/build_hires_overlay.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))          # tools/ itself
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))   # repo root

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

import build_coastline_overlay as bco
from mepscloud import fetch

ZOOM_K = 5           # locked value, see web/index.html
LINE_PX_HIRES = 3    # locked value, chosen by live 5px/3px/1px comparison
STATIC = Path(__file__).resolve().parent.parent / "web" / "static"


def render_png_at(layers, out_path: Path, nx: int, ny: int, extent, dpi: float):
    """Like build_coastline_overlay.render_png, but with an explicit dpi
    (that function hardcodes the module's base DPI=100)."""
    fig = plt.figure(figsize=(nx / bco.DPI, ny / bco.DPI), dpi=dpi)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_axis_off()
    fig.patch.set_alpha(0)
    ax.patch.set_alpha(0)
    for lines, color, lw, alpha in layers:
        for xs, ys in lines:
            ax.plot(xs, ys, color=color, lw=lw, alpha=alpha, solid_capstyle="round")
    fig.savefig(out_path, dpi=dpi, transparent=True)
    plt.close(fig)
    got = Image.open(out_path).size
    want = (nx * ZOOM_K, ny * ZOOM_K)
    assert got == want, f"size mismatch: got {got}, want {want}"
    print(f"[hires] wrote {out_path.name}  {got[0]}x{got[1]}  ({out_path.stat().st_size / 1e6:.1f} MB)")


def main():
    run_path = fetch.latest_cached_run_path()
    if run_path is None:
        raise SystemExit("no cached run npz found -- need real x/y arrays to size these exactly")
    with np.load(run_path) as z:
        x, y = z["x"], z["y"]
    nx, ny = len(x), len(y)
    extent = [x.min(), x.max(), y.min(), y.max()]
    hires_dpi = bco.DPI * ZOOM_K
    lw = bco.LINE_PX * (LINE_PX_HIRES / ZOOM_K)
    print(f"[hires] base grid {nx}x{ny}, ZOOM_K={ZOOM_K} -> {nx * ZOOM_K}x{ny * ZOOM_K} "
          f"@ dpi={hires_dpi}, lw={lw:.4f}pt ({LINE_PX_HIRES}px)")
    STATIC.mkdir(parents=True, exist_ok=True)

    print("[hires] fetching coastline/border/road geometry (cached after first run)...")
    coast = bco.native_lines("physical", "coastline")
    borders = bco.native_lines("cultural", "admin_0_boundary_lines_land")
    by_class = bco.road_lines_by_class(bco.FINLAND_BBOX)

    render_png_at(
        [(coast, bco.COAST_COLOR, lw, 1.0), (borders, bco.BORDER_COLOR, lw, 1.0)],
        STATIC / "overlay_hires.png", nx, ny, extent, hires_dpi,
    )
    road_layers = [(by_class[cls], bco.ROAD_COLOR, lw, alpha) for cls, alpha in bco.ROAD_CLASSES]
    render_png_at(road_layers, STATIC / "roads_hires.png", nx, ny, extent, hires_dpi)

    print(f"[hires] done -> {STATIC}")


if __name__ == "__main__":
    main()
