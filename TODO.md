# TODO / future ideas

Backlog of features not yet built. Nearer-term design context lives in the
git history and commit messages.

## Precipitation layer (weatherinfo.fi-style, 3 phases) — PLANNED

Show a per-timestep precipitation-rate layer coloured like
**weatherinfo.fi** (which is itself an FMI-MEPS render — same source model
as us, so the look ports directly). It splits precip into three phases,
each with its own colour ramp:

- **rain** (mm/h): blue → teal → green → yellow → orange → red → magenta →
  white, ticks 1/5/10/15/20/25 mm.
- **freezing rain** (mm/h): violet ramp, 1–5 mm.
- **snowfall** (cm/h): light-cyan → deep-blue ramp, 1–15 cm.

Exact colour stops sampled from their colorbars are saved in
`tools/weatherinfo_precip_palette.json` (build a LUT from those and map
value → ramp position; assume ~linear in value unless re-checked).

Decisions (locked): precip is an **overlay above the cloud layer** (its own
toggle, composites over any cloud product); snow shown as **cm depth**;
phase from the model's **`precipitation_type`** field.

### Intensity = per-hour rate, not total accumulation

MEPS det frames are hourly (0..66 h). Intensity = **per-step rate** by
differencing `precipitation_amount_acc` between consecutive frames [mm/h,
= kg/m² water-equiv]. Clip Δ<0 to 0 (guards against any acc reset); frame 0
→ dry. This "precip during the last hour" is what makes a pixel wet — the
alpha/dry test uses it, not `precipitation_type`.

### Phase per pixel — from `precipitation_type`

`precipitation_type` is an instantaneous per-frame categorical field
(units "1"); metno NWPdocs code table (values 0–7, **no "none" code** — it
is the type that *would* fall, so dryness comes from the intensity above,
not from this field):

  0 drizzle · 1 rain · 2 sleet · 3 snow · 4 freezing drizzle ·
  5 freezing rain · 6 graupel · 7 hail

Map to the three weatherinfo ramps:
- **rain** ramp  ← {0 drizzle, 1 rain}
- **freezing**   ← {4 freezing drizzle, 5 freezing rain}
- **snow** ramp  ← {2 sleet, 3 snow, 6 graupel, 7 hail}

So the fetch gains just **2 fields**: `precipitation_amount_acc` and
`precipitation_type`. Snow-ramp value is depth: `cm = mm_water_equiv ×
SLR/10`, SLR ≈ 10 (tunable) → ~1 cm per mm-water.

### Pipeline (`mepscloud/fetch.py`, `render.py`)

- Fetch `precipitation_amount_acc` as a float time series (we already
  stream per-variable), `np.diff` along time → per-hour mm/h, clip
  negatives. Fetch `precipitation_type` per frame (nearest-neighbour, keep
  the integer code — do NOT smooth/interpolate a categorical field).
- `_precip_rgba(rate_h, ptype)`: per pixel → phase ramp from `ptype` → LUT
  colour at the intensity (mm/h, or cm/h via SLR for snow); alpha ramps
  from transparent below a dry threshold (~0.1 mm/h) to mostly-opaque
  (radar look). north-up flip like `_combined_rgba`. Build the 3 LUTs from
  `tools/weatherinfo_precip_palette.json`.
- Add `"precip"` to `STATUS_PRODUCTS` / manifest layers; write per-frame
  RGBA PNGs.

### Viewer (`web/index.html`)

- Precip as an independent overlay `<img id="precip">` stacked **above** the
  cloud `#base`, **below** twilight/roads/coastlines, with its own toggle —
  composites over whichever cloud product is shown, transparent where dry.
- Add a compact 3-ramp legend (rain/freezing/snow).

### Testing

July runs are rain-only. To validate the snow + freezing-rain colours,
pull a **winter** archive run from THREDDS (e.g. a January date) during
implementation.

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
