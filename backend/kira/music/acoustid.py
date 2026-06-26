"""AcoustID acoustic fingerprinting — the last-resort music matcher.

When a file has no usable tags AND no parseable artist/title (a blog rip named
"track_07.flac", a YouTube grab), the title-based recording search has nothing to
go on. Chromaprint's `fpcalc` computes an acoustic fingerprint from the AUDIO
itself; the AcoustID web service maps that fingerprint → a MusicBrainz RECORDING,
which `musicbrainz.get_recording_releases` then resolves to a full album.

Isolated like the rest of `kira.music`: best-effort (never raises — returns None
on any failure), rate-limited to AcoustID's free-tier ceiling, and inert unless
`acoustid.enabled` is on AND a per-app API key is configured AND fpcalc is present.

  fingerprint(path)            → {duration, fingerprint} via fpcalc, or None
  lookup(client, fp, dur, key) → best AcoustIdMatch above a score floor, or None
  identify(client, path, key)  → fingerprint + lookup in one call
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

import httpx

from kira import fpcalc_setup

logger = logging.getLogger("kira.music.acoustid")

_BASE = "https://api.acoustid.org/v2"
# Kira's registered AcoustID APPLICATION key — ships so fingerprinting works out of
# the box (an AcoustID app key identifies the app for rate-limiting; it is NOT a
# secret credential, same as the bundled fanart.tv project key). A
# `providers.acoustid.api_key` setting overrides it.
PROJECT_KEY = "Icu2xFDuTY"
# AcoustID's free tier allows ~3 lookups/sec; pace ourselves so a folder of
# untagged files doesn't trip the limiter (a single module-level gate).
_MIN_INTERVAL = 0.34
_gate = asyncio.Lock()
_last = 0.0


@dataclass(frozen=True)
class AcoustIdMatch:
    recording_mbid: str
    title: str | None
    artist: str | None
    score: float          # 0..1 AcoustID match score


async def _throttle() -> None:
    global _last
    async with _gate:
        loop = asyncio.get_event_loop()
        wait = _MIN_INTERVAL - (loop.time() - _last)
        if wait > 0:
            await asyncio.sleep(wait)
        _last = loop.time()


async def fingerprint(path: str, *, length: int = 120) -> dict | None:
    """Run fpcalc on a file → {"duration": float, "fingerprint": str}. None when
    fpcalc isn't installed, the file isn't decodable, or it times out. `length`
    caps the analysed audio (the fingerprint only needs the opening ~2 minutes)."""
    exe = fpcalc_setup.resolve_fpcalc()
    if not exe or not path:
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "-json", "-length", str(length), path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode != 0 or not out:
            return None
        d = json.loads(out)
        if isinstance(d, dict) and d.get("fingerprint") and d.get("duration"):
            return {"duration": float(d["duration"]), "fingerprint": str(d["fingerprint"])}
    except (asyncio.TimeoutError, json.JSONDecodeError, ValueError, OSError) as e:
        logger.debug(f"fpcalc fingerprint failed for {path!r}: {e!r}")
    except Exception as e:  # noqa: BLE001 — never let a weird file crash a scan
        logger.warning(f"fpcalc fingerprint error for {path!r}: {e!r}")
    return None


async def lookup(
    client: httpx.AsyncClient, fingerprint_str: str, duration: float, api_key: str,
    *, min_score: float = 0.5,
) -> AcoustIdMatch | None:
    """Query AcoustID for the best RECORDING above `min_score`. None on any
    error / no confident match. Posts (not GET) since fingerprints are long."""
    if not (api_key and fingerprint_str and duration > 0):
        return None
    await _throttle()
    try:
        r = await client.post(
            f"{_BASE}/lookup",
            data={
                "client": api_key,
                "duration": str(int(round(duration))),
                "fingerprint": fingerprint_str,
                "meta": "recordings",
            },
            timeout=15.0,
        )
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.debug(f"AcoustID lookup failed: {e!r}")
        return None
    if not isinstance(d, dict) or d.get("status") != "ok":
        return None
    best: AcoustIdMatch | None = None
    for res in d.get("results") or []:
        if not isinstance(res, dict):
            continue
        score = float(res.get("score") or 0.0)
        if score < min_score:
            continue
        for rec in res.get("recordings") or []:
            mbid = rec.get("id") if isinstance(rec, dict) else None
            if not mbid:
                continue
            artists = rec.get("artists") or []
            artist = " & ".join(a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name")) or None
            cand = AcoustIdMatch(
                recording_mbid=str(mbid),
                title=(str(rec.get("title")) if rec.get("title") else None),
                artist=artist,
                score=score,
            )
            if best is None or cand.score > best.score:
                best = cand
    return best


async def identify(
    client: httpx.AsyncClient, path: str, api_key: str, *, min_score: float = 0.5,
) -> AcoustIdMatch | None:
    """Fingerprint a file and resolve it to a MusicBrainz recording in one call."""
    fp = await fingerprint(path)
    if not fp:
        return None
    return await lookup(client, fp["fingerprint"], fp["duration"], api_key, min_score=min_score)
