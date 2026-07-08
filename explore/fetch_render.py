#!/usr/bin/env python3
"""One-off, manually-triggered fetch + render of EVERY MEPS det_sfc product
(see ../docs/meps_det_sfc-variables.md) as a simple alpha-on-underlay layer,
for exploring the raw data -- NOT the production pipeline (mepscloud/), which
only fetches/renders the curated cloud + precip layers on a poll loop.

Unlike the production pipeline, this has no fixed physical range per
variable (0-1 fraction, a known altitude ceiling, ...) -- there isn't one
that makes sense across ~194 heterogeneous fields (temperature, pressure,
wind, radiation, ...). So each variable is auto-normalised to its own
min/max across the whole fetched run and quantized to that: alpha = white
constant, "brightness" = value's position in ITS OWN range. Not comparable
in absolute terms across variables/runs, but fine for exploring one
variable's spatial/temporal shape, which is the point here. A future pass
could pin down real physical ranges and use proper colormaps (viridis,
turbo, ...) for variables worth looking at closely.

Confirmed once (see session notes): of the ~195 data variables, only
icing_index has genuine multi-level structure (10 levels) -- skipped. Every
other variable is a plain (time, [1], y, x) field, same shape the production
pipeline already handles, just with per-variable auto range instead of a
fixed one.

No fetch/render status tracking (this is manual, not a poll loop) -- just
stdout progress. No npz/local-dev fast path -- OPeNDAP only.

Usage:
    python explore/fetch_render.py            # fetch + render every variable
    python explore/fetch_render.py --limit 5   # just the first 5, for a quick smoke test
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import time
from pathlib import Path

import netCDF4
import numpy as np
from PIL import Image

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mepscloud import fetch

CACHE_DIR = Path(__file__).resolve().parent / "web" / "cache"

# Coordinate/metadata variables -- not real data fields, don't try to render them.
SKIP_VARS = {"x", "y", "time", "longitude", "latitude", "projection_lambert", "forecast_reference_time"}


def fetch_variable_full(var) -> np.ndarray:
    """Read one variable's whole time series in one shot (local/one-off, not
    memory-constrained like the VPS updater -- no need for fetch.py's
    FETCH_CHUNK streaming here). Squeezes the singleton level dim if present."""
    if var.ndim == 4:
        raw = var[:, 0, :, :]
    elif var.ndim == 3:
        raw = var[:, :, :]
    else:
        raise ValueError(f"unexpected ndim {var.ndim}, shape {var.shape}")
    return np.ma.filled(raw, np.nan).astype(np.float32)


def quantize_auto(raw: np.ndarray):
    """alpha = value's position in ITS OWN min-max range (see module
    docstring). NaN (fill/no-data) -> alpha 0 (fully transparent), not
    lumped in with the real minimum."""
    finite = raw[np.isfinite(raw)]
    alpha = np.zeros(raw.shape, dtype=np.uint8)
    if finite.size == 0:
        return alpha, 0.0, 0.0
    vmin, vmax = float(finite.min()), float(finite.max())
    if vmax > vmin:
        frac = (raw - vmin) / (vmax - vmin)
        frac = np.where(np.isfinite(raw), frac, -1.0)   # NaN -> negative -> clipped to 0 below
        alpha = np.clip(np.rint(np.clip(frac, 0.0, 1.0) * 255), 0, 255).astype(np.uint8)
    return alpha, vmin, vmax


def write_frames(name: str, alpha_stack: np.ndarray, out_dir: Path):
    var_dir = out_dir / name
    var_dir.mkdir(parents=True, exist_ok=True)
    for ti in range(alpha_stack.shape[0]):
        north_up = np.flipud(alpha_stack[ti])   # y stored south->north; images want row0=north
        la = np.empty((*north_up.shape, 2), dtype=np.uint8)
        la[..., 0] = 255       # constant white
        la[..., 1] = north_up  # alpha = value's position in its own range
        Image.fromarray(la, mode="LA").save(var_dir / f"{ti:03d}.png")


def prune_old_runs(keep_dir: Path):
    frames_root = keep_dir.parent
    if not frames_root.exists():
        return
    for p in frames_root.iterdir():
        if p.is_dir() and p != keep_dir:
            shutil.rmtree(p, ignore_errors=True)
            print(f"[explore] pruned old run dir {p.name}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=None, help="only fetch/render the first N variables (smoke test)")
    args = ap.parse_args()

    url, run_time = fetch.latest_run_url()
    print(f"[explore] run {run_time.isoformat()}  opening {url}")
    ds = netCDF4.Dataset(url)
    try:
        x, y, valid_times = fetch.run_meta(ds)
        print(f"[explore] grid {len(y)}x{len(x)}, {len(valid_times)} forecast steps")
        out_dir = CACHE_DIR / "frames" / f"{run_time:%Y%m%dT%H%MZ}"
        out_dir.mkdir(parents=True, exist_ok=True)

        names = [n for n in ds.variables if n not in SKIP_VARS]
        if args.limit:
            names = names[: args.limit]

        products, skipped = [], []
        for i, name in enumerate(names, 1):
            var = ds.variables[name]
            is_standard = var.ndim == 3 or (var.ndim == 4 and var.shape[1] == 1)
            if not is_standard:
                skipped.append({"key": name, "shape": list(var.shape)})
                print(f"[explore] ({i}/{len(names)}) SKIP {name}: shape {var.shape} (not a single-level field)")
                continue
            t0 = time.time()
            print(f"[explore] ({i}/{len(names)}) {name} ...", end=" ", flush=True)
            raw = fetch_variable_full(var)
            alpha, vmin, vmax = quantize_auto(raw)
            write_frames(name, alpha, out_dir)
            del raw, alpha
            products.append({
                "key": name,
                "label": str(getattr(var, "long_name", name)),
                "units": str(getattr(var, "units", "")),
                "min": vmin, "max": vmax,
                "group": "sfx" if name.startswith("SFX_") else "main",
            })
            print(f"done ({time.time() - t0:.1f}s)  range=[{vmin:.4g}, {vmax:.4g}]")

        manifest = {
            "run_utc": run_time.isoformat(),
            "valid_times_utc": [t.isoformat() for t in valid_times],
            "grid": {"nx": len(x), "ny": len(y),
                     "x_min": float(x.min()), "x_max": float(x.max()),
                     "y_min": float(y.min()), "y_max": float(y.max())},
            "frame_url_template": f"frames/{run_time:%Y%m%dT%H%MZ}/{{layer}}/{{step:03d}}.png",
            "products": products,
            "skipped": skipped,
        }
    finally:
        ds.close()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[explore] wrote manifest.json -- {len(products)} products rendered, {len(skipped)} skipped")
    if not args.limit:
        prune_old_runs(out_dir)


if __name__ == "__main__":
    main()
