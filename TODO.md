# TODO / future ideas

Backlog of features not yet built. Nearer-term design context lives in the
git history and commit messages.

## Precip: a contour outline around the wet area, not new colors

The precip colourmap's low (light-rain) end is annoyingly close to the
chosen sea colour, but changing either is off the table: the sea colour
was already carefully tuned against the cloud-layer colours, and re-tuning
one would throw off that whole earlier optimisation. So — don't touch
colours. Instead, draw a single **contour outline** enclosing every
nonzero-precip pixel, in the same teal as the coastline/border/road overlay
(`tools/build_coastline_overlay.py`'s `COAST_COLOR = "#2ec4b6"`), so even
faint rain stays visible by its EDGE against the ground or cloud, whatever
colour is directly under it.

- "Nonzero" = the same wet/dry boundary the precip alpha fade already uses:
  `rate > PRECIP_DRY_LO` (0.05 mm/h) in `render.py`, i.e. anywhere precip
  alpha is above 0.
- Trace it with a ready-made contour function, not hand-rolled boundary
  detection: **`contourpy`**, the library matplotlib's own `contour()`
  actually delegates to internally — same algorithm/quality the user wants,
  but usable as a lightweight standalone dependency (just the geometry
  computation) rather than pulling in all of matplotlib. This matters
  because the production pipeline deliberately has NO
  matplotlib/cartopy/pyproj today (see `mepscloud/fetch.py`'s docstring —
  those are tools-only, one-off asset generation, kept out of the always-
  running updater to keep the deployed image light) and this contour would
  run every frame, every run (67 × every 3h), not as a one-off. Plan:
  `contourpy.contour_generator(...).lines(level)` on the rate field (or the
  binary wet mask) at `PRECIP_DRY_LO` to get the contour path(s) as
  coordinate arrays, then rasterize those paths onto the frame with PIL's
  `ImageDraw` (already used for every other frame in this pipeline) — no
  matplotlib figure/canvas involved at render time. Naturally handles
  several disconnected rain cells (each gets its own closed path). If this
  turns out to want full matplotlib after all, revisit the "keep production
  lean" call before adding the dependency, rather than assuming.
- New backend-rendered layer (own PNG dir per frame, teal stroke on
  transparent, 1px at native res — the precip layer is at cloud-frame
  resolution, not the 5x zoomed overlay), composited above the precip
  colourmap in the viewer as its own toggle (Overlays menu), not baked into
  the existing precip RGBA frames.

## Meteogram (point time series) — next passes

Shipped (see git): draggable marker + geolocation + point-source picker
(Komakallio/EFRY/geo/manual); a plain SVG chart (low/mid/high cloud fraction,
fog, rain rate) via client-side pixel-readback; the chart doubles as a time
slider; exact (root-found, not sampled) sun-elevation day/twilight/night
shading; "now" markers on the chart and the time slider; proper gap-based
zero-hiding (long flat-zero runs are a real break in the line, not just fewer
points on one continuous line); the client-side map zoom, locked at 5x with
a matching hi-res overlay/roads render. Backlog:

- **"Line-plot cloud"** — shipped as a temporary, separate second meteogram
  (faint per-pixel lines + a brighter mean, circular radius mask, tracks
  whichever raw layer is selected on the map). Leave the current
  implementation as-is for now — needs more experimentation (different
  radii, different layers, seeing what the spread actually looks like)
  before deciding anything further. Eventually: decide whether/how some
  aspect of it gets incorporated into the MAIN meteogram (the mean line?
  a refined spread visualisation — violin, sized balls, …?). Once that
  decision is made, this second temporary chart is obsolete and should be
  removed.
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
  to **its own** min/max **across the whole 67-frame run** (one shared scale
  per variable, computed once over all frames together) — explicitly NOT
  per-frame; a per-frame scale would make brightness incomparable across
  time (frame 40 brighter than frame 10 could just mean frame 10 had a
  smaller local range, not a lower value). Alpha-encode 0–255 against that
  one run-wide range (white RGB, matching the existing LA-PNG display trick)
  — not comparable in absolute terms across different variables or runs, but
  fine for exploring one variable's spatial/temporal shape within a run. A
  later pass could pin down real physical ranges and proper colormaps
  (viridis, turbo, …) for variables
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
