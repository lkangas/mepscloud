#!/usr/bin/env python3
"""One-off: render coastline/border/road overlays at ZOOM_K x the native grid
resolution, in TWO line-width variants, to compare before picking one to
actually use for the (now-locked, see web/index.html's ZOOM_K) 5x map zoom.

Variant A ("scaled"): line width in POINTS unchanged from the base render, so
at ZOOM_K x the dpi the line is ZOOM_K x as many PIXELS -- the same
PROPORTION of the image width as today's low-res lines, just with real
anti-aliased detail instead of CSS-upscaled blur. Looks like the current
zoomed view, but crisp.

Variant B ("fixed"): line width in POINTS divided by ZOOM_K, so the ABSOLUTE
pixel width stays ~1px (matching today's un-zoomed rendering) even inside the
now-K-times-bigger image -- a smaller FRACTION of the image, so relatively
THINNER once displayed at the same size as variant A.

Writes to web/static/hires_test/ (NOT web/static/ -- doesn't touch the live
overlay.png/roads.png) so both can be compared before deciding.

Usage (from a venv with cartopy/pyproj/matplotlib/netCDF4/requests installed):
    python tools/build_hires_overlay_compare.py
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

ZOOM_K = 5   # locked value, see web/index.html
OUT = Path(__file__).resolve().parent.parent / "web" / "static" / "hires_test"


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
    print(f"[hires] base grid {nx}x{ny}, ZOOM_K={ZOOM_K} -> {nx*ZOOM_K}x{ny*ZOOM_K} @ dpi={hires_dpi}")
    OUT.mkdir(parents=True, exist_ok=True)

    print("[hires] fetching coastline/border/road geometry (cached after first run)...")
    coast = bco.native_lines("physical", "coastline")
    borders = bco.native_lines("cultural", "admin_0_boundary_lines_land")
    by_class = bco.road_lines_by_class(bco.FINLAND_BBOX)

    # lw_points for a TARGET output pixel width w (at hires_dpi): w * 72/hires_dpi,
    # equivalently bco.LINE_PX * (w / ZOOM_K) since bco.LINE_PX is the 1px-at-base-dpi
    # points value. w=ZOOM_K (5px) = variant A; w=1 (1px) = variant B; w=3 is the
    # requested middle ground.
    def lw_for_px(w):
        return bco.LINE_PX * (w / ZOOM_K)

    variants = {
        "A_scaled": lw_for_px(ZOOM_K),   # 5px -- same points as base -> proportionally same, crisper
        "B_fixed":  lw_for_px(1),        # 1px -- same ABSOLUTE px as base -> relatively thinner
        "C_mid":    lw_for_px(3),        # 3px -- midpoint between A and B
    }
    for tag, lw in variants.items():
        print(f"[hires] variant {tag}: lw={lw:.4f}pt")
        render_png_at(
            [(coast, bco.COAST_COLOR, lw, 1.0), (borders, bco.BORDER_COLOR, lw, 1.0)],
            OUT / f"overlay_{tag}.png", nx, ny, extent, hires_dpi,
        )
        road_layers = [(by_class[cls], bco.ROAD_COLOR, lw, alpha) for cls, alpha in bco.ROAD_CLASSES]
        render_png_at(road_layers, OUT / f"roads_{tag}.png", nx, ny, extent, hires_dpi)

    print(f"[hires] done -> {OUT}")


if __name__ == "__main__":
    main()
