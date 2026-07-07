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

# Per-URL in-flight locks so concurrent requests for the same image coalesce
# onto a single upstream fetch (see proxy_image). Unbounded in theory, but keyed
# by the finite set of art URLs a library references, and Lock objects are tiny.
import asyncio as _asyncio
_INFLIGHT: dict[str, "_asyncio.Lock"] = {}


def _inflight_lock(key: str) -> "_asyncio.Lock":
    lock = _INFLIGHT.get(key)
    if lock is None:
        # Opportunistic GC so the registry can't grow unbounded across a huge
        # library: when it gets large, drop entries nobody is currently
        # awaiting (a locked entry is in active use and must be kept).
        if len(_INFLIGHT) > 512:
            for k in [k for k, v in list(_INFLIGHT.items()) if not v.locked()]:
                _INFLIGHT.pop(k, None)
        lock = _INFLIGHT[key] = _asyncio.Lock()
    return lock


# Negative cache for upstream fetch failures. Cover Art Archive commonly 404s
# (releases with no front cover), and IA/AniDB CDNs are slow — without this a
# missing cover was re-fetched from the slow upstream on every scroll-back.
# Short TTL so a transient blip self-heals; keyed by URL → expiry monotonic ts.
import time as _time
_NEG_CACHE: dict[str, float] = {}
_NEG_TTL_SEC = 600.0          # 10 min — long enough to stop scroll-back hammering


def _neg_cached(url: str) -> bool:
    exp = _NEG_CACHE.get(url)
    if exp is None:
        return False
    if _time.monotonic() >= exp:
        _NEG_CACHE.pop(url, None)
        return False
    return True


def _neg_remember(url: str) -> None:
    if len(_NEG_CACHE) > 4096:      # bound growth; drop expired entries
        now = _time.monotonic()
        for k in [k for k, e in list(_NEG_CACHE.items()) if e <= now]:
            _NEG_CACHE.pop(k, None)
    _NEG_CACHE[url] = _time.monotonic() + _NEG_TTL_SEC

from kira.config import cache_dir as _kira_cache_dir
_CACHE_DIR = _kira_cache_dir() / "images"
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
        await _asyncio.to_thread(_evict_lru)


# Host allow-list for the auth-exempt proxy. Every art URL Kira ever hands the
# frontend comes from one of these providers, so restricting the proxy to them
# closes the "unauthenticated blind SSRF / LAN port-scanner" hole (arbitrary
# `u` targets used to be fetchable pre-auth; url_guard deliberately allows LAN
# for webhooks, so it alone doesn't protect here). Suffix-matched per label:
# `image.tmdb.org` matches `tmdb.org`; `evil-tmdb.org` does not. Extend via
# KIRA_IMG_ALLOWED_HOSTS (comma-separated) for exotic setups.
_ALLOWED_IMG_HOSTS: tuple[str, ...] = (
    "tmdb.org",              # TMDB posters/backdrops (image.tmdb.org)
    "thetvdb.com",           # TVDB artwork (artworks.thetvdb.com)
    "anidb.net",             # AniDB covers (cdn.anidb.net, cdn-eu, cdn-us)
    "fanart.tv",             # fanart.tv assets (assets.fanart.tv)
    "coverartarchive.org",   # MusicBrainz Cover Art Archive
    "archive.org",           # CAA 302s to ia*.archive.org for the bytes
    "musicbrainz.org",
) + tuple(
    h.strip().lower()
    for h in os.environ.get("KIRA_IMG_ALLOWED_HOSTS", "").split(",")
    if h.strip()
)


def _img_host_allowed(url: str) -> bool:
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return False
    if any(host == d or host.endswith("." + d) for d in _ALLOWED_IMG_HOSTS):
        return True
    # Kira Packs host their art wherever the pack author likes (GitHub raw, a
    # fan CDN…). Allow the EXACT urls declared by installed packs — not their
    # hosts — so pack covers render + disk-cache like provider art without
    # widening the auth-exempt proxy. The SSRF guard downstream still applies.
    try:
        from kira.packs.loader import allowed_image_urls
        return url in allowed_image_urls()
    except Exception:  # noqa: BLE001 — packs are optional; never break the proxy
        return False


async def prefetch_into_cache(u: str) -> bool:
    """Warm the on-disk cache for `u` — the same pipeline as proxy_image minus
    the Response. Used by the post-scan music cover warmup so first paint
    serves CAA covers from localhost instead of paying the 2-8s cold fetch.
    Returns True when the bytes are (already) cached."""
    if not (u.startswith("http://") or u.startswith("https://")):
        return False
    if not _img_host_allowed(u) or _neg_cached(u):
        return False
    for fp in _cache_paths(u):
        if fp.is_file():
            return True
    async with _inflight_lock(u):
        for fp in _cache_paths(u):
            if fp.is_file():
                return True
        from kira import net
        fetched = await fetch_capped(
            net.shared_client(), u, max_bytes=_MAX_IMAGE_BYTES, timeout=30.0,
            revalidate_redirects=True,
        )
        if not fetched:
            _neg_remember(u)
            return False
        content, _ct = fetched
        fmt = sniff_image(content)
        if not fmt:
            return False
        ext = "jpg" if fmt == "jpeg" else fmt
        try:
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            dest = _CACHE_DIR / f"{hashlib.sha1(u.encode('utf-8')).hexdigest()}.{ext}"
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(content)
            os.replace(tmp, dest)
            await _maybe_evict()
        except OSError:
            return False
        return True


@router.get("", response_class=Response)
async def proxy_image(u: str = Query(..., max_length=2048)) -> Response:
    """Fetch + cache + serve a remote image. Cache hit → served from disk."""
    if not (u.startswith("http://") or u.startswith("https://")):
        raise HTTPException(400, "Only http(s) image URLs are allowed.")
    if not _img_host_allowed(u):
        raise HTTPException(403, "Host is not an allowed art provider.")
    # Serve from disk if we've already fetched it.
    for fp in _cache_paths(u):
        if fp.is_file():
            return FileResponse(fp, media_type=f"image/{fp.suffix[1:]}", headers=_CACHE_HEADERS)

    # Recently-failed upstream (e.g. CAA 404 for a coverless release): short-
    # circuit instead of re-hitting the slow upstream on every scroll-back.
    if _neg_cached(u):
        raise HTTPException(404, "Image unavailable upstream (recently failed).")

    # In-flight dedup: a card + mosaic + popup can all ask for the SAME cover at
    # once. Without this each fires its own upstream fetch; with it the first
    # request fetches while the rest wait on the per-URL lock, then serve from
    # the disk cache the first one just wrote.
    async with _inflight_lock(u):
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
            _neg_remember(u)         # don't re-hammer this slow/missing upstream
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
