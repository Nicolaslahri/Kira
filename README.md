# Kira

**Self-hosted, Docker-native media renamer.** Point Kira at a messy library and it
identifies every movie, show, and anime, renames and organizes them into clean
Plex- and Jellyfin-friendly folders, fills in missing subtitles, and never makes
a change you can't undo.

Kira runs as a single container, serves a web UI on port 8000, and keeps you in
control of the whole loop: **identify → review → apply**. Nothing is renamed
until you approve it.

---

## Features

**Identify &amp; organize**
- Matches movies, TV, and anime against **TMDB**, **TVDB**, and **AniDB**, with
  artwork from **fanart.tv**.
- **Plex** and **Jellyfin** naming layouts out of the box, plus a sandboxed
  Jinja template engine for fully custom names (`{title}`, `{year}`, `{vc}`,
  `{hdr}`, `{channels}`, …).
- Real container metadata — resolution, codec, HDR, audio channels — read via
  MediaInfo, not guessed from the filename.
- A watched-folders daemon auto-scans new files as they land.

**Safe by design**
- Review every match before anything moves; nothing is renamed without your
  approval.
- Authoritative, reversible operations — full **undo**, **crash recovery**, and
  idempotent re-runs. A portable embedded-ID index survives moves and re-scans
  so Kira always knows what it has already done.

**Subtitles**
- Multi-provider search across OpenSubtitles, SubDL, SubSource, Podnapisi and
  more, with honest relevance scoring and result caching.
- Coverage detection plus a narrated, library-wide backfill that fills the gaps.
- Extracts embedded subtitle tracks straight from the files (ffmpeg).

**Integrations**
- **Sonarr** — spot missing episodes and trigger a search for a whole season or
  a single episode, right from the library view.
- **Kira Packs** — community metadata for fan-edits and custom cuts (e.g. *One
  Pace*) that the usual providers can't match. See
  [docs/KIRA_PACKS.md](docs/KIRA_PACKS.md).

---

## Quick start (Docker)

> **Low-RAM NAS?** Don't build the image on the box — every push to `main`
> publishes a prebuilt multi-arch image via GitHub Actions. Point your stack at
> `ghcr.io/nicolaslahri/kira:latest` instead of `build: .`.

A ready-to-edit [`docker-compose.yml`](docker-compose.yml) is included. Change one
line — point the media volume at your library:

```yaml
    volumes:
      - ./config:/config
      - /path/to/your/media:/media   # ← change this to your library
```

Then:

```sh
docker compose up -d
# open http://localhost:8000
```

Run a scan, review the proposed renames, and apply. Provider keys go in
**Settings → Connections** (Kira ships a working fanart.tv key; TMDB and TVDB
keys are free to obtain). To require a login, set `KIRA_AUTH_USER` and
`KIRA_AUTH_PASS`.

---

## Configuration

Everything is configurable from **Settings** in the UI; the common knobs are also
environment variables for first-boot bootstrapping:

| Variable | Purpose |
|---|---|
| `KIRA_MEDIA_ROOT` | Library root Kira renames within (`/media` in Docker). |
| `KIRA_BROWSE_ROOT` | Where the in-app folder picker may browse. |
| `KIRA_DATABASE_URL` | SQLite URL (defaults to `/config/kira.db`). |
| `KIRA_TMDB_API_KEY` / `KIRA_TVDB_API_KEY` | Metadata provider keys (also settable in the UI). |
| `KIRA_AUTH_USER` / `KIRA_AUTH_PASS` | Optional HTTP Basic auth — set **both** to require login. |
| `KIRA_CACHE_DIR` | Where the image/poster caches persist (`/config/.cache` in Docker). |
| `KIRA_LOG_LEVEL` | Backend log level (`INFO` default; `DEBUG` for troubleshooting). |
| `KIRA_SLOW_QUERY_MS` | Log any DB query slower than this many milliseconds. |
| `KIRA_FORCE_IPV4` | Force IPv4 DNS resolution (works around broken IPv6 to TMDB etc.). |
| `KIRA_SCENE_RULES` | Path to a custom `scene-rules.json` overriding the bundled release tokens. |
| `KIRA_FRONTEND_DIST` | Path of the built SPA the backend serves (set by the image). |
| `KIRA_CORS_ORIGINS` | Extra allowed origins for API calls (dev servers). |
| `KIRA_HOST` | Bind address override for bare-metal runs. |
| `KIRA_MEDIA_HOST` | Compose-only: host path mounted at `/media` (see docker-compose.yml). |
| `KIRA_IMG_ALLOWED_HOSTS` | Extra image-CDN hosts the `/img` proxy may fetch from (comma-separated). |


### Security notes

- **TLS**: Kira speaks plain HTTP. If you expose it beyond your LAN, put it
  behind a reverse proxy that terminates TLS (Caddy, nginx, Traefik) and enable
  Basic auth (`KIRA_AUTH_USER`/`KIRA_AUTH_PASS` or the in-app sign-up).
- **Failed logins** are rate-limited per IP (exponential lockout) and logged.
- **`/config/kira.db` contains your provider API keys in plaintext** — treat the
  config volume as sensitive: keep it off shared folders, and consider
  `chmod 600 kira.db` on the host.
- The image proxy (`/img`) only fetches from known art CDNs; extend with
  `KIRA_IMG_ALLOWED_HOSTS` if you use a custom artwork source.

---

## Development

Kira is a Python/FastAPI backend serving a React + Vite single-page app. In
development the two run independently.

**Backend** (Python 3.12+):

```sh
cd backend
python -m venv .venv && . .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e .
uvicorn kira.main:app --reload --port 8000
pytest                                            # run the test suite
```

**Frontend**:

```sh
cd frontend
npm install
npm run dev      # Vite dev server
npm run build    # type-check + production build
npm test         # vitest
```

The native helpers — **ffmpeg** (embedded-subtitle extraction) and
**libmediainfo** (real codec/resolution reads) — are bundled into the Docker
image; install them locally to exercise those features. Kira degrades to
filename-only parsing when they're absent.

---

## How it works

A single long-lived process serves both `/api/v1` and the built SPA (same-origin,
no CORS). A scan walks the library, parses filenames, and resolves each file
against the metadata providers; matches land in a review queue; applying a match
performs the rename/move transactionally and records enough to reverse it. State
lives in SQLite (SQLAlchemy + Alembic migrations), and a Rust-backed filesystem
watcher drives the auto-scan daemon.

See [ARCHITECTURE.md](ARCHITECTURE.md) for a deeper tour.

---

## Project layout

```
backend/            FastAPI app, providers, subtitle + pack subsystems, tests
frontend/           React + Vite SPA
docs/               Kira Packs authoring guide + examples
tools/              helper scripts (e.g. the One Pace pack builder)
Dockerfile          single-image build (SPA + API)
docker-compose.yml  one-container deploy
```
