# syntax=docker/dockerfile:1
#
# Kira — single-image deploy: one container serving the FastAPI backend AND the
# built React SPA (same-origin, no CORS) on port 8000.
#
#   docker build -t kira .
#   docker run -p 8000:8000 -v ./config:/config -v /your/media:/media kira
#
# (or just `docker compose up` — see docker-compose.yml)

# ── Stage 1: build the React frontend ────────────────────────────────────────
# Debian-based (glibc) rather than alpine — Vite/rolldown/lightningcss ship
# native bindings that are flakier on musl. This stage is discarded; only the
# built `dist/` is copied forward, so its size doesn't affect the final image.
FROM node:22-slim AS frontend
WORKDIR /app/frontend
# Copy manifests first so `npm ci` caches across source-only changes.
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci
COPY frontend/ ./
# `vite build` reads .env.production → VITE_API_BASE=/api/v1 (same-origin).
RUN npm run build

# ── Stage 2: Python runtime ──────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# ffmpeg → embedded-subtitle extraction (kira/subtitles/embedded.py).
# libmediainfo → real resolution/codec/HDR/audio reads (pymediainfo).
# libchromaprint-tools → fpcalc for AcoustID audio fingerprinting (untagged music).
# All optional at runtime (the app degrades gracefully if absent) but cheap to
# include so MediaInfo, embedded-subs, and AcoustID work out of the box on every
# arch (amd64 + arm64) and survive image rebuilds — no in-container download.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libmediainfo0v5 libchromaprint-tools gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the backend's THIRD-PARTY deps FIRST, against a stub package, so this
# heavy layer stays cached across source + frontend edits. The dep set changes
# rarely; app code changes constantly — copying source before the dep install
# (the old order) busted this layer on every edit, reinstalling every wheel on
# the ARM NAS at each Portainer rebuild. Now a code edit only re-runs the fast
# --no-deps package reinstall below.
COPY backend/pyproject.toml ./backend/pyproject.toml
RUN mkdir -p ./backend/kira \
    && printf '__version__ = "0"\n' > ./backend/kira/__init__.py \
    && pip install --no-cache-dir ./backend \
    && rm -rf ./backend/kira

# Real source, then reinstall ONLY the package (deps already satisfied above).
# --force-reinstall --no-deps guarantees the real code lands even if the version
# string didn't change, without touching the cached dependency layer.
COPY backend/kira ./backend/kira
# Alembic migrations must ride ALONG the package (pyproject packages only
# kira*): without these two the boot-time upgrade raised every start and
# silently fell back to create_all — shipped schema revisions never applied.
COPY backend/alembic.ini ./backend/alembic.ini
COPY backend/migrations ./backend/migrations
RUN pip install --no-cache-dir --no-deps --force-reinstall ./backend

# Built SPA from stage 1 — served by kira.main at the path below.
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV KIRA_ALEMBIC_DIR=/app/backend \
    KIRA_FRONTEND_DIST=/app/frontend/dist \
    KIRA_DATABASE_URL=sqlite+aiosqlite:////config/kira.db \
    KIRA_MEDIA_ROOT=/media \
    KIRA_BROWSE_ROOT=/media \
    KIRA_CACHE_DIR=/config/.cache \
    PYTHONUNBUFFERED=1

# /config persists the SQLite DB + settings; /media is the library (mounted rw
# so Kira can rename/move in place).
RUN mkdir -p /config /media
VOLUME ["/config", "/media"]

EXPOSE 8000

# Image-level healthcheck so orchestrators that run the bare image (Portainer
# "recreate", `docker run`) get liveness signal too — compose has its own, but
# the image shouldn't depend on the compose file to be observable. No curl in
# python:slim, so use the interpreter that's already here.
HEALTHCHECK --interval=30s --timeout=6s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

# PUID/PGID support: set both to run the app as your host user (files Kira
# writes on /media then belong to you, not root). Unset = root, as before.
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]

# ONE long-lived uvicorn loop (no --workers: the watched-folders daemon + SQLite
# assume a single process). Serves /api/v1 + the SPA.
CMD ["uvicorn", "kira.main:app", "--host", "0.0.0.0", "--port", "8000"]
