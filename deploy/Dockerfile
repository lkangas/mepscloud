# Updater image for the MEPS cloud app. Deliberately light: the render
# pipeline only needs netCDF4/numpy/requests/Pillow (no cartopy/matplotlib --
# those are tools/-only, one-off local asset generation). The app code + web
# assets are bind-mounted at /app at runtime (see docker-compose.yml), so a
# code update is a `git pull` + `docker compose restart`, no image rebuild.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

CMD ["python", "update.py"]
