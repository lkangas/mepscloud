"""mepscloud — MEPS cloud-cover forecast, fetched from MET Norway's native
Lambert grid (see fetch.py for why, not FMI's resampled product).

Modules:
  config — central settings (paths, native grid projection, cloud variables)
  fetch  — find/fetch/quantize/cache the newest MEPS run

Rendering and the meteogram aren't built yet — still being designed.
"""

__all__ = ["config", "fetch"]
