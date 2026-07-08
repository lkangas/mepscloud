# TODO / future ideas

Backlog of features not yet built. Nearer-term design context lives in the
git history and commit messages.

## Meteogram (point time series) — PLANNED

A point meteogram beside the map (below it on mobile): pick a point, see cloud
(and later precip/altitude) as a function of forecast time.

Decisions (locked): **client-side pixel-readback** from the existing frames
(no backend change); first iteration = **point marker + simple total/low/mid/
high line chart + the map zoom/crop toggle** (the N×N "line-plot cloud"
experiment follows a bit later).

### Data path — pixel-readback (no backend change)

The raw per-layer frames are already served as **LA PNGs where alpha = cloud
fraction** (total/low/mid/high each their own layer). Build a point series by:
1. project the marker lat/lon → frame pixel with the existing
   `PROJ.forward` + `toPixel` in `web/static/twilight.js` (already aligned to
   the north-up frames, uses the manifest grid extents);
2. read that pixel's **alpha** from each layer's 67 frames (draw just the
   needed sub-region to a canvas, `getImageData`) → 0–1 fraction, ~lossless
   (uint8). Area sampling = read an N×N block instead of one pixel.
- Cache the series per marker position (recompute only on move). Load the 4
  needed layers on demand if not already preloaded.
- Altitude (base/top) later: the display PNG encodes metres as alpha via
  `ALT_DISPLAY_MAX` (~55 m steps) — fine for lines, but if more precision is
  wanted that's the one case for a small backend raw sidecar.

### First iteration

- **Point selection**: draggable marker pin over the map; default from browser
  geolocation, falling back to Komakallio → EFRY; a "use my location" action.
  Marker lives in pixel space; geolocation→pixel via forward projection.
  Showing its lat/lon needs a small **LCC inverse** added to `twilight.js`
  (pixel→x/y→lat/lon, ~15 lines).
- **Layout**: wrap map + meteogram in a flex row — panel right on desktop,
  below on mobile.
- **Chart**: hand-rolled inline **SVG** (no deps, matches twilight.js). x =
  forecast time (local), y = 0–100%, four lines total/low/mid/high (reuse the
  combined-legend colours), vertical cursor synced to the current animation
  frame. Deliberately plain — the goal is to see what the data looks like.
- **Zoom toggle**: a button that crops the map to a region around the marker
  purely client-side (CSS transform on `.frame`; marker + twilight SVGs scale
  along). Same UI otherwise; native 2.5 km pixels go blocky when enlarged.

### Later (after seeing the data)

- **"Line-plot cloud"**: read an N×N neighbourhood (~5×5 = 12.5 km to start)
  around the marker; overplot each pixel's series faintly + centre/mean bold,
  to show local spatial spread. Then refine (violin, sized balls, …).
- **Cloud base & top as lines**: `cloud_base_altitude` / `cloud_top_altitude`
  (already fetched, uint16 metres) plotted as the deck's vertical extent.
- Sun/night shading bands and a precip trace on the meteogram.

## (much later) Convective velocity scale w\* of the boundary layer

Deardorff convective velocity scale — a measure of daytime convective
"stirring" strength (relevant to seeing/turbulence for astro, and to
thermals). Formula to confirm when we get to it:

```
w* = [ (g / θ_v) · (H / (ρ·c_p)) · z_i ]^(1/3)
```

- `g` — gravitational acceleration, 9.81 m/s².
- `θ_v` — near-surface (virtual) potential temperature in K. NOT served
  directly by MEPS (no θ or θ_v field in `meps_det_sfc`), but derivable at
  2 m from fields that are present:
  `θ = T2m·(1e5/p_sfc)^0.2854`, `θ_v = θ·(1 + 0.61·q2m)`, using
  `air_temperature_2m`, `surface_air_pressure`, `specific_humidity_2m`.
  As it's only the `g/θ_v` denominator (~290 K), `air_temperature_2m` alone
  is a fine first approximation (within ~2 %).
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
