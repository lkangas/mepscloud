# mepscloud

Cloud-cover forecast for astrophotography planning in Finland, from MET
Norway's MEPS deterministic model. Live at
**[weather.defocus.fi/clouds](https://weather.defocus.fi/clouds/)**.

A time-scrubbable map of Finland + surroundings showing cloud by altitude
(low/mid/high + fog, colour-coded), a moving sun-elevation shade (civil /
nautical / astronomical twilight), Finnish roads, and a live status grid of
the fetch/render pipeline.

## How it works

- **Source**: MET Norway THREDDS/OPeNDAP (`meps_det_sfc`), native Lambert
  grid — not FMI's resampled product (which silently truncates large
  requests). See `mepscloud/fetch.py`.
- **Pipeline** (`mepscloud/`): fetch the newest run's cloud variables,
  quantize, and render one PNG per layer per forecast step, plus a custom
  altitude-coloured `combined` layer. No projection at render time — the grid
  is already a fixed-pitch rectangle, so frames are native-pixel PNGs.
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

## Deploy (petzval)

Static output served by the shared **weather Caddy** (in the sibling `wx`
repo's `~/weather`) at `/clouds/`; an isolated updater container renders into
`web/cache/`.

```bash
# on petzval, own dir ~/clouds (outside ~/weather and ~/sensor-platform):
git clone https://github.com/lkangas/mepscloud ~/clouds/repo
cd ~/clouds/repo/deploy && docker compose up -d --build   # runs update.py

# the weather Caddy (wx repo) bind-mounts ~/clouds/repo/web and serves /clouds/
```

Updating the deployed app is `git pull` in `~/clouds/repo` + `docker compose
restart` (code is bind-mounted, no rebuild unless deps change). See the `wx`
repo's `deploy/Caddyfile` for the `/clouds` route and
`../defocus.fi/docs/weather-tunnel.md` for the tunnel/Caddy side.
