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
import time
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


# Processing order = fetch order (raw MEPS vars), the derived combined, then
# the precipitation overlay + its raw rain-rate layer (both derived from
# precip rate + type, see below).
STATUS_PRODUCTS = list(config.CLOUD_VARS) + ["combined", "precip", "rain_rate"]


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


try:
    from zoneinfo import ZoneInfo
    _LOCAL_TZ = ZoneInfo(config.LOCAL_TZ)
except Exception:  # pragma: no cover - missing tzdata
    _LOCAL_TZ = None


def _local_stamp() -> str:
    """'YYYY-MM-DD HH:MM:SS EEST' in Finnish local time for the log file."""
    now = dt.datetime.now(dt.UTC)
    if _LOCAL_TZ is not None:
        loc = now.astimezone(_LOCAL_TZ)
        return loc.strftime("%Y-%m-%d %H:%M:%S ") + loc.tzname()
    return now.strftime("%Y-%m-%d %H:%M:%SZ")


LOG_PATH = None  # set lazily to config.CACHE_DIR / "log.txt"
_LOG_MAX_LINES = 500


def _log_file() -> Path:
    return config.CACHE_DIR / "log.txt"


def log_line(msg: str):
    """Append a timestamped line to the cumulative processing log (cache/
    log.txt), which the viewer shows when the status log is clicked."""
    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _log_file().open("a", encoding="utf-8") as f:
        f.write(f"{_local_stamp()}  {msg}\n")


def _trim_log():
    p = _log_file()
    if not p.exists():
        return
    lines = p.read_text(encoding="utf-8").splitlines()
    if len(lines) > _LOG_MAX_LINES:
        p.write_text("\n".join(lines[-_LOG_MAX_LINES:]) + "\n", encoding="utf-8")


def _prev_status_runs(new_run_utc: str) -> list:
    """Runs from the existing status.json that are NOT the run about to be
    processed -- i.e. the previous (already-ready) run, to keep visible during
    a handover until the new one finishes."""
    p = config.CACHE_DIR / "status.json"
    if not p.exists():
        return []
    try:
        runs = json.loads(p.read_text(encoding="utf-8")).get("runs", [])
    except (OSError, json.JSONDecodeError):
        return []
    return [r for r in runs if r.get("run_utc") != new_run_utc]


class _Status:
    """Incremental fetch/process status for the viewer to poll (cache/
    status.json). Per product, per frame: 0=available, 1=fetched, 2=processed.
    Written atomically (temp + os.replace) so the viewer never reads a
    half-written file. Currently one run at a time (the pipeline renders one
    run and prunes the previous); the doc is a {"runs": [...]} list so the
    viewer can already handle several once we keep old+new during a handover."""

    def __init__(self, run_time: dt.datetime, n_frames: int, products, prev_runs=None):
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
        # Previous (already-ready) run(s), shown above this one during a
        # handover; cleared once this run finishes (mark_done) so the old run
        # then disappears (its frames are pruned at the same point).
        self.prev_runs = list(prev_runs or [])
        _trim_log()
        short = self.run_utc.replace("+00:00", "Z")
        log_line(f"run {short}: init available"
                 + (f" (handover; previous {prev_runs[0]['run_utc'].replace('+00:00','Z')} still live)"
                    if prev_runs else ""))

    def mark_fetched(self, product: str):
        self.states[product] = [max(s, 1) for s in self.states[product]]
        self.fetched_at[product] = _now()
        if product in config.CLOUD_VARS:
            log_line(f"  fetched {product}")
        # fetching = downloading the raw MEPS vars (combined is derived, not
        # fetched); complete once every raw var is in.
        if not self._fetch_done and all(self.fetched_at[p] for p in config.CLOUD_VARS):
            self._fetch_done = True
            self.events.append({"label": "fetch complete", "at": _now()})
            log_line("fetch complete — all variables downloaded")

    def mark_processed(self, product: str, ti: int):
        self.states[product][ti] = 2

    def log_processed(self, product: str):
        log_line(f"  processed {product} ({self.n_frames} frames)")

    def mark_done(self):
        self.events.append({"label": "processing complete", "at": _now()})
        log_line(f"run {self.run_utc.replace('+00:00', 'Z')}: processing complete — ready")
        self.prev_runs = []  # this run is ready; the old one is dropped now

    def _current(self) -> dict:
        return {
            "run_utc": self.run_utc,
            "n_frames": self.n_frames,
            "products": self.products,
            "states": self.states,
            "fetched_at": self.fetched_at,
            "events": self.events,
        }

    def _doc(self) -> dict:
        return {"runs": self.prev_runs + [self._current()]}

    def write(self):
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        p = config.CACHE_DIR / "status.json"
        tmp = p.with_name("status.json.tmp")
        tmp.write_text(json.dumps(self._doc()), encoding="utf-8")
        # os.replace can transiently fail on Windows (PermissionError) if
        # something else (OneDrive sync, an AV scanner, a local dev server)
        # briefly holds the destination open -- this is written many times
        # per run (write_throttled), so retry a few times with a short
        # backoff rather than aborting the whole render over a momentary lock.
        for attempt in range(5):
            try:
                os.replace(tmp, p)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.2 * (attempt + 1))
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


# ---------------------------------------------------------------------------
# "precip" derived overlay: weatherinfo.fi-style precipitation, drawn OVER the
# cloud layer. Intensity = the per-hour rate (fetch.iter_precip_frames diffs
# the accumulated total); phase = the model's precipitation_type category,
# mapped to one of three colour ramps sampled pixel-for-pixel from
# weatherinfo.fi's FMI-MEPS render (see tools/weatherinfo_precip_palette.json):
#   rain      (mm/h, 0..25): blue->green->yellow->red->magenta->white
#   freezing  (mm/h, 0..5):  violet ramp
#   snow      (cm/h, 0..15): light-cyan->deep-blue ramp
# precipitation_type has no "none" code, so wet/dry is decided by the rate
# (alpha), not the type. Snow is shown as depth: cm = mm_water * SLR/10.
PRECIP_RAIN_STOPS = (
    "#0189cc", "#005bb8", "#00868e", "#21b876", "#69dc67", "#d8f31c",
    "#faf505", "#faf20c", "#fdbd11", "#fe9d16", "#fe8d1a", "#fe7320",
    "#fe6324", "#ff5328", "#de371b", "#c1220f", "#de0d00", "#fe1216",
    "#fc1b4b", "#fb257f", "#f933d2", "#f840ff", "#f969ff", "#fba8ff",
    "#fdd0ff", "#fef9ff",
)
PRECIP_FRZ_STOPS = (
    "#dc88d2", "#bb89c1", "#9f78ac", "#9967a3", "#955b9e", "#914e97",
    "#8c4291", "#87338a", "#832683", "#7e1a7e", "#790b76", "#750071",
)
PRECIP_SNOW_STOPS = (
    "#19d0db", "#14c9d6", "#0dbdd0", "#05b2cb", "#00a8c5", "#009dbf",
    "#0093ba", "#008bb6", "#0080b0", "#0075ac", "#0065a7", "#0054a1",
    "#003196", "#001789", "#000c7f", "#010071",
)
PRECIP_RAIN_MAX = 25.0   # mm/h at the top of the rain ramp
PRECIP_FRZ_MAX = 5.0     # mm/h at the top of the freezing-rain ramp
PRECIP_SNOW_MAX = 15.0   # cm/h at the top of the snow ramp
PRECIP_SLR = 10.0        # snow-to-liquid ratio: snow_depth_cm = mm_water * SLR/10
PRECIP_DRY_LO = 0.05     # mm/h: below -> fully transparent (dry)
PRECIP_DRY_HI = 0.20     # mm/h: at/above -> full precip opacity (fade-in between)
PRECIP_ALPHA = 0.90      # opacity of established precip (radar look, slightly see-through)
PRECIP_SNOW_T_K = 273.65  # 2 m temp below which fill-phase precip is drawn as snow (~0.5C)


def _hex_rgb(h: str):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _build_lut(stops, n: int = 256) -> np.ndarray:
    """Linear-interpolate the low->high colour stops into an (n,3) uint8 LUT."""
    cols = np.array([_hex_rgb(s) for s in stops], dtype=np.float32)
    xs = np.linspace(0.0, 1.0, len(cols))
    grid = np.linspace(0.0, 1.0, n)
    lut = np.stack([np.interp(grid, xs, cols[:, c]) for c in range(3)], axis=1)
    return np.clip(np.rint(lut), 0, 255).astype(np.uint8)


PRECIP_RAIN_LUT = _build_lut(PRECIP_RAIN_STOPS)
PRECIP_FRZ_LUT = _build_lut(PRECIP_FRZ_STOPS)
PRECIP_SNOW_LUT = _build_lut(PRECIP_SNOW_STOPS)

# precipitation_type category -> ramp (metno code table 0..7):
#   0 drizzle, 1 rain                         -> rain
#   4 freezing drizzle, 5 freezing rain       -> freezing
#   2 sleet, 3 snow, 6 graupel, 7 hail        -> snow
_PRECIP_FRZ_CODES = (4, 5)
_PRECIP_SNOW_CODES = (2, 3, 6, 7)


def _lut_index(value: np.ndarray, vmax: float, n: int = 256) -> np.ndarray:
    pos = np.clip(value / vmax, 0.0, 1.0)
    return np.rint(pos * (n - 1)).astype(np.intp)


def _precip_phase_masks(ptype: np.ndarray, t2m: np.ndarray):
    """(is_rain, is_frz, is_snow) boolean masks from precipitation_type + the
    fill-phase temperature fallback (see fetch.iter_precip_frames). Shared by
    the display colourmap (_precip_rgba) and the raw rain-rate layer below."""
    unknown = ptype < 0  # fill: not classified at the tick -> decide by temp
    cold = np.asarray(t2m) < PRECIP_SNOW_T_K
    is_frz = np.isin(ptype, _PRECIP_FRZ_CODES)  # freezing only from explicit model codes
    is_snow = np.isin(ptype, _PRECIP_SNOW_CODES) | (unknown & cold)
    is_rain = ~(is_frz | is_snow)  # known rain/drizzle, plus warm-or-unknown fallback
    return is_rain, is_frz, is_snow


def _precip_rgba(rate: np.ndarray, ptype: np.ndarray, t2m: np.ndarray) -> np.ndarray:
    """One timestep of the precip overlay -> north-up RGBA uint8.
    rate: float32 [ny,nx] mm/h (per-hour water-equiv). ptype: int16 [ny,nx]
    precipitation_type category (fetch.iter_precip_frames; -1 = unknown/fill).
    t2m: float32 [ny,nx] 2 m temp (K) -- resolves rain vs snow for fill pixels,
    where the instantaneous type is undefined but the hour still accumulated."""
    rate = rate.astype(np.float32, copy=False)
    ny, nx = rate.shape
    is_rain, is_frz, is_snow = _precip_phase_masks(ptype, t2m)
    rgb = np.zeros((ny, nx, 3), dtype=np.uint8)
    ir = _lut_index(rate, PRECIP_RAIN_MAX)
    rgb[is_rain] = PRECIP_RAIN_LUT[ir[is_rain]]
    iz = _lut_index(rate, PRECIP_FRZ_MAX)
    rgb[is_frz] = PRECIP_FRZ_LUT[iz[is_frz]]
    isn = _lut_index(rate * (PRECIP_SLR / 10.0), PRECIP_SNOW_MAX)
    rgb[is_snow] = PRECIP_SNOW_LUT[isn[is_snow]]
    alpha = np.clip((rate - PRECIP_DRY_LO) / (PRECIP_DRY_HI - PRECIP_DRY_LO), 0.0, 1.0) * PRECIP_ALPHA
    rgba = np.empty((ny, nx, 4), dtype=np.uint8)
    rgba[..., :3] = rgb
    rgba[..., 3] = np.clip(np.rint(alpha * 255), 0, 255).astype(np.uint8)
    return np.flipud(rgba)


def _rain_rate_la(rate: np.ndarray, ptype: np.ndarray, t2m: np.ndarray) -> np.ndarray:
    """One timestep of the RAW rain-rate layer -> north-up LA uint8, same
    encoding as the cloud fraction layers (alpha = quantized 0-1 fraction,
    constant white RGB -- see _to_display_png) so it doubles as a normal
    switchable layer button AND a meteogram pixel-readback source. This is
    needed because the display precip colourmap (_precip_rgba) is NOT
    invertible: its alpha only signals a wet/dry threshold (saturates at
    PRECIP_ALPHA for any rate above PRECIP_DRY_HI, no gradation above that),
    and its RGB colour would require guessing which of 3 phase LUTs a pixel
    came from -- the rain and snow ramps overlap almost exactly in the blue/
    cyan region (e.g. light rain and moderate snow can differ by an RGB
    distance of ~1/255), so a nearest-colour inverse would misclassify real
    values. This layer sidesteps that entirely: alpha directly IS
    rain_rate/PRECIP_RAIN_MAX, computed from the same rate/phase data at
    render time, no inversion needed. Zero where the phase isn't rain."""
    is_rain, _, _ = _precip_phase_masks(ptype, t2m)
    frac = np.where(is_rain, np.clip(rate / PRECIP_RAIN_MAX, 0.0, 1.0), 0.0)
    alpha = np.clip(np.rint(frac * 255), 0, 255).astype(np.uint8)
    la = np.empty((*alpha.shape, 2), dtype=np.uint8)
    la[..., 0] = 255
    la[..., 1] = alpha
    return np.flipud(la)


def _write_precip_frames(ds, out_dir: Path, on_precip=None, on_rain=None) -> tuple[Path, Path]:
    """Stream BOTH the precip overlay (display RGBA) and the raw rain-rate
    layer straight from the open OPeNDAP dataset, in one pass over
    fetch.iter_precip_frames (a generator -- can't be iterated twice)."""
    precip_dir = out_dir / "precip"
    precip_dir.mkdir(exist_ok=True)
    rain_dir = out_dir / "rain_rate"
    rain_dir.mkdir(exist_ok=True)
    for ti, rate, ptype, t2m in fetch.iter_precip_frames(ds):
        Image.fromarray(_precip_rgba(rate, ptype, t2m), mode="RGBA").save(precip_dir / f"{ti:03d}.png")
        if on_precip:
            on_precip(ti)
        Image.fromarray(_rain_rate_la(rate, ptype, t2m), mode="LA").save(rain_dir / f"{ti:03d}.png")
        if on_rain:
            on_rain(ti)
    return precip_dir, rain_dir


def _write_precip_frames_arrays(rate, ptype, t2m, out_dir: Path, on_precip=None, on_rain=None) -> tuple[Path, Path]:
    """Write both precip layers from full [t,ny,nx] arrays (local npz path)."""
    precip_dir = out_dir / "precip"
    precip_dir.mkdir(exist_ok=True)
    rain_dir = out_dir / "rain_rate"
    rain_dir.mkdir(exist_ok=True)
    for ti in range(rate.shape[0]):
        Image.fromarray(_precip_rgba(rate[ti], ptype[ti], t2m[ti]), mode="RGBA").save(precip_dir / f"{ti:03d}.png")
        if on_precip:
            on_precip(ti)
        Image.fromarray(_rain_rate_la(rate[ti], ptype[ti], t2m[ti]), mode="LA").save(rain_dir / f"{ti:03d}.png")
        if on_rain:
            on_rain(ti)
    return precip_dir, rain_dir


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


def _write_manifest(run_time: dt.datetime, valid_times, x, y, layers: list[str],
                    precip: bool = False) -> dict:
    manifest = {
        "run_utc": run_time.isoformat(),
        "valid_times_utc": [t.isoformat() for t in valid_times],
        "grid": {"nx": len(x), "ny": len(y),
                 "x_min": float(x.min()), "x_max": float(x.max()),
                 "y_min": float(y.min()), "y_max": float(y.max())},
        "layers": layers,
        # precip is an independent overlay (its own toggle, stacked above the
        # cloud layer), NOT one of the switchable base layers -- kept out of
        # `layers` so it never becomes a layer button. Shares the template.
        "precip_layer": "precip" if precip else None,
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
        # Handover: keep the previous run visible (and its frames + manifest
        # live, so the map keeps animating it) until this one is fully ready.
        prev_runs = _prev_status_runs(run_time.isoformat())
        status = _Status(run_time, len(valid_times), STATUS_PRODUCTS, prev_runs=prev_runs)
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
            status.log_processed(name)
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
        status.write()
        status.log_processed("combined")
        print(f"[render]   combined: {len(valid_times)} frames -> {cdir}")
        combined_inputs.clear()  # free the 4 held band arrays (~272MB) before precip

        # precip overlay + raw rain-rate layer: streamed straight from the
        # still-open dataset (2 more surface vars, differenced/categorised in
        # fetch.iter_precip_frames).
        status.mark_fetched("precip")
        status.mark_fetched("rain_rate")
        status.write()
        pdir, rdir = _write_precip_frames(
            ds, out_dir,
            on_precip=lambda ti: (status.mark_processed("precip", ti), status.write_throttled()),
            on_rain=lambda ti: (status.mark_processed("rain_rate", ti), status.write_throttled()))
        status.write()
        status.log_processed("precip")
        status.log_processed("rain_rate")
        print(f"[render]   precip: {len(valid_times)} frames -> {pdir}")
        print(f"[render]   rain_rate: {len(valid_times)} frames -> {rdir}")
        layers.append("rain_rate")  # a normal switchable layer, like the cloud vars above

        status.mark_done()
        status.write()
    finally:
        ds.close()

    layers = ["combined"] + layers  # derived layer first = viewer default
    manifest = _write_manifest(run_time, valid_times, x, y, layers, precip=True)
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
            status.log_processed(name)
            layers.append(name)
            print(f"[render]   {name}: {len(valid_times)} frames -> {var_dir}")
        status.mark_fetched("combined")
        status.write()
        cdir = _write_combined_frames({k: z[k] for k in COMBINED_INPUTS}, out_dir,
                                      on_frame=lambda ti: (status.mark_processed("combined", ti), status.write_throttled()))
        status.write()
        status.log_processed("combined")
        print(f"[render]   combined: {len(valid_times)} frames -> {cdir}")

        has_precip = "precip_rate" in z
        if has_precip:
            status.mark_fetched("precip")
            status.mark_fetched("rain_rate")
            status.write()
            pdir, rdir = _write_precip_frames_arrays(
                z["precip_rate"], z["precip_ptype"], z["precip_t2m"], out_dir,
                on_precip=lambda ti: (status.mark_processed("precip", ti), status.write_throttled()),
                on_rain=lambda ti: (status.mark_processed("rain_rate", ti), status.write_throttled()))
            status.write()
            status.log_processed("precip")
            status.log_processed("rain_rate")
            print(f"[render]   precip: {len(valid_times)} frames -> {pdir}")
            print(f"[render]   rain_rate: {len(valid_times)} frames -> {rdir}")
            layers.append("rain_rate")

        status.mark_done()
        status.write()

    layers = ["combined"] + layers  # derived layer first = viewer default
    manifest = _write_manifest(run_time, valid_times, x, y, layers, precip=has_precip)
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
            log_line(f"deleted handed-over run {p.name}")
