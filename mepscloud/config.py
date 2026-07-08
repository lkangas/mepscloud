"""Central configuration for the MEPS cloud forecast app.

Source: MET Norway's THREDDS/OPeNDAP distribution of the MEPS deterministic
run, native grid (not FMI's resampled, silently-truncatable WFS product —
see mepscloud/fetch.py's docstring for why).
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths. Generated output (frames, manifest, status, log) lives UNDER web/ so
# it sits next to index.html + static/ and is reachable with page-relative
# URLs (cache/..., static/...) -- which is what lets the app work unchanged
# whether it's served at the dev root or under the /clouds/ subpath in prod.
# Override the cache location with MEPSCLOUD_CACHE_DIR (the deployed updater
# points it at the Caddy-served volume).
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
CACHE_DIR = Path(os.environ.get("MEPSCLOUD_CACHE_DIR", WEB_DIR / "cache"))

# ---------------------------------------------------------------------------
# MET Norway THREDDS (see mepscloud/fetch.py for the catalog-listing logic).
# ---------------------------------------------------------------------------
THREDDS_BASE = "https://thredds.met.no/thredds"
MEPS_DET_SFC_PREFIX = "meps_det_sfc_"

# ---------------------------------------------------------------------------
# Native grid projection — confirmed from the NetCDF's own grid_mapping
# (projection_lambert) attributes, not guessed: Lambert Conformal Conic,
# tangent at 63.3N, central meridian 15E, spherical earth. x/y are metres,
# fixed 2500m pitch, genuinely rectangular (no resampling, no NaN padding —
# unlike FMI's product). See probe_lambert.py / probe_native_render.py in
# session scratch history for how this was verified.
# ---------------------------------------------------------------------------
NATIVE_PROJ4 = "+proj=lcc +lat_1=63.3 +lat_2=63.3 +lat_0=63.3 +lon_0=15 +R=6371000 +units=m +no_defs"

# ---------------------------------------------------------------------------
# Cloud variables. "Fraction" vars are 0-1 (or %, normalised on fetch) and
# stored quantized to uint8 (plenty of precision for visualization, ~1/4
# the size of float32). "Metres" vars (cloud base/top altitude) are stored
# as uint16 metres (cloud tops never approach 65535m, so no clipping risk
# in practice; NaN -> 0 which reads as "no cloud base/top", i.e. clear).
# ---------------------------------------------------------------------------
CLOUD_VARS_FRACTION = (
    "cloud_area_fraction",              # total (TCC)
    "low_type_cloud_area_fraction",     # LCC
    "medium_type_cloud_area_fraction",  # MCC
    "high_type_cloud_area_fraction",    # HCC
    "fog_area_fraction",                # surface fog (the "total but no band" cloud)
    "convective_cloud_area_fraction",   # CCC -- units are % unlike the rest, normalised on fetch
    "cloud_binary_mask",
)
CLOUD_VARS_METRES = (
    "cloud_base_altitude",
    "cloud_top_altitude",
)
CLOUD_VARS = CLOUD_VARS_FRACTION + CLOUD_VARS_METRES

# ---------------------------------------------------------------------------
# Precipitation. Handled separately from the cloud vars (not a fraction, not a
# metres field): the displayed *intensity* is the per-hour rate obtained by
# differencing the accumulated total between consecutive (hourly) frames, and
# the *phase* (rain / freezing rain / snow, weatherinfo.fi-style) comes from
# the model's categorical precipitation_type field. Both are surface fields in
# meps_det_sfc. See render.py for the phase->ramp mapping and the palette.
# ---------------------------------------------------------------------------
PRECIP_ACC_VAR = "precipitation_amount_acc"   # accumulated total precip, kg/m^2 (= mm water)
PRECIP_TYPE_VAR = "precipitation_type"        # categorical 0-7 (metno code table, see render.py)
# precipitation_type is INSTANTANEOUS (the phase at the frame tick) while the
# rate is the accumulation over the preceding hour, so a pixel that precipitated
# earlier in the hour but is dry at the tick has a rate but a fill/"unknown"
# type (~1/3 of wet pixels, confirmed both summer & winter). For those we fall
# back to 2 m temperature to pick rain vs snow -- otherwise winter snow would
# render as rain. precipitation_type stays the primary classifier.
PRECIP_TEMP_VAR = "air_temperature_2m"        # screen temperature (K), fill-phase fallback
PRECIP_VARS = (PRECIP_ACC_VAR, PRECIP_TYPE_VAR, PRECIP_TEMP_VAR)

# Forecast horizon: fetch/cache the entire extent of what the run publishes
# (67 hourly steps as of this writing) -- exploring the full horizon is the
# point of this app, not just a "tonight" snapshot.

# ---------------------------------------------------------------------------
# Site of interest (deferred meteogram feature — kept here for later).
# ---------------------------------------------------------------------------
KOMAKALLIO = (60.2415, 24.3349)  # (lat, lon)
LOCAL_TZ = "Europe/Helsinki"
