# mepscloud

Cloud-cover forecast for astrophotography planning in Finland, from MET
Norway's MEPS deterministic model. Live at
**[weather.defocus.fi/clouds](https://weather.defocus.fi/clouds/)**.

A time-scrubbable map of Finland + surroundings showing cloud by altitude
(low/mid/high + fog, colour-coded), precipitation (rate + phase), a
"Thermals" layer (boundary-layer convective velocity, relevant to daytime
thermals and to seeing), a moving sun-elevation shade (civil / nautical /
astronomical twilight), Finnish roads, and a live status grid of the
fetch/render pipeline.

## How it works

- **Source**: MET Norway THREDDS/OPeNDAP (`meps_det_sfc`), native Lambert
  grid — not FMI's resampled product (which silently truncates large
  requests). See `mepscloud/fetch.py`. The full `meps_det_sfc` variable list
  (221 fields) is in [`docs/meps_det_sfc-variables.md`](docs/meps_det_sfc-variables.md).
- **Pipeline** (`mepscloud/`): fetch the newest run's cloud variables,
  quantize, and render one PNG per layer per forecast step, plus a custom
  altitude-coloured `combined` layer. No projection at render time — the grid
  is already a fixed-pitch rectangle, so frames are native-pixel PNGs.
- **"Thermals"** (`w*`, the Deardorff convective velocity scale) is derived
  from sensible heat flux, boundary-layer thickness, and 2 m temperature.
  Deliberately the simpler of two formulations that were built and compared:
  a version using the real virtual potential temperature and air density
  (needing two more MEPS fields, surface pressure + 2 m humidity) tracked
  the simpler T2m-approximated version within <1% at typical Finnish
  conditions, so the extra complexity was dropped rather than kept for a
  sub-percent difference. See `mepscloud/config.py`'s w* section for the
  full rationale and the dropped formula, if ever worth revisiting.
- **Serving is static**: the pipeline writes `web/cache/` (frames +
  `manifest.json` + `status.json` + `log.txt`); the page (`web/index.html` +
  `web/static/`) is plain HTML/JS that reads them. No app server.
- **Updater** (`update.py`): a poll loop that keeps the newest run rendered.
  The availability check is cheap (a catalog fetch that self-skips before any
  download), so it sleeps until ~50 min after each 3-hourly init then polls
  every minute — a new run is live within ~1 min of publication. A new run is
  rendered while the previous stays live (handover), then the old is deleted.

## Local dev

```bash
python -m venv .venv && . .venv/bin/activate     # or WSL
pip install -r requirements.txt

python update.py --once                          # fetch + render the newest run once
python -m http.server 5174 --directory web       # -> http://localhost:5174/
```

`tools/` (cartopy/matplotlib, `tools/requirements.txt`) regenerates the
static map assets (`web/static/basemap`-derived `landmask.png`, coastline/
border `overlay.png`, `roads.png`) — a one-off, not part of the runtime.

## Deploy

Two parts that share the `web/` directory: the **updater** container (runs
`update.py`, rendering the newest run into `web/cache/`) and any **static web
server** that serves `web/`.

```bash
git clone https://github.com/lkangas/mepscloud
cd mepscloud/deploy && docker compose up -d --build   # runs update.py -> web/cache/
```

Then serve the repo's `web/` directory with any static file server (Caddy,
nginx, …), at a domain root or under a subpath — the page uses only
page-relative URLs (`cache/…`, `static/…`), so both work. Set
`MEPSCLOUD_CACHE_DIR` if the cache lives outside `web/`.

The repo is bind-mounted into the container, so updating a deployment is a
`git pull` + `docker compose restart` (rebuild only when `requirements.txt`
changes).
