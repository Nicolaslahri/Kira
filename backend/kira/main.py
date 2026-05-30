from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
from kira.config import settings
from kira.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Clear any stale scan lock left by a crash or hard kill. A scan runs as
    # an in-process background task, so a process restart definitionally means
    # NO scan is in flight — yet `system.scan_running` may still hold the
    # timestamp of the dead scan, which 409-locks the Scan button for up to 6h
    # ("I restarted and it still says a scan is already running"). Resetting on
    # boot makes "just restart" actually fix it.
    try:
        from kira.api.scans import _release_db_scan_lock
        await _release_db_scan_lock()
    except Exception as e:
        print(f"startup: scan-lock reset failed: {e!r}")
    # Background warm-up: refresh the AniDB↔TVDB↔TMDB cross-reference table
    # (weekly), then start the auto-heal sweep for files matched before
    # later fixes landed.
    import asyncio
    from kira.api.matches import _auto_heal_stale_matches
    from kira.providers.anime_mappings import AnimeMappings

    async def _warmup():
        try:
            await AnimeMappings._ensure_loaded()
        except Exception as e:
            print(f"warmup: anime mapping refresh failed: {e!r}")
        # One-shot picture-cache migration: evict franchise-member URLs
        # cached before the season-aware poster fetch landed. Guarded by
        # an internal version marker so it runs once and is a no-op after.
        try:
            from kira.providers.anidb import AniDBProvider
            await AniDBProvider.migrate_picture_cache()
        except Exception as e:
            print(f"warmup: picture cache migration failed: {e!r}")
        await _auto_heal_stale_matches()

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
        print(f"startup: watcher start failed: {e!r}")

    try:
        yield
    finally:
        if not warmup_task.done():
            warmup_task.cancel()
        try:
            from kira.watcher import watcher
            await watcher.stop()
        except Exception as e:
            print(f"shutdown: watcher stop failed: {e!r}")


app = FastAPI(
    title="Kira API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
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
