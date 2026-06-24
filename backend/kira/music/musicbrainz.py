"""MusicBrainz client — the metadata source for the music matcher.

Lives INSIDE the isolated music subsystem (not the cascade provider registry) so
music stays self-contained. MusicBrainz is keyless but has two hard rules we
honor here: a descriptive **User-Agent is required**, and requests must stay
**≤ 1 req/s** (a class-level async gate enforces ~1.1s spacing, the same shape as
the AniDB gate). All calls are best-effort — a network/HTTP error returns
None/[] rather than raising, so a flaky MusicBrainz never breaks a scan.

Two resolution paths feed the matcher:
  • `get_release(mbid)` — when the file's tags already carry a release MBID, pull
    the full release + its track list directly (the "id bypass").
  • `search_releases(artist, album)` — otherwise, Lucene-search for the album and
    let the matcher score the candidates.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field

import httpx

_BASE = "https://musicbrainz.org/ws/2"
# MusicBrainz REQUIRES a meaningful User-Agent identifying the app + a contact.
_USER_AGENT = "Kira/0.5.0 ( https://github.com/Nicolaslahri/kira )"
_TIMEOUT = 15.0

# ── ≤1 req/s rate gate (MusicBrainz bans abusive clients) ─────────────────────
_MB_MIN_INTERVAL = 1.1
_mb_lock = asyncio.Lock()
_mb_last = 0.0


async def _rate_limited_get(client: httpx.AsyncClient, url: str, params: dict) -> dict | None:
    """One GET, serialized to ≤1/s, JSON or None. Never raises."""
    global _mb_last
    async with _mb_lock:
        wait = _MB_MIN_INTERVAL - (time.monotonic() - _mb_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            r = await client.get(
                url, params={**params, "fmt": "json"},
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                timeout=_TIMEOUT,
            )
        except httpx.RequestError:
            _mb_last = time.monotonic()
            return None
        finally:
            _mb_last = time.monotonic()
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


# ── Normalized shapes ─────────────────────────────────────────────────────────

@dataclass
class MBTrack:
    position: int            # track number within its medium (disc)
    disc: int                # medium/disc number, 1-based
    recording_id: str | None
    title: str
    length_ms: int | None
    artist: str | None


@dataclass
class MBRelease:
    id: str                  # release (album) MBID
    title: str
    artist: str              # album-artist credit (joined)
    date: str | None
    year: int | None
    release_group_id: str | None
    track_count: int
    tracks: list[MBTrack] = field(default_factory=list)

    def cover_art_front_url(self) -> str:
        # Cover Art Archive 302-redirects this to the actual image; the image
        # proxy / <img> follows it. `-500` asks for the 500px thumbnail.
        return f"https://coverartarchive.org/release/{self.id}/front-500"


@dataclass
class MBReleaseHit:
    id: str
    title: str
    artist: str
    date: str | None
    track_count: int | None
    score: int               # MusicBrainz search score, 0..100


@dataclass
class MBRecordingHit:
    """A single RECORDING (track) search hit + one release that contains it —
    the per-track fallback for loose singles whose album can't be resolved."""
    recording_id: str
    title: str
    artist: str
    release_id: str | None
    release_title: str | None
    date: str | None
    score: int               # MusicBrainz search score, 0..100

    @property
    def year(self) -> int | None:
        return _year_of(self.date)

    def cover_art_front_url(self) -> str | None:
        return (f"https://coverartarchive.org/release/{self.release_id}/front-500"
                if self.release_id else None)


def _year_of(date: str | None) -> int | None:
    if not date:
        return None
    m = re.search(r"\d{4}", date)
    return int(m.group()) if m else None


def _credit(artist_credit: list | None) -> str:
    """Join an `artist-credit` array into a display string ("A feat. B")."""
    if not isinstance(artist_credit, list):
        return ""
    out = []
    for c in artist_credit:
        if isinstance(c, dict):
            out.append(str(c.get("name") or (c.get("artist") or {}).get("name") or ""))
            out.append(str(c.get("joinphrase") or ""))
        elif isinstance(c, str):
            out.append(c)
    return "".join(out).strip()


def _parse_release(d: dict) -> MBRelease | None:
    if not isinstance(d, dict) or not d.get("id"):
        return None
    tracks: list[MBTrack] = []
    for medium in d.get("media") or []:
        if not isinstance(medium, dict):
            continue
        disc = medium.get("position") or 1
        for t in medium.get("tracks") or []:
            if not isinstance(t, dict):
                continue
            rec = t.get("recording") or {}
            length = t.get("length") or rec.get("length")
            tracks.append(MBTrack(
                position=int(t.get("position") or 0),
                disc=int(disc or 1),
                recording_id=rec.get("id"),
                title=str(t.get("title") or rec.get("title") or ""),
                length_ms=int(length) if isinstance(length, (int, float)) else None,
                artist=_credit(t.get("artist-credit") or rec.get("artist-credit")) or None,
            ))
    rg = d.get("release-group") or {}
    return MBRelease(
        id=str(d["id"]),
        title=str(d.get("title") or ""),
        artist=_credit(d.get("artist-credit")),
        date=d.get("date") or None,
        year=_year_of(d.get("date")),
        release_group_id=rg.get("id"),
        track_count=sum(len(m.get("tracks") or []) for m in (d.get("media") or []) if isinstance(m, dict)),
        tracks=tracks,
    )


# ── API ───────────────────────────────────────────────────────────────────────

async def get_release(client: httpx.AsyncClient, mbid: str) -> MBRelease | None:
    """Fetch a release (album) + its full track list by MBID. The id-bypass path."""
    d = await _rate_limited_get(
        client, f"{_BASE}/release/{mbid}",
        {"inc": "recordings+artist-credits+release-groups"},
    )
    return _parse_release(d) if d else None


def _lucene_escape(s: str) -> str:
    # Escape Lucene special chars so a title like "AC/DC" or "Mr. Bad Guy"
    # doesn't break the query syntax.
    return re.sub(r'([+\-&|!(){}\[\]^"~*?:\\/])', r"\\\1", s)


async def search_releases(
    client: httpx.AsyncClient, artist: str, album: str, *, track_count: int | None = None,
    limit: int = 12,
) -> list[MBReleaseHit]:
    """Lucene-search releases by artist + album. Returns candidates with
    MusicBrainz's own relevance score for the matcher to rank.

    `track_count` is a RANKING signal only (the matcher's `_pick_release_hit`
    prefers an exact-count release) — deliberately NOT a query filter. A deluxe /
    clean / reissue edition routinely differs from the standard by a track or two,
    and a hard `tracks:N` clause would drop the right release entirely. We over-
    fetch instead and let the matcher rank by score + track-count proximity.
    """
    if not album.strip():
        return []
    parts = [f'release:"{_lucene_escape(album)}"']
    if artist.strip():
        a = _lucene_escape(artist)
        parts.append(f'(artist:"{a}" OR artistname:"{a}")')
    d = await _rate_limited_get(
        client, f"{_BASE}/release",
        {"query": " AND ".join(parts), "limit": str(limit)},
    )
    if not d:
        return []
    hits: list[MBReleaseHit] = []
    for r in d.get("releases") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        hits.append(MBReleaseHit(
            id=str(r["id"]),
            title=str(r.get("title") or ""),
            artist=_credit(r.get("artist-credit")),
            date=r.get("date") or None,
            track_count=r.get("track-count") if isinstance(r.get("track-count"), int) else None,
            score=int(r.get("score") or 0),
        ))
    return hits


async def get_recording_releases(client: httpx.AsyncClient, recording_mbid: str) -> str | None:
    """For the AcoustID path: a fingerprint resolves to a RECORDING; return the
    MBID of one release that contains it so we can pull the full album."""
    d = await _rate_limited_get(
        client, f"{_BASE}/recording/{recording_mbid}", {"inc": "releases"},
    )
    if not d:
        return None
    rels = d.get("releases") or []
    for r in rels:
        if isinstance(r, dict) and r.get("id"):
            return str(r["id"])
    return None


async def search_recordings(
    client: httpx.AsyncClient, artist: str, title: str, *, limit: int = 5,
) -> list[MBRecordingHit]:
    """Lucene-search RECORDINGS (individual tracks) by artist + title — the
    per-track fallback for loose singles / tracks whose album can't be resolved.
    Each hit carries one containing release (for the album name + cover art)."""
    if not title.strip():
        return []
    parts = [f'recording:"{_lucene_escape(title)}"']
    if artist.strip():
        a = _lucene_escape(artist)
        parts.append(f'(artist:"{a}" OR artistname:"{a}")')
    d = await _rate_limited_get(
        client, f"{_BASE}/recording",
        {"query": " AND ".join(parts), "limit": str(limit)},
    )
    if not d:
        return []
    hits: list[MBRecordingHit] = []
    for r in d.get("recordings") or []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        rels = r.get("releases") or []
        rel = rels[0] if rels and isinstance(rels[0], dict) else {}
        hits.append(MBRecordingHit(
            recording_id=str(r["id"]),
            title=str(r.get("title") or ""),
            artist=_credit(r.get("artist-credit")),
            release_id=str(rel.get("id")) if rel.get("id") else None,
            release_title=str(rel.get("title")) if rel.get("title") else None,
            date=r.get("first-release-date") or rel.get("date") or None,
            score=int(r.get("score") or 0),
        ))
    return hits
