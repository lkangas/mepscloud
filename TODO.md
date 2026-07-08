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

## Product explorer: every MEPS variable, local-only one-off tool

A completely separate, stripped-down tool to browse every product in
`meps_det_sfc` (see `docs/meps_det_sfc-variables.md`), not just the curated
cloud/precip layers the main app tracks. **Local-only** (runs on my machine,
served locally, no VPS/deployment) and **manually triggered** (not a poll
loop) — decided over building it live on petzval or as a new repo, since it's
one-off exploratory use, not a maintained app, and ~194 variables' worth of
frames is multiple GB (vs. the main app's <1GB for ~11 layers).

Design (confirmed live against the dataset, not guessed):

- Of the ~195 data variables, only `icing_index` has real multi-level
  structure (10 levels) — every other one is a plain `(time, [1], y, x)`
  field, the same shape the main pipeline already handles. Skip
  `icing_index`, render the other ~194.
- No fixed physical range makes sense across such heterogeneous fields
  (temperature, pressure, wind, radiation, …), unlike the main app's cloud
  fractions (0–1) or altitudes (fixed ceiling). Auto-normalise each variable
  to **its own** min/max across the whole fetched run, alpha-encode 0–255
  against that (white RGB, matching the existing LA-PNG display trick) — not
  comparable in absolute terms across variables/runs, fine for exploring one
  variable's spatial/temporal shape. A later pass could pin down real
  physical ranges and proper colormaps (viridis, turbo, …) for variables
  worth a closer look — explicitly deferred, not part of this first pass.
- Viewer: everything fancy stripped out. Time slider, a product picker
  (dropdown — too many for a button row; group by `main` vs `SFX_*`), the
  same land/sea + coastline underlay (reuse `web/static/` assets by relative
  path, no duplication). No fetch/status tracker, no geolocation/marker
  source picker, no zoom, no twilight shading, no combined/precip-style
  derived layers. Click the map to set a point; a single-line meteogram of
  that variable at that point (reuse the pixel-readback trick, one line, no
  fills/gap-hiding — those were tuned for cloud/rain specifically).
- A draft fetch/render script exists (`explore/fetch_render.py`, untested
  beyond a tiny `--limit` smoke test) — reuses `mepscloud.fetch.latest_run_url`
  /`run_meta`, writes its own `explore/web/cache/` (gitignored, separate from
  the main app's cache), no chunked OPeNDAP reads (local run, not the memory-
  constrained VPS updater, so simpler whole-variable reads are fine). The
  viewer (`explore/web/index.html`) is not yet built.

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
