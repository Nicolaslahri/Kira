"""Image proxy + on-disk cache.

Some poster hosts are slow (AniDB's `cdn.anidb.net` measured ~12× slower than
TheTVDB's CDN). Routing those through Kira fetches the image ONCE, caches the
bytes locally, and serves every later request from disk — so covers load from
localhost (instant) and a slow/flaky upstream CDN is hit at most once per image.

Security: the URL is SSRF-guarded (can't be pointed at internal/metadata hosts),
the body is size-capped, and only real image bytes are served (sniffed, not
trusted by content-type). The endpoint is auth-exempt — like the login-page
poster rails — because `<img>` tags can't send Basic-auth headers; serving
SSRF-guarded, size-capped, image-only bytes pre-auth is the same accepted
trade-off as `/auth/backdrop`.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Response
from fastapi.responses import FileResponse

from kira.download_guard import fetch_capped, sniff_image

router = APIRouter(prefix="/img", tags=["images"])
logger = logging.getLogger("kira.api.images")

_CACHE_DIR = Path.cwd() / ".cache" / "images"
_MAX_IMAGE_BYTES = 12 * 1024 * 1024            # 12 MiB — generous for a poster
_CACHE_HEADERS = {"Cache-Control": "public, max-age=2592000, immutable"}   # 30d
_EXTS = ("jpg", "png", "webp", "gif", "bmp")

# Bounded LRU eviction so the proxy cache can't silently fill the host disk on a
# huge library (5k+ series → tens of thousands of posters/backdrops). When the
# directory exceeds the cap we delete oldest-by-mtime files down to a target.
# The sweep is a blocking dir-walk, so it runs off the event loop (to_thread)
# and only every Nth write — not on the hot path of every request.
_CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024      # 2 GiB hard cap
_CACHE_TARGET_BYTES = int(_CACHE_MAX_BYTES * 0.8)   # evict down to 80%
_EVICT_EVERY = 200                              # check every N successful writes
_write_count = 0


def _cache_paths(url: str) -> list[Path]:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return [_CACHE_DIR / f"{key}.{e}" for e in _EXTS]


def _evict_lru() -> None:
    """If the cache dir exceeds `_CACHE_MAX_BYTES`, delete oldest-by-mtime files
    until under `_CACHE_TARGET_BYTES`. Sync/blocking — call via `to_thread`.
    Best-effort: any stat/unlink error on a single file is skipped. The 30-day
    `immutable` headers mean re-fetching an evicted image is cheap + rare."""
    try:
        entries: list[tuple[float, int, Path]] = []
        total = 0
        with os.scandir(_CACHE_DIR) as it:
            for e in it:
                if not e.is_file() or e.name.endswith(".part"):
                    continue
                try:
                    st = e.stat()
                except OSError:
                    continue
                entries.append((st.st_mtime, st.st_size, Path(e.path)))
                total += st.st_size
        if total <= _CACHE_MAX_BYTES:
            return
        entries.sort(key=lambda t: t[0])          # oldest first
        freed = 0
        need = total - _CACHE_TARGET_BYTES
        for _mtime, size, path in entries:
            if freed >= need:
                break
            try:
                path.unlink()
                freed += size
            except OSError:
                continue
        logger.info("image cache eviction: freed %d MiB (was %d MiB)",
                    freed // (1 << 20), total // (1 << 20))
    except FileNotFoundError:
        return
    except Exception as e:  # noqa: BLE001 — eviction must never break a request
        logger.info("image cache eviction failed (non-fatal): %r", e)


async def _maybe_evict() -> None:
    """Throttled, off-loop LRU sweep — runs every `_EVICT_EVERY` writes."""
    global _write_count
    _write_count += 1
    if _write_count % _EVICT_EVERY == 0:
        import asyncio
        await asyncio.to_thread(_evict_lru)


@router.get("", response_class=Response)
async def proxy_image(u: str = Query(..., max_length=2048)) -> Response:
    """Fetch + cache + serve a remote image. Cache hit → served from disk."""
    if not (u.startswith("http://") or u.startswith("https://")):
        raise HTTPException(400, "Only http(s) image URLs are allowed.")
    # Serve from disk if we've already fetched it.
    for fp in _cache_paths(u):
        if fp.is_file():
            return FileResponse(fp, media_type=f"image/{fp.suffix[1:]}", headers=_CACHE_HEADERS)

    from kira import net
    fetched = await fetch_capped(
        net.shared_client(), u, max_bytes=_MAX_IMAGE_BYTES, timeout=30.0,
        # /img is auth-exempt and `u` is fully caller-controlled, so every
        # redirect hop is re-validated against the SSRF guard rather than
        # blindly followed to a possible internal/metadata target.
        revalidate_redirects=True,
    )
    if not fetched:
        raise HTTPException(502, "Could not fetch the image.")
    content, _ct = fetched
    fmt = sniff_image(content)                 # trust magic bytes, not content-type
    if not fmt:
        raise HTTPException(415, "Upstream did not return an image.")
    ext = "jpg" if fmt == "jpeg" else fmt
    # Persist atomically so a concurrent request never reads a half-written file.
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dest = _CACHE_DIR / f"{hashlib.sha1(u.encode('utf-8')).hexdigest()}.{ext}"
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(content)
        os.replace(tmp, dest)
        await _maybe_evict()        # bounded LRU GC (throttled, off-loop)
    except OSError as e:
        logger.info("image cache write failed (serving anyway): %r", e)
    return Response(content, media_type=f"image/{ext}", headers=_CACHE_HEADERS)
