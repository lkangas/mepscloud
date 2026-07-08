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

## (much later) Convective velocity scale w\* of the boundary layer

Deardorff convective velocity scale — a measure of daytime convective
"stirring" strength (relevant to seeing/turbulence for astro, and to
thermals). Formula to confirm when we get to it:

```
w* = [ (g / θ_v) · (H / (ρ·c_p)) · z_i ]^(1/3)
```

- `g` — gravitational acceleration, 9.81 m/s².
- `θ_v` — near-surface (virtual) potential temperature in K; surface/2 m
  temperature is a fine first approximation.
- `H / (ρ·c_p)` — the *kinematic* surface sensible heat flux (units K·m/s).
  H is the sensible heat flux in W/m² (`SFX_H`, see MEPS product list),
  ρ ≈ 1.2 kg/m³ air density, c_p ≈ 1005 J/(kg·K). w\* only defined for
  unstable (H > 0, upward) conditions — undefined/zero at night.
- `z_i` — boundary-layer (mixed-layer) depth; MEPS has
  `atmosphere_boundary_layer_thickness`.

So it needs three extra MEPS fields beyond what we fetch now (`SFX_H`,
2 m temperature, boundary-layer thickness). Purely a derived scalar — could
be a meteogram trace or a map layer. Research/verify the exact θ_v vs. T
choice and flux sign convention before implementing.
