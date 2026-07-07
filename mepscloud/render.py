"""Stream the newest MEPS run straight to per-timestep PNG frames -- no
cartopy, no matplotlib, no map projection at render time (the grid is
already a native-pixel rectangle, see config.py), and no giant combined
array ever written to disk (see fetch.iter_quantized_variables). Just:
quantized array -> flip north-up -> alpha=cloudiness, white RGB -> PNG.

Frames are meant to sit over the land/water basemap (tools/build_basemap.py):
clear sky is fully transparent, cloud shows as white at an opacity matching
its fraction, so basemap colour shows through wherever it's clear.

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
    """quantized array -> north-up LA (luminance+alpha) PNG: white at an
    opacity equal to cloudiness, so clear (0) is fully transparent."""
    if is_metres:
        alpha = np.clip(arr2d.astype(np.float32) / ALT_DISPLAY_MAX_M * 255, 0, 255).astype(np.uint8)
    else:
        alpha = arr2d
    north_up = np.flipud(alpha)  # y is stored ascending (south->north); images want row0=north
    la = np.empty((*north_up.shape, 2), dtype=np.uint8)
    la[..., 0] = 255       # constant white
    la[..., 1] = north_up  # alpha = cloudiness
    return Image.fromarray(la, mode="LA")


def _frames_dir(run_time: dt.datetime) -> Path:
    return config.CACHE_DIR / "frames" / f"{run_time:%Y%m%dT%H%MZ}"


def _write_frames(name: str, quantized: np.ndarray, out_dir: Path) -> Path:
    is_metres = name in config.CLOUD_VARS_METRES
    var_dir = out_dir / name
    var_dir.mkdir(exist_ok=True)
    for ti in range(quantized.shape[0]):
        img = _to_display_png(quantized[ti], is_metres)
        # optimize=True roughly triples PNG encode time across 500+ frames
        # for a modest size win -- not worth it, especially while iterating.
        img.save(var_dir / f"{ti:03d}.png")
    return var_dir


def _write_manifest(run_time: dt.datetime, valid_times, x, y, layers: list[str]) -> dict:
    manifest = {
        "run_utc": run_time.isoformat(),
        "valid_times_utc": [t.isoformat() for t in valid_times],
        "grid": {"nx": len(x), "ny": len(y),
                 "x_min": float(x.min()), "x_max": float(x.max()),
                 "y_min": float(y.min()), "y_max": float(y.max())},
        "layers": layers,
        "frame_url_template": f"frames/{run_time:%Y%m%dT%H%MZ}/{{layer}}/{{step:03d}}.png",
    }
    (config.CACHE_DIR / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


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
        print(f"[render] run {run_time.isoformat()} | grid {len(y)}x{len(x)} | "
              f"{len(valid_times)} forecast steps")

        out_dir.mkdir(parents=True, exist_ok=True)
        layers = []
        for name, quantized in fetch.iter_quantized_variables(ds):
            n_frames = quantized.shape[0]
            var_dir = _write_frames(name, quantized, out_dir)
            layers.append(name)
            del quantized
            print(f"[render]   {name}: {n_frames} frames -> {var_dir}")
    finally:
        ds.close()

    manifest = _write_manifest(run_time, valid_times, x, y, layers)
    print(f"[render] wrote {config.CACHE_DIR / 'manifest.json'}")
    _prune_old_frame_dirs(keep=out_dir)
    return manifest


def render_from_npz(npz_path: Path, force: bool = True) -> dict:
    """Fast local iteration path: re-render display PNGs from an already-
    cached quantized npz (see fetch.fetch_latest_run) instead of streaming
    from OPeNDAP again -- for tuning the display encoding (colour/alpha)
    without re-fetching data that hasn't changed."""
    with np.load(npz_path) as z:
        run_time = dt.datetime.fromisoformat(str(z["run_time"]))
        valid_times = [dt.datetime.fromisoformat(s) for s in z["valid_times"]]
        x, y = z["x"], z["y"]
        out_dir = _frames_dir(run_time)
        manifest_path = config.CACHE_DIR / "manifest.json"
        if not force and manifest_path.exists():
            existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing.get("run_utc") == run_time.isoformat():
                print(f"[render] run {run_time.isoformat()} already rendered -> {manifest_path}")
                return existing

        out_dir.mkdir(parents=True, exist_ok=True)
        layers = []
        for name in config.CLOUD_VARS:
            var_dir = _write_frames(name, z[name], out_dir)
            layers.append(name)
            print(f"[render]   {name}: {len(valid_times)} frames -> {var_dir}")

    manifest = _write_manifest(run_time, valid_times, x, y, layers)
    print(f"[render] wrote {config.CACHE_DIR / 'manifest.json'}")
    _prune_old_frame_dirs(keep=out_dir)
    return manifest


def _prune_old_frame_dirs(keep: Path):
    frames_root = config.CACHE_DIR / "frames"
    if not frames_root.exists():
        return
    for p in frames_root.iterdir():
        if p.is_dir() and p != keep:
            shutil.rmtree(p, ignore_errors=True)
