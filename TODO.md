# TODO / future ideas

Backlog of features not yet built. Nearer-term design context lives in the
git history and commit messages.

## Meteogram (point time series) — next passes

Shipped (see git): draggable marker + geolocation + point-source picker
(Komakallio/EFRY/geo/manual); a plain SVG chart (low/mid/high cloud fraction,
fog, rain rate) via client-side pixel-readback; the chart doubles as a time
slider; exact (root-found, not sampled) sun-elevation day/twilight/night
shading; "now" markers on the chart and the time slider; proper gap-based
zero-hiding (long flat-zero runs are a real break in the line, not just fewer
points on one continuous line); the client-side map zoom, locked at 5x with
a matching hi-res overlay/roads render. Backlog:

- **"Line-plot cloud"**: read an N×N neighbourhood (~5×5 = 12.5 km to start)
  around the marker; overplot each pixel's series faintly + centre/mean bold,
  to show local spatial spread. Then refine (violin, sized balls, …).
- **Cloud base & top as lines**: `cloud_base_altitude` / `cloud_top_altitude`
  (already fetched, uint16 metres) plotted as the deck's vertical extent. The
  display PNG encodes metres as alpha via `ALT_DISPLAY_MAX` (~55 m steps) —
  fine for lines, or a small backend raw sidecar if more precision is wanted.
- Snow / freezing-rain traces on the meteogram (rain_rate's raw-layer pattern
  extends directly — same trick, phase-filtered on the other two branches of
  the precip classification instead of is_rain).

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
