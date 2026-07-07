#!/usr/bin/env python3
"""Keep the newest MEPS run fetched + rendered.

MEPS inits every 3 h (00,03,06,09,12,15,18,21 UTC) and publishes ~1.5-2 h
after init. The availability check is cheap -- render_latest_run() fetches
only MET Norway's small catalog.xml and returns immediately (before any data
download) when the newest published run is already rendered. So we can poll
frequently without hammering the OPeNDAP service.

Schedule (matches the design discussion):
  * after a run is processed, sleep until  next_init + MARGIN  -- no point
    polling in the dead window right after a run, nothing new is coming;
  * from then, poll every POLL_SEC (default 60 s) so a freshly published run
    is picked up within ~1 min;
  * a new run is fetched+rendered while the previous one stays live (handover,
    see render.py), then this repeats.

MARGIN is deliberately a bit under an hour: it starts polling safely before
the typical ~90 min publication, at the cost of some extra (cheap) catalog
GETs. Tune via --margin-min once real publication times are observed.

Run:
    python update.py                 # the poll loop (for the updater container)
    python update.py --once          # fetch+render the newest run once, exit
    python update.py --margin-min 50 --poll-sec 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import time

from mepscloud import render

INIT_STEP_H = 3               # MEPS init cadence
DEFAULT_MARGIN_MIN = 50       # start polling this long after an init time
DEFAULT_POLL_SEC = 60         # 1-min polling once inside the window


def next_init_after(t: dt.datetime) -> dt.datetime:
    """The next 3-hourly UTC init time strictly after t."""
    cand = t.replace(minute=0, second=0, microsecond=0,
                     hour=(t.hour // INIT_STEP_H) * INIT_STEP_H)
    while cand <= t:
        cand += dt.timedelta(hours=INIT_STEP_H)
    return cand


def run_loop(margin_min: int, poll_sec: int):
    margin = dt.timedelta(minutes=margin_min)
    while True:
        manifest = render.render_latest_run()          # cheap no-op if unchanged
        run_init = dt.datetime.fromisoformat(manifest["run_utc"])
        window = next_init_after(run_init) + margin
        now = dt.datetime.now(dt.UTC)
        if now < window:
            wait = (window - now).total_seconds()
            print(f"[update] {run_init:%Y-%m-%dT%H:%MZ} ready; next poll window opens "
                  f"{window:%Y-%m-%dT%H:%MZ}; sleeping {wait / 60:.0f} min")
            time.sleep(wait)
        else:
            time.sleep(poll_sec)                        # in the window -> 1-min polling


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="fetch+render newest run once, then exit")
    ap.add_argument("--margin-min", type=int, default=DEFAULT_MARGIN_MIN,
                    help="minutes after an init time to start polling (default 50)")
    ap.add_argument("--poll-sec", type=int, default=DEFAULT_POLL_SEC,
                    help="poll interval inside the window, seconds (default 60)")
    args = ap.parse_args()
    if args.once:
        render.render_latest_run()
    else:
        run_loop(args.margin_min, args.poll_sec)


if __name__ == "__main__":
    main()
