"""Embedded audio-tag reader (mutagen) — the gold signal for the music matcher.

Reads ID3v2 (MP3), Vorbis comments (FLAC/OGG/Opus) and MP4 atoms (M4A/AAC)
through mutagen's normalized "easy" interface, so the rest of the subsystem sees
one flat `MusicTags` shape regardless of container. Crucially it also pulls any
embedded MusicBrainz IDs + AcoustID — when present, those let the matcher resolve
a release/recording DIRECTLY (the music equivalent of the `{tmdb-…}` id bypass),
no fuzzy search needed. Untagged files still return their duration (feeds the
AcoustID fingerprint path). Never raises — a bad/locked/non-audio file → None.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import mutagen


@dataclass
class MusicTags:
    """Flat, container-agnostic view of a file's embedded tags. Any field may be
    None on an untagged or partially-tagged file; the matcher degrades from
    MBID → tag search → fingerprint accordingly."""
    artist: str | None = None
    album_artist: str | None = None
    album: str | None = None
    title: str | None = None
    track_no: int | None = None
    track_total: int | None = None
    disc_no: int | None = None
    disc_total: int | None = None
    year: int | None = None
    date: str | None = None
    genre: str | None = None
    duration: float | None = None        # seconds (float); None when unreadable
    # MusicBrainz IDs — the "embedded id bypass" for music; authoritative.
    mb_recording_id: str | None = None   # the specific recording (this track)
    mb_release_id: str | None = None     # the release (album) — cluster-level identity
    mb_release_group_id: str | None = None
    mb_artist_id: str | None = None
    # AcoustID — used by the fingerprint fallback (Phase 4).
    acoustid_id: str | None = None
    acoustid_fingerprint: str | None = None

    @property
    def has_mbid(self) -> bool:
        """True when we can skip search entirely and resolve by id."""
        return bool(self.mb_release_id or self.mb_recording_id)


def _first(tags, *keys: str) -> str | None:
    """First non-empty value among `keys` (mutagen values are lists). Tolerates
    EasyID3/EasyMP4 raising on an unregistered key."""
    for k in keys:
        try:
            v = tags.get(k)
        except Exception:
            v = None
        if not v:
            continue
        val = v[0] if isinstance(v, (list, tuple)) else v
        s = str(val).strip()
        if s:
            return s
    return None


def _num_pair(s: str | None) -> tuple[int | None, int | None]:
    """Parse a track/disc field: "5" → (5, None); "5/12" → (5, 12)."""
    if not s:
        return None, None
    a, _, b = str(s).partition("/")

    def _i(x: str) -> int | None:
        x = x.strip()
        return int(x) if x.isdigit() else None

    return _i(a), _i(b)


def _year_from(date: str | None) -> int | None:
    if not date:
        return None
    m = re.search(r"\d{4}", str(date))
    return int(m.group()) if m else None


def read_tags(path: str) -> MusicTags | None:
    """Read embedded tags from `path`. Returns a `MusicTags` (mostly empty but
    with a `duration` for an untagged-but-readable audio file), or None when the
    file isn't a recognizable audio container. Never raises."""
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return None
    if audio is None:
        return None

    duration: float | None = None
    try:
        length = getattr(getattr(audio, "info", None), "length", None)
        if length:
            duration = float(length)
    except Exception:
        pass

    tags = getattr(audio, "tags", None)
    if tags is None:
        return MusicTags(duration=duration)   # readable audio, no tags → duration only

    track_no, track_total = _num_pair(_first(tags, "tracknumber"))
    disc_no, disc_total = _num_pair(_first(tags, "discnumber"))
    date = _first(tags, "date", "originaldate", "year")
    return MusicTags(
        artist=_first(tags, "artist"),
        album_artist=_first(tags, "albumartist", "album artist"),
        album=_first(tags, "album"),
        title=_first(tags, "title"),
        track_no=track_no,
        track_total=track_total,
        disc_no=disc_no,
        disc_total=disc_total,
        year=_year_from(date),
        date=date,
        genre=_first(tags, "genre"),
        duration=duration,
        # mutagen normalizes these MB keys across EasyID3 / EasyMP4 / Vorbis.
        # (`musicbrainz_trackid` is the RECORDING mbid in MB's vocabulary.)
        mb_recording_id=_first(tags, "musicbrainz_trackid"),
        mb_release_id=_first(tags, "musicbrainz_albumid"),
        mb_release_group_id=_first(tags, "musicbrainz_releasegroupid"),
        mb_artist_id=_first(tags, "musicbrainz_artistid"),
        acoustid_id=_first(tags, "acoustid_id"),
        acoustid_fingerprint=_first(tags, "acoustid_fingerprint"),
    )


def write_tags(
    path: str, *,
    artist: str | None = None,
    album_artist: str | None = None,
    album: str | None = None,
    title: str | None = None,
    track_no: int | None = None,
    track_total: int | None = None,
    disc_no: int | None = None,
    year: int | None = None,
    mb_release_id: str | None = None,
    mb_recording_id: str | None = None,
    mb_artist_id: str | None = None,
) -> bool:
    """Write matched metadata INTO an audio file's embedded tags, in place.

    For music the embedded tags ARE the canonical metadata (Plex / Jellyfin / Kodi
    read them, not NFO) — so this is how a match actually lands in the file. Done
    across containers via mutagen's normalized easy interface and best-effort:
    returns True if saved, False on any error; NEVER raises (a locked / unsupported
    file is a silent no-op). Each field is set defensively — skipped if the
    container can't hold it — so the universally-supported core (artist / album /
    title / track / date) always lands while the MusicBrainz ids are written where
    the format allows.
    """
    try:
        audio = mutagen.File(path, easy=True)
    except Exception:
        return False
    if audio is None:
        return False

    def _set(key: str, value: object) -> None:
        if value is None or value == "":
            return
        try:
            audio[key] = [str(value)]
        except Exception:
            pass   # container can't hold this key — skip it, keep the rest

    _set("artist", artist)
    _set("albumartist", album_artist or artist)
    _set("album", album)
    _set("title", title)
    if track_no is not None:
        _set("tracknumber", f"{track_no}/{track_total}" if track_total else str(track_no))
    if disc_no is not None:
        _set("discnumber", str(disc_no))
    _set("date", year)
    # MusicBrainz ids — `musicbrainz_trackid` is the RECORDING mbid by convention.
    _set("musicbrainz_trackid", mb_recording_id)
    _set("musicbrainz_albumid", mb_release_id)
    _set("musicbrainz_artistid", mb_artist_id)

    try:
        audio.save()
        return True
    except Exception:
        return False
