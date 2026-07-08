#!/usr/bin/env python3
"""One-off, manually-triggered fetch + render of EVERY renderable MEPS
det_sfc product (see ../docs/meps_det_sfc-variables.md) as a simple
alpha-on-underlay layer, for exploring the raw data -- NOT the production
pipeline (mepscloud/), which only fetches/renders the curated cloud + precip
layers on a poll loop.

Unlike the production pipeline, this has no fixed physical range per variable
(0-1 fraction, a known altitude ceiling, ...) -- there isn't one that makes
sense across ~194 heterogeneous fields (temperature, pressure, wind,
radiation, ...). So each variable is auto-normalised to its own min/max
**across the whole run** (all 67 frames together, NOT per-frame -- per-frame
would make brightness incomparable across time) and alpha-encoded 0-255
against that: white RGB constant, alpha = the value's position in its own
run-wide range. Not comparable in absolute terms across variables/runs, but
fine for exploring one variable's spatial/temporal shape. The viewer
un-normalises back to real values (using each product's stored min/max) for
the click-a-point meteogram.

"Renderable" = a plain (time, y, x) or (time, 1, y, x) field at the native
grid. Auto-excludes coordinate/dimension vars (1-D) and the one genuine
multi-level field, icing_index (10 levels) -- no hardcoded skip list needed,
just a shape check against the run's (nt, ny, nx).

Robust + resumable for an unattended run: chunked OPeNDAP reads with per-chunk
retry; each variable wrapped in try/except so one failure is logged and
skipped, not fatal; the manifest is rewritten after every variable (atomic),
so a crash leaves a valid partial manifest AND a re-run skips variables whose
frames are already complete. No fetch/render status tracking (manual, not a
poll loop) -- just stdout progress.

Local-only: writes explore/web/cache/ (gitignored) and copies the land/sea +
coastline underlay assets into explore/web/static/. Nothing deployed.

Usage:
    python explore/fetch_render.py            # fetch + render every renderable variable
    python explore/fetch_render.py --limit 10  # just the first 10 renderable, for a smoke test
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

import netCDF4
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mepscloud import fetch

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "web" / "cache"
STATIC_DST = HERE / "web" / "static"
STATIC_SRC = HERE.parent / "web" / "static"
READ_CHUNK = 12   # timesteps per OPeNDAP request (bounded size -> robust; memory isn't the constraint locally)


def is_renderable(var, nt: int, ny: int, nx: int) -> bool:
    """A plain single-level field at the native grid: (time, y, x) or
    (time, 1, y, x). Excludes 1-D coordinate vars and multi-level fields
    (e.g. icing_index, whose level dim is 10 not 1)."""
    s = var.shape
    if var.ndim == 3:
        return s == (nt, ny, nx)
    if var.ndim == 4:
        return s[0] == nt and s[1] == 1 and s[2] == ny and s[3] == nx
    return False


def read_var_chunked(var, nt: int, ny: int, nx: int, retries: int = 3) -> np.ndarray:
    """Full (nt, ny, nx) float32, read in READ_CHUNK-timestep slices with a
    short retry per slice (OPeNDAP can hiccup over a long run). Masked/fill
    values -> NaN."""
    out = np.empty((nt, ny, nx), dtype=np.float32)
    for lo in range(0, nt, READ_CHUNK):
        hi = min(lo + READ_CHUNK, nt)
        for attempt in range(retries):
            try:
                sl = var[lo:hi, 0, :, :] if var.ndim == 4 else var[lo:hi, :, :]
                out[lo:hi] = np.ma.filled(sl, np.nan).astype(np.float32)
                break
            except Exception:
                if attempt == retries - 1:
                    raise
                time.sleep(2 * (attempt + 1))
    return out


def quantize_auto(raw: np.ndarray):
    """(alpha uint8 [nt,ny,nx], vmin, vmax). alpha encodes the value's
    position in its own run-wide [vmin, vmax] as **1..255**, reserving alpha
    0 exclusively for no-data (NaN). This matters because otherwise a
    legitimate minimum value (position 0) would be indistinguishable from
    missing -- e.g. land_area_fraction is 0 over all the sea, which must read
    as the value 0, not "no data". The 1/255 shift is imperceptible. The
    viewer un-normalises: value = vmin + (alpha-1)/254 * (vmax-vmin), and
    treats alpha 0 as a gap. A constant field (vmax == vmin) is shown at full
    alpha where finite so it's visibly uniform rather than invisible."""
    finite_mask = np.isfinite(raw)
    if not finite_mask.any():
        return np.zeros(raw.shape, np.uint8), None, None
    finite = raw[finite_mask]
    vmin, vmax = float(finite.min()), float(finite.max())
    if vmax > vmin:
        frac = np.clip((raw - vmin) / (vmax - vmin), 0.0, 1.0)
        alpha = np.where(finite_mask, np.rint(frac * 254) + 1, 0).astype(np.uint8)
    else:
        alpha = np.where(finite_mask, 255, 0).astype(np.uint8)
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


def frames_complete(var_dir: Path, nt: int) -> bool:
    return var_dir.is_dir() and sum(1 for _ in var_dir.glob("[0-9][0-9][0-9].png")) >= nt


def write_manifest_atomic(doc: dict):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / "manifest.json"
    tmp = p.with_name("manifest.json.tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    for attempt in range(5):   # os.replace can transiently fail on Windows/OneDrive; retry
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.2 * (attempt + 1))


def copy_static_assets():
    STATIC_DST.mkdir(parents=True, exist_ok=True)
    for f in ("landmask.png", "overlay.png"):
        if (STATIC_SRC / f).exists():
            shutil.copy2(STATIC_SRC / f, STATIC_DST / f)
            print(f"[explore] copied underlay asset {f}")


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
    ap.add_argument("--limit", type=int, default=None, help="only the first N renderable variables (smoke test)")
    args = ap.parse_args()

    t_start = time.time()
    url, run_time = fetch.latest_run_url()
    print(f"[explore] run {run_time.isoformat()}  opening {url}")
    ds = netCDF4.Dataset(url)
    try:
        x, y, valid_times = fetch.run_meta(ds)
        nt, ny, nx = len(valid_times), len(y), len(x)
        print(f"[explore] grid {ny}x{nx}, {nt} forecast steps")
        out_dir = CACHE_DIR / "frames" / f"{run_time:%Y%m%dT%H%MZ}"
        out_dir.mkdir(parents=True, exist_ok=True)
        copy_static_assets()

        renderable = [n for n, v in ds.variables.items() if is_renderable(v, nt, ny, nx)]
        excluded = [{"key": n, "shape": list(v.shape)}
                    for n, v in ds.variables.items() if v.ndim >= 3 and not is_renderable(v, nt, ny, nx)]
        if args.limit:
            renderable = renderable[: args.limit]
        print(f"[explore] {len(renderable)} renderable variables"
              + (f" (limited to {args.limit})" if args.limit else "")
              + f", {len(excluded)} excluded (multi-level etc.)")

        # Resume: reuse already-complete variables from a prior partial run of
        # THIS run (frames present + metadata in the existing manifest), and
        # remember which ones were all-no-data so they aren't re-fetched.
        done, done_empty = {}, set()
        mpath = CACHE_DIR / "manifest.json"
        if mpath.exists():
            try:
                prev = json.loads(mpath.read_text(encoding="utf-8"))
                if prev.get("run_utc") == run_time.isoformat():
                    done = {p["key"]: p for p in prev.get("products", [])}
                    done_empty = {e["key"] for e in prev.get("empty", [])}
            except (OSError, json.JSONDecodeError):
                pass

        grid = {"nx": nx, "ny": ny, "x_min": float(x.min()), "x_max": float(x.max()),
                "y_min": float(y.min()), "y_max": float(y.max())}

        def manifest_doc(products, empty, failed):
            return {
                "run_utc": run_time.isoformat(),
                "valid_times_utc": [t.isoformat() for t in valid_times],
                "grid": grid,
                "frame_url_template": f"frames/{run_time:%Y%m%dT%H%MZ}/{{layer}}/{{step:03d}}.png",
                "products": products,
                "empty": empty,       # renderable but all-no-data this run (e.g. sea-ice temp in summer)
                "excluded": excluded,
                "failed": failed,
            }

        products, empty, failed = [], [], []
        for i, name in enumerate(renderable, 1):
            var = ds.variables[name]
            if name in done and frames_complete(out_dir / name, nt):
                products.append(done[name])
                print(f"[explore] ({i}/{len(renderable)}) {name}: already rendered, skipping")
                continue
            if name in done_empty:
                empty.append({"key": name, "label": str(getattr(var, "long_name", name)),
                              "group": "sfx" if name.startswith("SFX_") else "main"})
                print(f"[explore] ({i}/{len(renderable)}) {name}: all no-data (cached), skipping")
                continue
            t0 = time.time()
            print(f"[explore] ({i}/{len(renderable)}) {name} ...", end=" ", flush=True)
            try:
                raw = read_var_chunked(var, nt, ny, nx)
                alpha, vmin, vmax = quantize_auto(raw)
                del raw
                if vmin is None:   # no finite data anywhere this run -> list separately, don't render blank frames
                    empty.append({"key": name, "label": str(getattr(var, "long_name", name)),
                                  "group": "sfx" if name.startswith("SFX_") else "main"})
                    write_manifest_atomic(manifest_doc(products, empty, failed))
                    print("all no-data, listed as empty")
                    continue
                write_frames(name, alpha, out_dir)
                del alpha
                products.append({
                    "key": name,
                    "label": str(getattr(var, "long_name", name)),
                    "units": str(getattr(var, "units", "")),
                    "min": vmin, "max": vmax,
                    "constant": (vmin == vmax),
                    "group": "sfx" if name.startswith("SFX_") else "main",
                })
                write_manifest_atomic(manifest_doc(products, empty, failed))
                elapsed = time.time() - t0
                eta = (time.time() - t_start) / i * (len(renderable) - i) / 60
                print(f"done ({elapsed:.1f}s) range=[{vmin:.4g}, {vmax:.4g}]  ~{eta:.0f} min left")
            except Exception as e:   # one bad variable must not abort the whole run
                failed.append({"key": name, "error": repr(e)})
                write_manifest_atomic(manifest_doc(products, empty, failed))
                print(f"FAILED: {e}")
                traceback.print_exc()
    finally:
        ds.close()

    write_manifest_atomic(manifest_doc(products, empty, failed))
    if not args.limit and not failed:
        prune_old_runs(out_dir)
    mins = (time.time() - t_start) / 60
    print(f"[explore] DONE in {mins:.1f} min -- {len(products)} rendered, "
          f"{len(empty)} empty (all no-data), {len(failed)} failed, {len(excluded)} excluded")
    if empty:
        print("[explore] empty variables:", ", ".join(e["key"] for e in empty))
    if failed:
        print("[explore] failed variables:", ", ".join(f["key"] for f in failed))


if __name__ == "__main__":
    main()
