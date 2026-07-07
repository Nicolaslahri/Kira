"""Fetch + cache + parse packs, and load/save the local bindings.

The download/cache layer mirrors ``providers/anime_lists.py``: lazy, 24h
refresh, corruption-safe, and it NEVER throws to callers (a dead URL yields a
stale cache or ``(None, reason)`` — never a crash mid-scan). Every fetch goes
through ``download_guard.fetch_capped`` so the SSRF guard + byte cap apply to
the user-supplied URL.

Bindings (the local, machine-specific list of installed packs) live in the
``settings`` table under the single key ``packs.bindings`` as a JSON array.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

from kira.packs.schema import (
    MAX_PACK_BYTES,
    Pack,
    PackBinding,
    PackValidationError,
    parse_pack,
)

logger = logging.getLogger("kira.packs.loader")

BINDINGS_KEY = "packs.bindings"

from kira.config import cache_dir as _kira_cache_dir
_CACHE_DIR = _kira_cache_dir() / "packs"
_MAX_AGE_SEC = 24 * 3600

# In-memory parsed-pack cache keyed by binding.key, plus a load lock.
_packs: dict[str, Pack] = {}
_load_lock = asyncio.Lock()


def _cache_path(key: str) -> Path:
    safe = key.replace(":", "_").replace("/", "_")
    return _CACHE_DIR / f"{safe}.json"


def _fresh(path: Path) -> bool:
    return path.exists() and (time.time() - path.stat().st_mtime) < _MAX_AGE_SEC


async def _client() -> tuple[httpx.AsyncClient, bool]:
    """Return (client, own). Reuse the shared client; fall back to a throwaway
    one only if the shared accessor is unavailable (e.g. inside a unit test)."""
    try:
        from kira import net

        return net.shared_client(), False
    except Exception:
        return httpx.AsyncClient(), True


async def fetch_pack(
    url: str, *, client: httpx.AsyncClient | None = None
) -> tuple[Pack | None, str | None]:
    """Download + validate the pack at ``url``. Returns ``(pack, None)`` on
    success or ``(None, reason)`` on any failure. Never raises."""
    from kira.download_guard import fetch_capped
    from kira.url_guard import is_safe_outbound_url

    url = (url or "").strip()
    if not url:
        return None, "no URL"
    ok, reason = is_safe_outbound_url(url)
    if not ok:
        return None, f"unsafe URL ({reason})"

    own = False
    c = client
    if c is None:
        c, own = await _client()
    try:
        got = await fetch_capped(
            c, url, max_bytes=MAX_PACK_BYTES, timeout=30.0,
            headers={"Accept": "application/json"}, follow_redirects=True,
        )
        if got is None:
            return None, "could not fetch URL (unreachable, non-200, or too large)"
        raw, _ct = got
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except Exception as e:
            return None, f"not valid JSON: {e}"
        if not isinstance(data, dict):
            return None, "pack JSON must be an object"
        try:
            pack = parse_pack(data)
        except PackValidationError as e:
            return None, str(e)
        return pack, None
    finally:
        if own and c is not None:
            await c.aclose()


def _write_cache(key: str, pack: Pack) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(key).write_text(pack.model_dump_json(), encoding="utf-8")
        _invalidate_image_urls()
    except Exception as e:  # cache is an optimisation, never fatal
        logger.warning("packs: cache write failed for %s: %r", key, e)


# ── Pack image allow-list (consumed by the /img proxy) ──────────────────────
# Pack posters live wherever the pack author hosts them (GitHub raw, a fan
# CDN…) — hosts the proxy's static provider allow-list rightly refuses. Rather
# than widening by HOST (which would open the auth-exempt proxy to arbitrary
# URLs on that host), allow the EXACT urls installed packs declare. The
# proxy's SSRF guard still applies on top.
_image_urls_memo: set[str] | None = None


def _invalidate_image_urls() -> None:
    global _image_urls_memo
    _image_urls_memo = None


def allowed_image_urls() -> set[str]:
    """Every poster / season-poster URL declared by a cached pack (memory +
    disk, so it survives a restart before any pack is re-fetched). Memoized;
    invalidated on pack cache writes and evictions."""
    global _image_urls_memo
    if _image_urls_memo is not None:
        return _image_urls_memo
    packs: dict[str, Pack] = dict(_packs)
    try:
        if _CACHE_DIR.exists():
            for p in _CACHE_DIR.glob("*.json"):
                cached = _read_cache(p.stem)
                if cached is not None and p.stem not in packs:
                    packs[p.stem] = cached
    except OSError:
        pass
    urls: set[str] = set()
    for pack in packs.values():
        try:
            if pack.show.poster_url:
                urls.add(pack.show.poster_url)
            urls.update(u for u in pack.show.season_posters.values() if u)
        except Exception:  # noqa: BLE001 — one odd pack must not break the proxy
            continue
    _image_urls_memo = urls
    return urls


def _read_cache(key: str) -> Pack | None:
    p = _cache_path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return parse_pack(data)
    except Exception as e:
        logger.warning("packs: cache read failed for %s: %r", key, e)
        return None


async def get_pack(
    binding: PackBinding, *, force: bool = False, client: httpx.AsyncClient | None = None
) -> Pack | None:
    """Resolve the parsed pack for a binding. In-memory → fresh disk → network.
    Stale cache beats nothing. Returns None only when there's no usable copy."""
    key = binding.key
    if not force and key in _packs and _fresh(_cache_path(key)):
        return _packs[key]
    async with _load_lock:
        if not force and key in _packs and _fresh(_cache_path(key)):
            return _packs[key]
        if not force and _fresh(_cache_path(key)):
            cached = _read_cache(key)
            if cached is not None:
                _packs[key] = cached
                return cached
        pack, err = await fetch_pack(binding.url, client=client)
        if pack is not None:
            _packs[key] = pack
            _write_cache(key, pack)
            return pack
        logger.info("packs: fetch failed for %s (%s); falling back to cache", key, err)
        cached = _read_cache(key)
        if cached is not None:
            _packs[key] = cached
            return cached
        return None


def evict(key: str) -> None:
    """Drop a pack from memory + delete its disk cache (on unbind)."""
    _packs.pop(key, None)
    try:
        p = _cache_path(key)
        if p.exists():
            p.unlink()
    except OSError:
        pass
    _invalidate_image_urls()


# ── Bindings persistence (settings table) ───────────────────────────────────
async def load_bindings(session) -> list[PackBinding]:
    """Read + validate the installed bindings. Malformed entries are dropped
    (never crash a scan because one stored row went bad)."""
    from kira.settings_store import get_raw

    raw = await get_raw(session, BINDINGS_KEY)
    if not isinstance(raw, list):
        return []
    out: list[PackBinding] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            out.append(PackBinding.model_validate(entry))
        except Exception as e:
            logger.warning("packs: dropping invalid binding %r: %r", entry.get("url"), e)
    return out


async def save_bindings(session, bindings: list[PackBinding]) -> None:
    """Upsert the bindings list under ``packs.bindings`` and commit."""
    from kira.models import Setting

    value = [b.model_dump() for b in bindings]
    row = await session.get(Setting, BINDINGS_KEY)
    if row is None:
        session.add(Setting(key=BINDINGS_KEY, value=value))
    else:
        row.value = value
    await session.commit()
