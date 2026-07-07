"""Fetch and cache the newest MEPS deterministic run from MET Norway's
THREDDS/OPeNDAP service.

Why MET Norway instead of FMI's WFS grid query (the prototype's original
source): FMI resamples the native Lambert grid onto a padded regular lat/lon
grid server-side, and large requests get *silently truncated* (fewer rows
than requested, no error) rather than erroring -- verified by comparing wide-
vs-narrow bbox probes during design. MET Norway serves the native, genuinely
rectangular Lambert grid (fixed 2500m pitch, no NaN padding) as standard
CF NetCDF over OPeNDAP, with real server-side subsetting and cloud fractions
already normalised to 0-1 (except convective_cloud_area_fraction, still %).

Memory note: the raw float32 data for all cloud vars x all forecast hours is
~2GB, too much to hold as float32 on a small VPS. Fetch is streamed one
variable at a time (each var's full time series, ~270MB in flight, then
quantized and released) rather than loading everything at once. Quantized
storage (uint8 for fractions, uint16 metres for cloud base/top) keeps the
on-disk cache under 1GB for the full run.
"""
from __future__ import annotations

import datetime as dt
import xml.etree.ElementTree as ET
from pathlib import Path

import netCDF4
import numpy as np
import requests

from . import config

_THREDDS_CATALOG_NS = "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"


def _catalog_run_paths(date: dt.date) -> list[str]:
    """urlPaths of meps_det_sfc_*.ncml files for a UTC date, newest first."""
    url = f"{config.THREDDS_BASE}/catalog/meps25epsarchive/{date:%Y/%m/%d}/catalog.xml"
    resp = requests.get(url, timeout=30)
    if resp.status_code != 200:
        return []
    root = ET.fromstring(resp.content)
    paths = [
        el.attrib["urlPath"]
        for el in root.iter(f"{{{_THREDDS_CATALOG_NS}}}dataset")
        if "urlPath" in el.attrib and config.MEPS_DET_SFC_PREFIX in el.attrib["urlPath"]
    ]
    return sorted(paths, reverse=True)


def latest_run_url() -> tuple[str, dt.datetime]:
    """OPeNDAP URL + run time (UTC) for the newest available deterministic
    run. Checks today, falling back to yesterday for the window right after
    UTC midnight before today's first run has landed."""
    now = dt.datetime.now(dt.UTC)
    for date in (now.date(), now.date() - dt.timedelta(days=1)):
        paths = _catalog_run_paths(date)
        if paths:
            newest = paths[0]
            # e.g. meps25epsarchive/2026/07/07/meps_det_sfc_20260707T15Z.ncml
            stamp = newest.rsplit("_", 1)[-1].removesuffix(".ncml")  # "20260707T15Z"
            run_time = dt.datetime.strptime(stamp, "%Y%m%dT%HZ").replace(tzinfo=dt.UTC)
            return f"{config.THREDDS_BASE}/dodsC/{newest}", run_time
    raise RuntimeError("no MEPS deterministic run found in the THREDDS catalog "
                        "for today or yesterday")


def cache_path(run_time: dt.datetime) -> Path:
    return config.CACHE_DIR / f"run_{run_time:%Y%m%dT%H%MZ}.npz"


def _quantize_fraction(arr: np.ndarray) -> np.ndarray:
    """0-1 cloud fraction -> uint8 0-255 (NaN -> 0, i.e. treated as clear)."""
    return np.clip(np.round(np.nan_to_num(arr, nan=0.0) * 255), 0, 255).astype(np.uint8)


def _quantize_metres(arr: np.ndarray) -> np.ndarray:
    """Cloud base/top altitude in metres -> uint16 (NaN -> 0 = no cloud base/top)."""
    return np.clip(np.nan_to_num(arr, nan=0.0), 0, 65535).astype(np.uint16)


def run_meta(ds: netCDF4.Dataset):
    """(x, y, valid_times) for an open run dataset."""
    x = np.asarray(ds.variables["x"][:], dtype=np.float32)
    y = np.asarray(ds.variables["y"][:], dtype=np.float32)
    time_var = ds.variables["time"]
    valid_times = netCDF4.num2date(
        time_var[:], time_var.units,
        only_use_cftime_datetimes=False, only_use_python_datetimes=True,
    )
    return x, y, valid_times


def iter_quantized_variables(ds: netCDF4.Dataset):
    """Yield (name, quantized_array) for each cloud variable, one at a time.

    This is the memory-bounding primitive the whole pipeline is built on:
    callers (npz caching, PNG rendering) consume and discard each variable's
    array before the next is fetched, so peak memory stays ~one variable's
    array (~270MB raw float32 in flight, briefly) regardless of how many
    variables or forecast hours are pulled -- instead of ~2GB for all 8 at
    once. See module docstring.
    """
    for name in config.CLOUD_VARS_FRACTION:
        print(f"[fetch]   {name}")
        var = ds.variables[name]
        # netCDF4 returns a MaskedArray for vars with _FillValue; np.asarray()
        # on that silently leaks the raw fill sentinel (~9.97e36) through
        # instead of NaN, which would then get clamped into range by the
        # quantizer (e.g. "fully cloudy") instead of "no data" -> 0.
        raw = np.ma.filled(var[:, 0, :, :], np.nan).astype(np.float32)
        if str(getattr(var, "units", "")) == "%":
            raw /= 100.0
        yield name, _quantize_fraction(raw)
        del raw
    for name in config.CLOUD_VARS_METRES:
        print(f"[fetch]   {name}")
        raw = np.ma.filled(ds.variables[name][:, 0, :, :], np.nan).astype(np.float32)
        yield name, _quantize_metres(raw)
        del raw


def fetch_latest_run(force: bool = False) -> Path:
    """Fetch the newest run's full forecast horizon for all cloud variables,
    quantize, and write one npz to the cache. No-ops (returns the existing
    path) if that run is already cached, unless force=True.

    This keeps the full quantized array set on disk, which is handy for
    local dev/inspection -- the production pipeline (render.py) doesn't use
    this, it streams straight from OPeNDAP to PNGs without ever writing the
    combined array set to disk. See render.render_latest_run().
    """
    url, run_time = latest_run_url()
    path = cache_path(run_time)
    if path.exists() and not force:
        print(f"[fetch] run {run_time.isoformat()} already cached -> {path}")
        return path

    print(f"[fetch] opening {url}")
    ds = netCDF4.Dataset(url)
    try:
        x, y, valid_times = run_meta(ds)
        print(f"[fetch] run {run_time.isoformat()} | grid {len(y)}x{len(x)} | "
              f"{len(valid_times)} forecast steps ({valid_times[0].isoformat()} .. "
              f"{valid_times[-1].isoformat()})")
        out = dict(iter_quantized_variables(ds))
    finally:
        ds.close()

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        run_time=run_time.isoformat(),
        valid_times=np.array([t.isoformat() for t in valid_times]),
        x=x, y=y,
        **out,
    )
    print(f"[fetch] wrote {path} ({path.stat().st_size / 1e6:.1f} MB)")
    _prune_old_runs(keep=path)
    return path


def _prune_old_runs(keep: Path):
    for p in config.CACHE_DIR.glob("run_*.npz"):
        if p != keep:
            p.unlink(missing_ok=True)


def latest_cached_run_path() -> Path | None:
    paths = sorted(config.CACHE_DIR.glob("run_*.npz"))
    return paths[-1] if paths else None
