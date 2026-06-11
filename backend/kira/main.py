import base64
import logging
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

# Import FIRST, at module load — installs the force-IPv4 resolver before any
# provider client (or the settings test endpoint) can resolve a hostname, so we
# never attempt a dead IPv6 path. The lifespan still reads the user's saved
# override below.
from kira import net as _net  # noqa: F401  (import side-effect: net.install())

from kira.api import files as files_api
from kira.api import health as health_api
from kira.api import history as history_api
from kira.api import integrations as integrations_api
from kira.api import matches as matches_api
from kira.api import providers as providers_api
from kira.api import rename as rename_api
from kira.api import scans as scans_api
from kira.api import search as search_api
from kira.api import series as series_api
from kira.api import settings as settings_api
from kira.api import system as system_api
from kira.api import webhooks as webhooks_api
from kira.config import settings
from kira.database import init_db

logger = logging.getLogger("kira.startup")


def _bind_host() -> str:
    """Best-effort detection of the host uvicorn bound to. The bind host is a
    uvicorn CLI arg (the Dockerfile CMD uses `--host 0.0.0.0`), not a config
    field, so we parse `sys.argv` and fall back to the KIRA_HOST env var."""
    import os
    import sys

    argv = sys.argv
    for i, a in enumerate(argv):
        if a == "--host" and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith("--host="):
            return a[len("--host="):]
    return os.environ.get("KIRA_HOST", "")


def _is_loopback(host: str) -> bool:
    """True when the bind host is localhost-only (no exposure beyond this box)."""
    return host.strip().lower() in ("", "127.0.0.1", "::1", "localhost")


def _warn_insecure_exposure() -> None:
    """When bound beyond loopback, nudge the operator to lock things down:
    require Basic auth, and confine the filesystem browser. We only WARN — we do
    not change defaults, because full browse is intentionally needed for
    first-run library-root picking and creds are intentionally opt-in."""
    import os

    if _is_loopback(_bind_host()):
        return
    log = logging.getLogger("kira.startup")
    if not (settings.auth_user and settings.auth_pass):
        log.warning(
            "Kira is bound beyond localhost but HTTP Basic auth is OFF. "
            "Anyone who can reach this port has full API access. Set "
            "KIRA_AUTH_USER and KIRA_AUTH_PASS to require credentials."
        )
    if not os.environ.get("KIRA_BROWSE_ROOT", "").strip():
        log.warning(
            "Kira is bound beyond localhost and the folder-browser is "
            "unconfined (KIRA_BROWSE_ROOT is unset), so it can list any "
            "directory the server process can read. Set KIRA_BROWSE_ROOT "
            "(e.g. your media volume) to confine filesystem browsing."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Security posture check: if exposed beyond localhost, warn the operator
    # about opt-in auth / unconfined browsing rather than silently running open.
    try:
        _warn_insecure_exposure()
    except Exception as e:
        logger.warning("startup: exposure warning check failed (non-fatal): %r", e)
    # Force-IPv4 resolver (default on) — sidesteps the broken-IPv6 trap that
    # makes dual-stack hosts like TMDB intermittently fail. `kira.net` installs
    # the resolver on import; here we honour the user's saved override.
    try:
        from kira import net
        from kira.database import SessionLocal
        from kira.settings_store import get_raw
        async with SessionLocal() as _s:
            _v = await get_raw(_s, "network.force_ipv4")
        if isinstance(_v, bool):
            net.set_force_ipv4(_v)
        logger.info("startup: force_ipv4=%s", net.force_ipv4_enabled())
    except Exception as e:
        logger.warning("startup: force_ipv4 config failed (non-fatal): %r", e)
    # Clear any stale scan lock left by a crash or hard kill. A scan runs as
    # an in-process background task, so a process restart definitionally means
    # NO scan is in flight — yet `system.scan_running` may still hold the
    # timestamp of the dead scan, which 409-locks the Scan button for up to 6h
    # ("I restarted and it still says a scan is already running"). Resetting on
    # boot makes "just restart" actually fix it.
    try:
        from kira.api.scans import _release_db_scan_lock, reconcile_orphaned_scans
        await _release_db_scan_lock()
        # Settle scan + file rows a crash/restart left mid-flight: the Scan row
        # stops showing "scanning", and MediaFile covers stuck in the match
        # animation reset to pending (re-matched by the next scan).
        n_scans, n_files = await reconcile_orphaned_scans()
        from kira import activity
        activity.set_boot_recovery(n_scans, n_files)
        if n_scans or n_files:
            logger.info("startup: reconciled %d orphaned scan(s) + %d stuck file(s)", n_scans, n_files)
    except Exception as e:
        logger.warning("startup: scan-lock reset failed: %r", e)
    # #4: settle rename intents a crash left in the move→DB-commit window — finalize
    # the DB for moves that landed on disk, discard intents for moves that never ran.
    try:
        from kira.api.rename import reconcile_pending_renames
        n_final, n_disc = await reconcile_pending_renames()
        if n_final or n_disc:
            logger.info("startup: reconciled rename intents — %d finalized, %d discarded", n_final, n_disc)
    except Exception as e:
        logger.warning("startup: rename-intent reconcile failed: %r", e)
    # Background warm-up: refresh the AniDB↔TVDB↔TMDB cross-reference table
    # (weekly), then start the auto-heal sweep for files matched before
    # later fixes landed.
    import asyncio
    from kira.api.matches import _auto_heal_stale_matches
    from kira.providers.anime_mappings import AnimeMappings

    async def _warmup():
        from kira import activity
        activity.begin("warmup", "Updating anime mappings")
        try:
            await AnimeMappings._ensure_loaded()
        except Exception as e:
            logger.warning("warmup: anime mapping refresh failed: %r", e)
        # One-shot picture-cache migration: evict franchise-member URLs
        # cached before the season-aware poster fetch landed. Guarded by
        # an internal version marker so it runs once and is a no-op after.
        try:
            from kira.providers.anidb import AniDBProvider
            await AniDBProvider.migrate_picture_cache()
        except Exception as e:
            logger.warning("warmup: picture cache migration failed: %r", e)
        activity.end("warmup")
        await _auto_heal_stale_matches()
        # Self-prune the rename log to the configured retention window. Cheap
        # single DELETE; no-op when retention is "forever".
        try:
            from kira.api.history import prune_old_history
            from kira.database import SessionLocal
            async with SessionLocal() as _ps:
                n = await prune_old_history(_ps)
                if n:
                    logger.info("startup: pruned %d expired history row(s)", n)
        except Exception as e:
            logger.warning("startup: history prune failed: %r", e)

    # Keep a strong reference — asyncio holds only a weakref to bare tasks,
    # so without this binding the GC can collect the warmup mid-fetch and
    # leave the cross-reference table un-refreshed without any error log.
    warmup_task = asyncio.create_task(_warmup())

    # Watched-folders daemon: arm the auto-scan watcher from settings. It's
    # opt-in (does nothing unless watch.config.auto_scan is true) and never
    # raises out of start(), so a watcher problem can't block boot.
    try:
        from kira.watcher import watcher
        await watcher.start()
    except Exception as e:
        logger.warning("startup: watcher start failed: %r", e)

    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            from kira.watcher import watcher
            await watcher.stop()
        except Exception as e:
            logger.warning("shutdown: watcher stop failed: %r", e)
        try:
            from kira import net
            await net.aclose_shared()
        except Exception as e:
            logger.warning("shutdown: shared http client close failed: %r", e)


def _basic_auth_ok(authorization: str | None, user: str, pw: str) -> bool:
    """Constant-time check of an HTTP Basic `Authorization` header against the
    configured credentials. False on any missing/malformed/mismatched input."""
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
    except Exception:
        return False
    u, sep, p = decoded.partition(":")
    if not sep:
        return False
    # Evaluate BOTH comparisons (no short-circuit) with constant-time compare so
    # timing can't reveal whether the username or the password was the mismatch.
    ok_user = secrets.compare_digest(u, user)
    ok_pass = secrets.compare_digest(p, pw)
    return ok_user and ok_pass


def _auth_exempt(method: str, path: str) -> bool:
    """Bypass Basic auth for: CORS preflight, the container health probe, and the
    token-gated *arr webhooks (which carry their own secret).

    Anchored to the exact mounted paths so an unauthenticated request can't slip
    past by embedding `/webhooks/` or `/health` elsewhere in the URL (e.g.
    `/api/v1/files/x/webhooks/y` or `/api/v1/evil/health`)."""
    return (
        method == "OPTIONS"
        or path == "/api/v1/health"
        or path.startswith("/api/v1/webhooks/")
    )


app = FastAPI(
    title="Kira API",
    version="0.1.0",
    lifespan=lifespan,
)


# HTTP Basic auth (opt-in via KIRA_AUTH_USER + KIRA_AUTH_PASS). Registered
# BEFORE CORS so CORS stays the OUTERMOST middleware — that way even a 401 from
# here carries CORS headers and the SPA can read it instead of an opaque CORS
# error. No creds configured → fully open (today's localhost behavior).
@app.middleware("http")
async def _basic_auth_mw(request: Request, call_next):
    user, pw = settings.auth_user, settings.auth_pass
    if user and pw and not _auth_exempt(request.method, request.url.path):
        if not _basic_auth_ok(request.headers.get("Authorization"), user, pw):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Kira"'})
    return await call_next(request)


# Catch-all error guard — registered BEFORE CORS so it sits INSIDE it. An
# unhandled exception turned into a normal 500 Response here flows back OUT
# through CORSMiddleware, which then attaches the CORS headers. Without this, a
# raised exception bypasses CORS's response decoration entirely → the 500 has no
# Access-Control-Allow-Origin → the browser reports a misleading "Failed to
# fetch" that hides the real error (exactly what masked the Sonarr-test crash).
# FastAPI's HTTPException + validation handlers run further in, so this only
# catches genuine unhandled 500s; the traceback still prints to the server log.
@app.middleware("http")
async def _catch_errors_mw(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception:
        logger.exception("UNHANDLED ERROR on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)


def _cors_config() -> tuple[list[str], bool]:
    """Decide the CORS origin list and whether to send credentials, refusing
    the unsafe wildcard-plus-credentials combination.

    `allow_credentials=True` with a wildcard origin is both spec-invalid (the
    browser rejects `Access-Control-Allow-Origin: *` on a credentialed request)
    AND a misconfiguration that, if a future build special-cased it, would let
    *any* site drive the credentialed API of a LAN-exposed instance. So if the
    configured origins contain `*`, we keep the wildcard (open, anonymous use)
    but DROP credentials — never both."""
    origins = list(settings.cors_origins)
    if "*" in origins:
        logger.warning(
            "CORS: wildcard origin '*' configured — disabling allow_credentials "
            "(wildcard + credentials is unsafe and browser-invalid). Set an "
            "explicit origin list to allow credentialed cross-origin requests."
        )
        return origins, False
    return origins, True


_cors_origins, _cors_allow_credentials = _cors_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


# PB-5: rich /health endpoint replaces the previous one-liner. Reports
# db liveness, AniDB ban + circuit state, uptime, version — designed for
# container orchestrators (Docker HEALTHCHECK, k8s probes). Wired as a
# router so it's grouped under the same prefix as the rest of the API.
app.include_router(health_api.router, prefix="/api/v1")
app.include_router(scans_api.router, prefix="/api/v1")
app.include_router(files_api.router, prefix="/api/v1")
app.include_router(matches_api.router, prefix="/api/v1")
app.include_router(search_api.router, prefix="/api/v1")
app.include_router(series_api.router, prefix="/api/v1")
app.include_router(settings_api.router, prefix="/api/v1")
app.include_router(rename_api.router, prefix="/api/v1")
app.include_router(history_api.router, prefix="/api/v1")
app.include_router(system_api.router, prefix="/api/v1")
app.include_router(system_api.notif_router, prefix="/api/v1")
app.include_router(providers_api.router, prefix="/api/v1")
app.include_router(integrations_api.router, prefix="/api/v1")
app.include_router(webhooks_api.router, prefix="/api/v1")


# ─────────────────────────────────────────────────────────────────────
# Single-image SPA serving (the "Docker-native" deploy). In DEV the React
# app runs on its own Vite server, so this whole block no-ops unless a built
# `frontend/dist/index.html` exists. In the Docker image the built frontend is
# copied in (KIRA_FRONTEND_DIST) and served here, SAME-ORIGIN — one container,
# one port, no CORS.
#
# We deliberately do NOT add a catch-all `/{path}` route: that would be matched
# BEFORE any route registered later (e.g. a test's dynamic route) and shadow it.
# Instead a 404-aware exception handler does the SPA fallback — it fires ONLY
# when no route matched, so every API route (and FastAPI's docs) is untouched,
# and API 404s keep their real `detail`.
# ─────────────────────────────────────────────────────────────────────
import os as _os
from pathlib import Path as _Path

from fastapi.responses import FileResponse as _FileResponse
from fastapi.staticfiles import StaticFiles as _StaticFiles
from starlette.exceptions import HTTPException as _StarletteHTTPException

_FRONTEND_DIST = _Path(
    _os.environ.get("KIRA_FRONTEND_DIST")
    or (_Path(__file__).resolve().parents[2] / "frontend" / "dist")
)
_SPA_INDEX = _FRONTEND_DIST / "index.html"
_API_PREFIXES = ("/api", "/docs", "/redoc", "/openapi")

if _SPA_INDEX.is_file():
    # Hashed, immutable build assets get real StaticFiles (proper caching /
    # range support). A specific prefix → can't shadow /api or anything else.
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", _StaticFiles(directory=str(_assets_dir)), name="assets")

    @app.exception_handler(_StarletteHTTPException)
    async def _spa_aware_404(request: Request, exc: _StarletteHTTPException):
        # SPA fallback: a 404 on a non-API GET means the client-side router
        # owns the path (deep-link / hard refresh on /review) → serve the shell,
        # or the real built file (favicon.svg, etc.) when one exists.
        path = request.url.path
        if exc.status_code == 404 and request.method == "GET" \
                and not path.startswith(_API_PREFIXES):
            rel = path.lstrip("/")
            target = (_FRONTEND_DIST / rel).resolve()
            if rel and _FRONTEND_DIST.resolve() in target.parents and target.is_file():
                return _FileResponse(target)
            return _FileResponse(_SPA_INDEX)
        # Everything else: mirror FastAPI's default HTTPException response so API
        # errors keep their detail + headers byte-for-byte.
        return JSONResponse(
            {"detail": exc.detail}, status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
