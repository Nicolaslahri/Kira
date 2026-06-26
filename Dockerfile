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
    && apt-get install -y --no-install-recommends ffmpeg libmediainfo0v5 libchromaprint-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the backend package + its deps. Copy only what pip needs first so this
# heavy layer caches across frontend/source edits.
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/kira ./backend/kira
RUN pip install --no-cache-dir ./backend

# Built SPA from stage 1 — served by kira.main at the path below.
COPY --from=frontend /app/frontend/dist ./frontend/dist

ENV KIRA_FRONTEND_DIST=/app/frontend/dist \
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
# ONE long-lived uvicorn loop (no --workers: the watched-folders daemon + SQLite
# assume a single process). Serves /api/v1 + the SPA.
CMD ["uvicorn", "kira.main:app", "--host", "0.0.0.0", "--port", "8000"]
