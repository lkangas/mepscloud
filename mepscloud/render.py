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
import os
import shutil
from pathlib import Path

import netCDF4
import numpy as np
from PIL import Image

from . import config, fetch

# uint16 metres -> 0-255 display range. Real cloud tops observed up to
# ~13.3km; pad a bit above that rather than clip real data.
ALT_DISPLAY_MAX_M = 14000

# ---------------------------------------------------------------------------
# "combined" derived layer: a custom cloud rendering built PURELY from the
# low/mid/high altitude bands (NOT the MEPS-served total -- the served total
# has cells where it's cloudy but nothing is classified into a band, which
# have no defined altitude-hue and would render as black). It (a) makes the
# clear -> few-percent-cloud edge conspicuous and (b) colour-codes altitude.
#
# Coverage from the three bands = 1-(1-low)(1-mid)(1-high) (random-overlap
# combine). Opacity = boosted transfer(coverage) (gamma < 1 lifts thin cloud).
# Hue = the low/mid/high colours mixed weighted by each layer's fraction
# RAISED TO A POWER, so the DOMINANT layer wins instead of everything
# averaging toward the warm low colour (a flat average lets white high cloud
# never show). Chosen by visual comparison (see session history): bright amber
# low, yellow mid, white high, power 4, gamma 0.42.
# Fog (surface obscuration) is a distinct phenomenon from the layered cloud
# bands, so it gets a cool violet off the warm altitude ramp -- it's the
# "total but no low/mid/high band" cloud (confirmed ~100% fog), astronomy-
# relevant, and would otherwise be invisible.
COMBINED_LOW_RGB = (255, 154, 46)    # #ff9a2e amber
COMBINED_MID_RGB = (255, 225, 77)    # #ffe14d yellow
COMBINED_HIGH_RGB = (255, 255, 255)  # white
COMBINED_FOG_RGB = (155, 107, 208)   # #9b6bd0 violet
COMBINED_POWER = 4.0
COMBINED_GAMMA = 0.42
COMBINED_DEAD = 0.01                 # <1% coverage reads as clear (fully transparent)
COMBINED_INPUTS = (
    "low_type_cloud_area_fraction",
    "medium_type_cloud_area_fraction",
    "high_type_cloud_area_fraction",
    "fog_area_fraction",
)


# Processing order = fetch order (raw MEPS vars) then the derived combined.
STATUS_PRODUCTS = list(config.CLOUD_VARS) + ["combined"]


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


class _Status:
    """Incremental fetch/process status for the viewer to poll (cache/
    status.json). Per product, per frame: 0=available, 1=fetched, 2=processed.
    Written atomically (temp + os.replace) so the viewer never reads a
    half-written file. Currently one run at a time (the pipeline renders one
    run and prunes the previous); the doc is a {"runs": [...]} list so the
    viewer can already handle several once we keep old+new during a handover."""

    def __init__(self, run_time: dt.datetime, n_frames: int, products):
        rt = run_time if run_time.tzinfo else run_time.replace(tzinfo=dt.UTC)
        self.run_utc = rt.isoformat()
        self.n_frames = n_frames
        self.products = list(products)
        self.states = {p: [0] * n_frames for p in self.products}
        self.fetched_at = {p: None for p in self.products}
        self._since_write = 0
        # Timestamped key points for the viewer's log (all UTC ISO).
        self.events = [{"label": "init available", "at": _now()}]
        self._fetch_done = False

    def mark_fetched(self, product: str):
        self.states[product] = [max(s, 1) for s in self.states[product]]
        self.fetched_at[product] = _now()
        # fetching = downloading the raw MEPS vars (combined is derived, not
        # fetched); complete once every raw var is in.
        if not self._fetch_done and all(self.fetched_at[p] for p in config.CLOUD_VARS):
            self._fetch_done = True
            self.events.append({"label": "fetch complete", "at": _now()})

    def mark_processed(self, product: str, ti: int):
        self.states[product][ti] = 2

    def mark_done(self):
        self.events.append({"label": "processing complete", "at": _now()})

    def _doc(self) -> dict:
        return {"runs": [{
            "run_utc": self.run_utc,
            "n_frames": self.n_frames,
            "products": self.products,
            "states": self.states,
            "fetched_at": self.fetched_at,
            "events": self.events,
        }]}

    def write(self):
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = config.CACHE_DIR / "status.json"
        tmp = p.with_name("status.json.tmp")
        tmp.write_text(json.dumps(self._doc()), encoding="utf-8")
        os.replace(tmp, p)
        self._since_write = 0

    def write_throttled(self, every: int = 4):
        """Write at most every `every` frames -- enough for a smooth grid
        animation without a filesystem write per frame."""
        self._since_write += 1
        if self._since_write >= every:
            self.write()


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


def _combined_rgba(low, mid, high, fog) -> np.ndarray:
    """One timestep of the combined layer -> north-up RGBA uint8, built from
    the three altitude bands plus surface fog. Inputs are uint8 (0-255) 2-D
    arrays, south->north (flipped at the end like _to_display_png)."""
    lo = low.astype(np.float32) / 255.0
    mi = mid.astype(np.float32) / 255.0
    hi = high.astype(np.float32) / 255.0
    fo = fog.astype(np.float32) / 255.0
    wl, wm, wh, wf = (lo ** COMBINED_POWER, mi ** COMBINED_POWER,
                      hi ** COMBINED_POWER, fo ** COMBINED_POWER)
    s = wl + wm + wh + wf + 1e-6
    rgb = (wl[..., None] * np.array(COMBINED_LOW_RGB, np.float32)
           + wm[..., None] * np.array(COMBINED_MID_RGB, np.float32)
           + wh[..., None] * np.array(COMBINED_HIGH_RGB, np.float32)
           + wf[..., None] * np.array(COMBINED_FOG_RGB, np.float32)) / s[..., None]
    coverage = 1.0 - (1.0 - lo) * (1.0 - mi) * (1.0 - hi) * (1.0 - fo)  # random-overlap
    t = np.clip((coverage - COMBINED_DEAD) / (1 - COMBINED_DEAD), 0, 1)
    alpha = t ** COMBINED_GAMMA
    rgba = np.empty((*lo.shape, 4), dtype=np.uint8)
    rgba[..., :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    rgba[..., 3] = np.clip(alpha * 255, 0, 255).astype(np.uint8)
    return np.flipud(rgba)


def _write_combined_frames(inputs: dict, out_dir: Path, on_frame=None) -> Path:
    """inputs maps COMBINED_INPUTS names -> full [t,ny,nx] uint8 arrays."""
    var_dir = out_dir / "combined"
    var_dir.mkdir(exist_ok=True)
    low = inputs["low_type_cloud_area_fraction"]
    mid = inputs["medium_type_cloud_area_fraction"]
    high = inputs["high_type_cloud_area_fraction"]
    fog = inputs["fog_area_fraction"]
    for ti in range(low.shape[0]):
        rgba = _combined_rgba(low[ti], mid[ti], high[ti], fog[ti])
        Image.fromarray(rgba, mode="RGBA").save(var_dir / f"{ti:03d}.png")
        if on_frame:
            on_frame(ti)
    return var_dir


def _frames_dir(run_time: dt.datetime) -> Path:
    return config.CACHE_DIR / "frames" / f"{run_time:%Y%m%dT%H%MZ}"


def _write_frames(name: str, quantized: np.ndarray, out_dir: Path, on_frame=None) -> Path:
    is_metres = name in config.CLOUD_VARS_METRES
    var_dir = out_dir / name
    var_dir.mkdir(exist_ok=True)
    for ti in range(quantized.shape[0]):
        img = _to_display_png(quantized[ti], is_metres)
        # optimize=True roughly triples PNG encode time across 500+ frames
        # for a modest size win -- not worth it, especially while iterating.
        img.save(var_dir / f"{ti:03d}.png")
        if on_frame:
            on_frame(ti)
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
        status = _Status(run_time, len(valid_times), STATUS_PRODUCTS)
        status.write()
        layers = []
        combined_inputs = {}  # kept in memory to build the derived layer after
        for name, quantized in fetch.iter_quantized_variables(ds):
            n_frames = quantized.shape[0]
            status.mark_fetched(name)  # the generator already downloaded it
            status.write()
            var_dir = _write_frames(name, quantized, out_dir,
                                    on_frame=lambda ti, n=name: (status.mark_processed(n, ti), status.write_throttled()))
            status.write()
            layers.append(name)
            if name in COMBINED_INPUTS:
                combined_inputs[name] = quantized  # ~68MB uint8 each, 4 kept
            else:
                del quantized
            print(f"[render]   {name}: {n_frames} frames -> {var_dir}")
        status.mark_fetched("combined")
        status.write()
        cdir = _write_combined_frames(combined_inputs, out_dir,
                                      on_frame=lambda ti: (status.mark_processed("combined", ti), status.write_throttled()))
        status.mark_done()
        status.write()
        print(f"[render]   combined: {len(valid_times)} frames -> {cdir}")
    finally:
        ds.close()

    layers = ["combined"] + layers  # derived layer first = viewer default
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
        status = _Status(run_time, len(valid_times), STATUS_PRODUCTS)
        status.write()
        layers = []
        for name in config.CLOUD_VARS:
            status.mark_fetched(name)  # data already local in the npz
            status.write()
            var_dir = _write_frames(name, z[name], out_dir,
                                    on_frame=lambda ti, n=name: (status.mark_processed(n, ti), status.write_throttled()))
            status.write()
            layers.append(name)
            print(f"[render]   {name}: {len(valid_times)} frames -> {var_dir}")
        status.mark_fetched("combined")
        status.write()
        cdir = _write_combined_frames({k: z[k] for k in COMBINED_INPUTS}, out_dir,
                                      on_frame=lambda ti: (status.mark_processed("combined", ti), status.write_throttled()))
        status.mark_done()
        status.write()
        print(f"[render]   combined: {len(valid_times)} frames -> {cdir}")

    layers = ["combined"] + layers  # derived layer first = viewer default
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
