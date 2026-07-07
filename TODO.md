# TODO / future ideas

Backlog of features not yet built. Nearer-term design context lives in the
git history and commit messages.

## Precipitation layer (radar style)

Fetch precipitation from the same MET Norway MEPS deterministic run and show
it as a layer **on top of** the cloud layer, coloured like weather radar
(green → yellow → orange → red for increasing intensity).

- Source variables in `meps_det_sfc` (confirmed present, units `kg/m^2`):
  - `rainfall_amount` — *instantaneous* rainfall at surface (likely what we
    want for a per-timestep "is it raining now" view).
  - `precipitation_amount_acc` — accumulated total precip (would need
    differencing between steps for a per-hour rate).
  - also available: `integral_of_rainfall_amount_wrt_time`,
    `precipitation_type`, solid-precip (snow+graupel+hail) accumulation.
- Pipeline: add the chosen var to the fetch/quantize path
  (`mepscloud/fetch.py` `CLOUD_VARS`-style) and render per-timestep PNGs in
  `mepscloud/render.py`, but with a radar colourmap + alpha (transparent
  where dry) instead of the white-alpha cloud encoding.
- Viewer: its own toggle, stacked above the cloud layer, below the
  road/coastline overlays.

## Meteogram: plot cloud base & top as lines

When the (still-deferred) point meteogram gets built, include
`cloud_base_altitude` and `cloud_top_altitude` as plotted **lines** (the
vertical extent of the cloud deck at the point), not just the low/mid/high
fraction bands. Both are already fetched/cached (uint16 metres).

See the earlier deferred-meteogram approach: sample the point by projecting
lat/lon → native pixel and reading the value back out of the cached data /
rendered frames.
