"""The music matcher — ties embedded tags + MusicBrainz into per-file matches.

Album-centric, mirroring how `_match_cluster` matches a TV series ONCE and then
assigns episodes: resolve the RELEASE for a `music|artist|album` cluster, then
assign each file to a track. Resolution ladder (best signal first):
  1. a release MBID shared in the files' tags → fetch it directly (the id bypass)
  2. else an album search by the cluster's artist + album (+ track count)
Track assignment per file: recording MBID (exact) → (disc, track#) → title
similarity. Confidence is 0..1 to match `Match.confidence` (the frontend ×100s it).

Pure logic + the MusicBrainz client; imports only a shared string-similarity
util from the cascade dir (NOT its decision logic), so music stays isolated.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

import httpx

from kira.matcher.similarity import normalize, trigram_similarity
from kira.matcher.keys import SINGLES_ALBUM_MARKERS
from kira.music import musicbrainz as mb
from kira.music.tags import MusicTags


@dataclass
class MusicFile:
    """Matcher input: a file's id + embedded tags + filename-parsed fallbacks
    (used when tags are absent)."""
    file_id: int
    tags: MusicTags
    fb_artist: str | None = None
    fb_album: str | None = None
    fb_title: str | None = None
    fb_track_no: int | None = None
    path: str | None = None      # filesystem path — for the AcoustID fingerprint fallback


@dataclass
class MusicMatch:
    file_id: int
    release_id: str          # MusicBrainz release (album) MBID
    recording_id: str | None
    title: str               # track title
    album: str
    artist: str
    year: int | None
    track_no: int | None
    disc_no: int | None
    cover_art_url: str | None
    confidence: float        # 0..1
    matched_via: str         # mbid | tracknum | title | unpaired


def _artist_of(f: MusicFile) -> str:
    return (f.tags.album_artist or f.tags.artist or f.fb_artist or "").strip()


def _album_of(f: MusicFile) -> str:
    return (f.tags.album or f.fb_album or "").strip()


def _pick_release_hit(hits, cluster_size: int):
    """Best release among search hits: drop low-score noise, then prefer an exact
    track-count match to the cluster (kills compilations/box-sets that contain
    the album), else the highest MusicBrainz relevance score."""
    if not hits:
        return None
    good = [h for h in hits if h.score >= 70] or hits[:1]
    exact = [h for h in good if h.track_count == cluster_size]
    return max(exact or good, key=lambda h: h.score)


# Trailing "edition" suffixes that distinguish a release from MusicBrainz's base
# title — stripped so "Purpose (Deluxe)" / "… (Deluxe Edition)" still resolves.
_EDITION_RE = re.compile(
    r"\s*[\(\[]\s*(?:(?:super\s+|triple\s+chucks?\s+)?deluxe|expanded|special|"
    r"platinum|gold|legacy|complete|remaster(?:ed)?|reissue|mono|stereo|"
    r"collector'?s?|bonus(?:\s+track)?(?:\s+version)?|\d+(?:th|st|nd|rd)?\s*anniversary)"
    r"[^)\]]*[\)\]]\s*$",
    re.IGNORECASE,
)


def _strip_edition(album: str) -> str:
    """Drop trailing edition parentheticals so a release MusicBrainz catalogs
    under its base title still matches. Handles stacked suffixes."""
    out, prev = album.strip(), None
    while out and out != prev:
        prev = out
        out = _EDITION_RE.sub("", out).strip()
    return out


def _album_variants(album: str, artist: str = "") -> list[str]:
    """The album as written, then with a leading "<artist> - " folder prefix
    removed, then the edition-stripped form of each (deduped, order-preserved).

    A folder named "Justin Bieber - My World 2.0" yields parsed album
    "Justin Bieber - My World 2.0"; searching MusicBrainz for that whole string
    misses the real release "My World 2.0" and the album falls to the per-track
    recording fallback (a loose 78% group). Stripping the known artist prefix
    recovers the real album match."""
    variants = [album]
    if artist:
        low = album.lower()
        for dash in (" - ", " – ", " — "):   # hyphen, en-dash, em-dash
            pre = f"{artist}{dash}".lower()
            if low.startswith(pre) and len(album) > len(pre):
                variants.append(album[len(pre):].strip())
                break
    for base in list(variants):
        stripped = _strip_edition(base)
        if stripped and stripped.lower() not in (v.lower() for v in variants):
            variants.append(stripped)
    return variants


def _pick_recording_hit(hits):
    """Best recording among search hits: a tighter score floor than releases
    (track titles are short/common), preferring one that carries a release so we
    get an album name + cover art."""
    if not hits:
        return None
    good = [h for h in hits if h.score >= 85] or []
    if not good:
        return None
    with_rel = [h for h in good if h.release_id]
    return max(with_rel or good, key=lambda h: h.score)


def _to_match(f: MusicFile, release, track, via: str, conf: float) -> MusicMatch:
    return MusicMatch(
        file_id=f.file_id,
        release_id=release.id,
        recording_id=track.recording_id if track else None,
        title=(track.title if track else (f.tags.title or f.fb_title or "")),
        album=release.title,
        artist=(release.artist or _artist_of(f)),
        year=(release.year or f.tags.year),
        track_no=(track.position if track else f.tags.track_no),
        disc_no=(track.disc if track else f.tags.disc_no),
        cover_art_url=release.cover_art_front_url(),
        confidence=conf,
        matched_via=via,
    )


def _assign(files: list[MusicFile], release) -> list[MusicMatch]:
    by_rec = {t.recording_id: t for t in release.tracks if t.recording_id}
    by_pos = {(t.disc, t.position): t for t in release.tracks}
    out: list[MusicMatch] = []
    for f in files:
        track, via, conf = None, "unpaired", 0.0
        # 1. recording MBID — exact.
        if f.tags.mb_recording_id and f.tags.mb_recording_id in by_rec:
            track, via, conf = by_rec[f.tags.mb_recording_id], "mbid", 1.0
        # 2. (disc, track number) within the confirmed release.
        if track is None:
            disc = f.tags.disc_no or 1
            tno = f.tags.track_no if f.tags.track_no is not None else f.fb_track_no
            if tno is not None and (disc, tno) in by_pos:
                track, via, conf = by_pos[(disc, tno)], "tracknum", 0.92
        # 3. title similarity.
        if track is None:
            ftitle = f.tags.title or f.fb_title or ""
            if ftitle:
                best, bestsim = None, 0.0
                nt = normalize(ftitle)
                for t in release.tracks:
                    sim = trigram_similarity(nt, normalize(t.title))
                    if sim > bestsim:
                        best, bestsim = t, sim
                if best is not None and bestsim >= 0.6:
                    track, via, conf = best, "title", round(0.60 + bestsim * 0.35, 3)
        out.append(_to_match(f, release, track, via, conf))
    return out


async def match_album(
    client: httpx.AsyncClient, files: list[MusicFile], *, acoustid_key: str | None = None,
) -> list[MusicMatch]:
    """Match a cluster of music files (same artist+album) to a MusicBrainz release
    and assign each file to a track. One MusicMatch per file (`matched_via`
    'unpaired', confidence 0 when a file can't be placed). Returns [] when the
    release can't be resolved at all — caller leaves the cluster `no_match`."""
    if not files:
        return []

    # 1. id bypass — the release MBID the files' tags agree on.
    release = None
    rel_ids = [f.tags.mb_release_id for f in files if f.tags.mb_release_id]
    if rel_ids:
        release = await mb.get_release(client, Counter(rel_ids).most_common(1)[0][0])

    # 2. album search — the album as written, THEN an edition-stripped variant
    #    ("Purpose (Deluxe)" → "Purpose"). Track count is only a ranking signal
    #    (see search_releases), so a deluxe/clean edition still resolves.
    if release is None:
        rep = max(files, key=lambda f: (bool(_album_of(f)), bool(_artist_of(f))))
        artist, album = _artist_of(rep), _album_of(rep)
        # A "Singles"/loose folder is NOT a real album — resolving a release for it
        # force-matches every unrelated song onto one wrong album (the cluster of
        # distinct singles collapses to "24 files, 1 track" + false duplicates).
        # Skip release resolution → match each file to its OWN recording below, all
        # sharing one synthetic "Singles" group.
        if normalize(album) not in SINGLES_ALBUM_MARKERS:
            for alb in (_album_variants(album, artist) if album else []):
                hit = _pick_release_hit(
                    await mb.search_releases(client, artist, alb, track_count=len(files)),
                    len(files),
                )
                if hit is not None:
                    release = await mb.get_release(client, hit.id)
                    if release is not None:
                        break

    if release is not None:
        return _assign(files, release)

    # 3. per-track recording fallback — loose singles / tracks whose album can't
    #    be resolved as one release. Returns [] if nothing matched at all.
    return await _match_by_recordings(client, files, acoustid_key=acoustid_key)


async def _match_by_recordings(
    client: httpx.AsyncClient, files: list[MusicFile], *, acoustid_key: str | None = None,
) -> list[MusicMatch]:
    """Per-track fallback when no single release resolves (loose singles, or an
    album MusicBrainz doesn't catalog as one). Each file matches on its OWN
    recording (artist + title). All matches share ONE synthetic group id, so the
    cluster stays a single card (e.g. a "Singles" folder) titled by the folder.
    Returns [] if not a single track matched (caller leaves the cluster no_match)."""
    rep = max(files, key=lambda f: (bool(_album_of(f)), bool(_artist_of(f))))
    # Group LABEL = the folder/parsed album (consistent across the whole cluster,
    # e.g. "Singles") — NOT a representative's tags.album, since loose singles each
    # carry their own single's album, so one ("bad guy (with Justin Bieber)") would
    # mislabel the entire group. Group ARTIST = the dominant tagged artist (a
    # Singles folder is mostly one artist; collabs vary per track and stay on each
    # track's own match).
    group_album = next((f.fb_album for f in files if f.fb_album), None) or _album_of(rep) or "Singles"
    _artist_counts = Counter(a for a in (_artist_of(f) for f in files) if a)
    group_artist = _artist_counts.most_common(1)[0][0] if _artist_counts else ""
    group_id = f"loose:{normalize(group_artist)}:{normalize(group_album)}"
    out: list[MusicMatch] = []
    matched_any = False
    for idx, f in enumerate(files, start=1):
        title = (f.tags.title or f.fb_title or "").strip()
        hit = _pick_recording_hit(await mb.search_recordings(client, _artist_of(f), title)) if title else None
        # SEQUENTIAL track number within the group. Loose singles have no real track
        # numbers, so without a distinct number per file they'd all share
        # (disc 1, track None/1) and the UI collapses the whole folder into a single
        # "track" (the bug: a 34-file Singles folder showed "Singles · 1 track").
        tno = idx
        if hit is None:
            # AcoustID fingerprint fallback — the title search had nothing to go on
            # (untagged / garbage filename). Identify the file by its AUDIO. Gated on
            # a key + an on-disk path (the scan seam passes a key only when AcoustID
            # is enabled + fpcalc is installed); best-effort, so a miss → unpaired.
            if acoustid_key and f.path:
                from kira.music import acoustid as _ac
                am = await _ac.identify(client, f.path, acoustid_key)
                if am:
                    rel = await mb.get_recording_releases(client, am.recording_mbid)
                    out.append(MusicMatch(
                        file_id=f.file_id, release_id=group_id, recording_id=am.recording_mbid,
                        title=(am.title or title or f.fb_title or "").strip() or "Unknown",
                        album=group_album, artist=(am.artist or _artist_of(f) or group_artist),
                        year=f.tags.year, track_no=tno, disc_no=f.tags.disc_no,
                        cover_art_url=(f"https://coverartarchive.org/release/{rel}/front-500" if rel else None),
                        confidence=0.72, matched_via="acoustid"))
                    matched_any = True
                    continue
            out.append(MusicMatch(
                file_id=f.file_id, release_id=group_id, recording_id=None,
                title=title, album=group_album, artist=(_artist_of(f) or group_artist),
                year=f.tags.year, track_no=tno, disc_no=f.tags.disc_no,
                cover_art_url=None, confidence=0.0, matched_via="unpaired"))
            continue
        matched_any = True
        out.append(MusicMatch(
            file_id=f.file_id, release_id=group_id, recording_id=hit.recording_id,
            title=hit.title or title, album=group_album,
            artist=(hit.artist or _artist_of(f) or group_artist),
            year=hit.year or f.tags.year, track_no=tno, disc_no=f.tags.disc_no,
            cover_art_url=hit.cover_art_front_url(), confidence=0.78, matched_via="recording"))
    return out if matched_any else []
