"""Stream the newest MEPS run straight to per-timestep PNG frames -- no
cartopy, no matplotlib, no map projection at render time (the grid is
already a native-pixel rectangle, see config.py), and no giant combined
array ever written to disk (see fetch.iter_quantized_variables). Just:
quantized array -> flip north-up -> invert for "light=clear" -> PNG.

A separate one-time asset (tools/build_coastline_overlay.py) is stacked on
top client-side; this module has no coastline/projection dependency at all.
"""
from __future__ import annotations

import datetime as dt
import json
import shutil
from pathlib import Path

import netCDF4
import numpy as np
from PIL import Image

from . import config, fetch

# uint16 metres -> 0-255 display range. Real cloud tops observed up to
# ~13.3km; pad a bit above that rather than clip real data.
ALT_DISPLAY_MAX_M = 14000


def _to_display_png(arr2d: np.ndarray, is_metres: bool) -> Image.Image:
    """quantized array -> north-up, inverted (light=clear, dark=cloud/high) L-mode PNG."""
    if is_metres:
        scaled = np.clip(arr2d.astype(np.float32) / ALT_DISPLAY_MAX_M * 255, 0, 255)
        u8 = scaled.astype(np.uint8)
    else:
        u8 = arr2d
    inverted = 255 - u8          # 0 (clear/no data) -> white, max -> black
    north_up = np.flipud(inverted)  # y is stored ascending (south->north); images want row0=north
    return Image.fromarray(north_up, mode="L")


def _frames_dir(run_time: dt.datetime) -> Path:
    return config.CACHE_DIR / "frames" / f"{run_time:%Y%m%dT%H%MZ}"


def render_latest_run(force: bool = False) -> dict:
    """Fetch (streamed) and render the newest run to PNG frames + manifest.json.
    Skips entirely (returns the existing manifest) if that run is already
    rendered, unless force=True."""
    url, run_time = fetch.latest_run_url()
    out_dir = _frames_dir(run_time)
    manifest_path = config.CACHE_DIR / "manifest.json"

    if not force and manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if existing.get("run_utc") == run_time.isoformat():
            print(f"[render] run {run_time.isoformat()} already rendered -> {manifest_path}")
            return existing

    print(f"[render] opening {url}")
    ds = netCDF4.Dataset(url)
    try:
        x, y, valid_times = fetch.run_meta(ds)
        n_time = len(valid_times)
        print(f"[render] run {run_time.isoformat()} | grid {len(y)}x{len(x)} | "
              f"{n_time} forecast steps")

        out_dir.mkdir(parents=True, exist_ok=True)
        layers = []
        for name, quantized in fetch.iter_quantized_variables(ds):
            is_metres = name in config.CLOUD_VARS_METRES
            var_dir = out_dir / name
            var_dir.mkdir(exist_ok=True)
            for ti in range(n_time):
                img = _to_display_png(quantized[ti], is_metres)
                img.save(var_dir / f"{ti:03d}.png", optimize=True)
            layers.append(name)
            del quantized
            print(f"[render]   {name}: {n_time} frames -> {var_dir}")
    finally:
        ds.close()

    manifest = {
        "run_utc": run_time.isoformat(),
        "valid_times_utc": [t.isoformat() for t in valid_times],
        "grid": {"nx": len(x), "ny": len(y),
                 "x_min": float(x.min()), "x_max": float(x.max()),
                 "y_min": float(y.min()), "y_max": float(y.max())},
        "layers": layers,
        "frame_url_template": f"frames/{run_time:%Y%m%dT%H%MZ}/{{layer}}/{{step:03d}}.png",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"[render] wrote {manifest_path}")
    _prune_old_frame_dirs(keep=out_dir)
    return manifest


def _prune_old_frame_dirs(keep: Path):
    frames_root = config.CACHE_DIR / "frames"
    if not frames_root.exists():
        return
    for p in frames_root.iterdir():
        if p.is_dir() and p != keep:
            shutil.rmtree(p, ignore_errors=True)
