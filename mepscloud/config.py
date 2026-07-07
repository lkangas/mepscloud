"""Central configuration for the MEPS cloud forecast app.

Source: MET Norway's THREDDS/OPeNDAP distribution of the MEPS deterministic
run, native grid (not FMI's resampled, silently-truncatable WFS product —
see mepscloud/fetch.py's docstring for why).
"""
from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (cache lives under the project; it is regenerated, safe to delete).
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "cache"
WEB_DIR = PROJECT_ROOT / "web"

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
    "convective_cloud_area_fraction",   # CCC -- units are % unlike the rest, normalised on fetch
    "cloud_binary_mask",
)
CLOUD_VARS_METRES = (
    "cloud_base_altitude",
    "cloud_top_altitude",
)
CLOUD_VARS = CLOUD_VARS_FRACTION + CLOUD_VARS_METRES

# Forecast horizon: fetch/cache the entire extent of what the run publishes
# (67 hourly steps as of this writing) -- exploring the full horizon is the
# point of this app, not just a "tonight" snapshot.

# ---------------------------------------------------------------------------
# Site of interest (deferred meteogram feature — kept here for later).
# ---------------------------------------------------------------------------
KOMAKALLIO = (60.2415, 24.3349)  # (lat, lon)
LOCAL_TZ = "Europe/Helsinki"
