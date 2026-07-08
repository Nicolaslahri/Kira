"""The album matcher — MBID bypass, album search, and per-file track assignment
(recording MBID → track number → title similarity). MusicBrainz mocked."""
from __future__ import annotations

import pytest

import kira.music.matcher as matcher
from kira.music.matcher import MusicFile, _album_variants, _strip_edition, match_album
from kira.music.musicbrainz import MBRecordingHit, MBRelease, MBReleaseHit, MBTrack
from kira.music.tags import MusicTags


def _release():
    return MBRelease(
        id="rel-1", title="Discovery", artist="Daft Punk", date="2001-03-12", year=2001,
        release_group_id="rg-1", track_count=2,
        tracks=[
            MBTrack(position=1, disc=1, recording_id="rec-1", title="One More Time", length_ms=320000, artist="Daft Punk"),
            MBTrack(position=2, disc=1, recording_id="rec-2", title="Aerodynamic", length_ms=207000, artist="Daft Punk"),
        ],
    )


@pytest.mark.asyncio
async def test_mbid_bypass_assigns_by_recording_then_tracknum(monkeypatch):
    async def fake_get_release(client, mbid):
        assert mbid == "rel-1"
        return _release()

    async def fake_search(*a, **k):
        raise AssertionError("must not search when a release MBID is present")

    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    files = [
        MusicFile(1, MusicTags(mb_release_id="rel-1", mb_recording_id="rec-1", track_no=1)),
        MusicFile(2, MusicTags(mb_release_id="rel-1", track_no=2)),  # no recording id → track number
    ]
    out = {m.file_id: m for m in await match_album(None, files)}
    assert out[1].matched_via == "mbid" and out[1].confidence == 1.0
    assert out[1].title == "One More Time" and out[1].album == "Discovery" and out[1].year == 2001
    assert out[1].cover_art_url.endswith("/release/rel-1/front-500")
    assert out[2].matched_via == "tracknum" and out[2].confidence == 0.92 and out[2].title == "Aerodynamic"


@pytest.mark.asyncio
async def test_search_path_with_title_fallback(monkeypatch):
    async def fake_search(client, artist, album, *, track_count=None, limit=8):
        assert artist == "Daft Punk" and album == "Discovery" and track_count == 2
        return [MBReleaseHit(id="rel-1", title="Discovery", artist="Daft Punk", date="2001", track_count=2, score=100)]

    async def fake_get_release(client, mbid):
        return _release()

    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    files = [
        MusicFile(1, MusicTags(artist="Daft Punk", album="Discovery", track_no=1)),
        MusicFile(2, MusicTags(artist="Daft Punk", album="Discovery", title="Aerodynamic")),  # no track# → title
    ]
    out = {m.file_id: m for m in await match_album(None, files)}
    assert out[1].matched_via == "tracknum"
    assert out[2].matched_via == "title" and out[2].confidence >= 0.9  # exact title match


@pytest.mark.asyncio
async def test_unpaired_and_unmatchable(monkeypatch):
    async def fake_search(client, artist, album, *, track_count=None, limit=8):
        return [MBReleaseHit(id="rel-1", title="Discovery", artist="Daft Punk", date="2001", track_count=2, score=100)]

    async def fake_get_release(client, mbid):
        return _release()

    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    # A file that exists in the cluster but matches no track → unpaired, conf 0.
    files = [MusicFile(1, MusicTags(artist="Daft Punk", album="Discovery", track_no=99, title="ZZZ Nonexistent"))]
    out = await match_album(None, files)
    assert len(out) == 1 and out[0].matched_via == "unpaired" and out[0].confidence == 0.0

    # No album + no MBID + no recording hit → [].
    monkeypatch.setattr(matcher.mb, "search_recordings", lambda *a, **k: _aempty())
    assert await match_album(None, [MusicFile(1, MusicTags(title="loose"))]) == []


async def _aempty():
    return []


def test_strip_edition_and_variants():
    assert _strip_edition("Purpose (Deluxe)") == "Purpose"
    assert _strip_edition("Under the Mistletoe (Deluxe Edition)") == "Under the Mistletoe"
    assert _strip_edition("Justice (Triple Chucks Deluxe)") == "Justice"
    assert _strip_edition("Believe (Deluxe Edition) (Remastered)") == "Believe"   # stacked
    assert _strip_edition("Changes") == "Changes"                                  # untouched
    assert _album_variants("Purpose (Deluxe)") == ["Purpose (Deluxe)", "Purpose"]
    assert _album_variants("Changes") == ["Changes"]                               # no variant
    # Artist-prefixed folder ("Justin Bieber - My World 2.0") → also try the bare
    # album so the real release resolves instead of falling to the loose fallback.
    assert _album_variants("Justin Bieber - My World 2.0", "Justin Bieber") == ["Justin Bieber - My World 2.0", "My World 2.0"]
    assert _album_variants("My World 2.0", "Justin Bieber") == ["My World 2.0"]    # no prefix → untouched
    assert _album_variants("Purpose (Deluxe)", "Justin Bieber") == ["Purpose (Deluxe)", "Purpose"]  # prefix absent, edition still stripped


@pytest.mark.asyncio
async def test_deluxe_resolves_via_stripped_variant(monkeypatch):
    # No hit for the deluxe name; a hit for the base name → resolves.
    async def fake_search(client, artist, album, *, track_count=None, limit=12):
        if album == "Purpose":
            return [MBReleaseHit(id="rel-p", title="Purpose", artist="Justin Bieber", date="2015", track_count=1, score=100)]
        return []

    async def fake_get_release(client, mbid):
        assert mbid == "rel-p"
        return MBRelease(id="rel-p", title="Purpose", artist="Justin Bieber", date="2015", year=2015,
                         release_group_id="rg", track_count=1,
                         tracks=[MBTrack(position=1, disc=1, recording_id="r1", title="Sorry", length_ms=None, artist="Justin Bieber")])

    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    out = {m.file_id: m for m in await match_album(None, [MusicFile(1, MusicTags(artist="Justin Bieber", album="Purpose (Deluxe)", track_no=1))])}
    assert out[1].album == "Purpose" and out[1].matched_via == "tracknum"


@pytest.mark.asyncio
async def test_recording_fallback_for_loose_singles(monkeypatch):
    async def fake_search(*a, **k):
        return []                       # no album resolves

    async def fake_get_release(*a, **k):
        return None

    async def fake_recordings(client, artist, title, *, limit=5):
        return [MBRecordingHit(recording_id=f"rec-{title}", title=title, artist="Justin Bieber",
                               release_id="rel-s", release_title="Single", date="2019", score=100)]

    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    monkeypatch.setattr(matcher.mb, "search_recordings", fake_recordings)
    files = [
        MusicFile(1, MusicTags(artist="Justin Bieber", album="Singles", title="Yummy")),
        MusicFile(2, MusicTags(artist="Justin Bieber", album="Singles", title="Intentions")),
    ]
    out = {m.file_id: m for m in await match_album(None, files)}
    assert out[1].matched_via == "recording" and out[1].title == "Yummy" and out[1].confidence == 0.78
    assert out[2].matched_via == "recording" and out[2].title == "Intentions"
    # both share ONE synthetic group id → the cluster stays a single card
    assert out[1].release_id == out[2].release_id and out[1].release_id.startswith("loose:")
    assert out[1].cover_art_url.endswith("/release/rel-s/front-500")
    # DISTINCT sequential track numbers — loose singles must NOT collapse to one
    # "track" (that made the UI see a 34-file folder as 34 dupes of one episode).
    assert out[1].track_no == 1 and out[2].track_no == 2


def test_singles_folder_clusters_by_folder_not_artist():
    """A "Singles" folder of DIFFERENT-artist collabs must cluster by the FOLDER
    (one group), not scatter per-artist into N one-file clusters (each of which
    would then get force-matched to a wrong release)."""
    from kira.matcher.keys import compute_series_key
    from kira.parser import ParsedFile

    def key(artist, path):
        return compute_series_key(
            ParsedFile(original_filename="x.flac", media_type="music", artist=artist, album="Singles", title="t"),
            file_path=path)

    a = key("Justin Bieber & Ariana Grande", r"Z:/m/JB - Discography/Singles/a.flac")
    b = key("Justin Bieber & Ed Sheeran", r"Z:/m/JB - Discography/Singles/b.flac")
    assert a == b and a.startswith("music|singles|")          # same folder → one cluster
    assert key("Drake", r"Z:/m/Drake/Singles/c.flac") != a    # different folder → separate
    # A real album still keys by artist + album (unchanged).
    real = compute_series_key(
        ParsedFile(original_filename="x.flac", media_type="music", artist="Justin Bieber", album="Purpose", title="Sorry"),
        file_path=r"Z:/m/JB/Purpose/x.flac")
    assert real == "music|justin bieber|purpose"


@pytest.mark.asyncio
async def test_acoustid_fallback_rescues_untagged_file(monkeypatch):
    """An UNTAGGED file (no title) that the title-based recording search can't place
    is rescued by AcoustID: fingerprint → recording MBID → release → match. Gated on
    a key + a path; without a key it stays unpaired (no_match)."""
    async def _empty(*a, **k):
        return []
    async def _none(*a, **k):
        return None
    async def fake_recording_releases(client, mbid):
        return "rel-aid"
    monkeypatch.setattr(matcher.mb, "search_releases", _empty)        # no album resolves
    monkeypatch.setattr(matcher.mb, "get_release", _none)
    monkeypatch.setattr(matcher.mb, "search_recordings", _empty)
    monkeypatch.setattr(matcher.mb, "get_recording_releases", fake_recording_releases)

    import kira.music.acoustid as _ac
    async def fake_identify(client, path, key, **k):
        return _ac.AcoustIdMatch(recording_mbid="rec-aid", title="Yummy", artist="Justin Bieber", score=0.95)
    monkeypatch.setattr(_ac, "identify", fake_identify)

    files = [MusicFile(1, MusicTags(album="Singles"), path="/m/track_07.flac")]   # NO title/artist
    out = {m.file_id: m for m in await match_album(None, files, acoustid_key="my-key")}
    assert out[1].matched_via == "acoustid" and out[1].title == "Yummy"
    assert out[1].artist == "Justin Bieber" and out[1].recording_id == "rec-aid"
    assert out[1].cover_art_url.endswith("/release/rel-aid/front-500")
    # No key → no fallback → the cluster stays no_match (matched_via unpaired → []).
    assert await match_album(None, files, acoustid_key=None) == []


@pytest.mark.asyncio
async def test_singles_album_skips_release_resolution(monkeypatch):
    """A "Singles" folder is NOT a real album: match_album must NOT resolve a release
    (which would force unrelated songs onto one wrong album → "24 files, 1 track" +
    false duplicates), even if search_releases WOULD return a hit. It goes straight
    to per-recording matching so each song stays a distinct track in one group."""
    searched = {"album": False}

    async def fake_search(client, artist, album, *, track_count=None, limit=12):
        searched["album"] = True   # a (wrong) hit that must NOT be used
        return [MBReleaseHit(id="rel-x", title="Singles", artist="Justin Bieber", date="2020", track_count=1, score=100)]

    async def fake_get_release(*a, **k):
        return None

    async def fake_recordings(client, artist, title, *, limit=5):
        return [MBRecordingHit(recording_id=f"rec-{title}", title=title, artist=artist,
                               release_id="rel-s", release_title="Single", date="2019", score=100)]

    monkeypatch.setattr(matcher.mb, "search_releases", fake_search)
    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    monkeypatch.setattr(matcher.mb, "search_recordings", fake_recordings)
    files = [
        MusicFile(1, MusicTags(artist="Justin Bieber & Ariana Grande", album="Singles", title="Stuck with U")),
        MusicFile(2, MusicTags(artist="Justin Bieber", album="Singles", title="Anyone")),
    ]
    out = {m.file_id: m for m in await match_album(None, files)}
    assert searched["album"] is False                       # album search SKIPPED for "Singles"
    assert out[1].matched_via == "recording" and out[1].title == "Stuck with U"
    assert out[2].matched_via == "recording" and out[2].title == "Anyone"
    # distinct tracks (no collapse) + one shared synthetic group (one "Singles" card)
    assert out[1].track_no == 1 and out[2].track_no == 2
    assert out[1].release_id == out[2].release_id and out[1].release_id.startswith("loose:")


@pytest.mark.asyncio
async def test_heterogeneous_singles_skip_release_resolution(monkeypatch):
    """A Singles folder: per-file DIFFERENT album tags + release ids (each
    single is its own release). Resolving any ONE release force-matched every
    unrelated song onto it ("35 files · 1 track", 34 false duplicates offered
    for deletion). The heterogeneity guard must route the cluster to the
    per-recording path without touching release resolution."""
    async def boom_get_release(client, mbid):
        raise AssertionError("release resolution must not run for a heterogeneous singles cluster")

    async def boom_search(*a, **k):
        raise AssertionError("album search must not run for a heterogeneous singles cluster")

    recorded: list[list[MusicFile]] = []

    async def fake_recordings(client, files, *, acoustid_key=None):
        recorded.append(files)
        return []

    monkeypatch.setattr(matcher.mb, "get_release", boom_get_release)
    monkeypatch.setattr(matcher.mb, "search_releases", boom_search)
    monkeypatch.setattr(matcher, "_match_by_recordings", fake_recordings)

    files = [
        MusicFile(1, MusicTags(artist="Justin Bieber", album="Monster", title="Monster", track_no=1, mb_release_id="rel-m")),
        MusicFile(2, MusicTags(artist="Justin Bieber", album="Sorry", title="Sorry", track_no=1, mb_release_id="rel-s")),
        MusicFile(3, MusicTags(artist="Justin Bieber", album="Flatline", title="Flatline", track_no=1, mb_release_id="rel-f")),
        MusicFile(4, MusicTags(artist="Justin Bieber", album="One Time", title="One Time", track_no=1, mb_release_id="rel-o")),
    ]
    out = await match_album(None, files)
    assert out == []
    assert len(recorded) == 1 and len(recorded[0]) == 4


@pytest.mark.asyncio
async def test_singles_folder_name_routes_to_recordings(monkeypatch):
    """Even with homogeneous-looking tags, a folder literally named 'Singles'
    is a singles bucket — per-recording matching, no release resolution."""
    async def boom_get_release(client, mbid):
        raise AssertionError("no release resolution for a Singles folder")

    async def fake_recordings(client, files, *, acoustid_key=None):
        return []

    monkeypatch.setattr(matcher.mb, "get_release", boom_get_release)
    monkeypatch.setattr(matcher, "_match_by_recordings", fake_recordings)

    files = [
        MusicFile(i, MusicTags(artist="Justin Bieber", title=f"Song {i}"), fb_album="Singles")
        for i in (1, 2, 3)
    ]
    assert await match_album(None, files) == []


@pytest.mark.asyncio
async def test_homogeneous_album_still_uses_id_bypass(monkeypatch):
    """A REAL album (one shared release id, one album tag) must keep the fast
    MBID bypass — the guard only fires on self-disagreeing clusters."""
    async def fake_get_release(client, mbid):
        assert mbid == "rel-1"
        return _release()

    monkeypatch.setattr(matcher.mb, "get_release", fake_get_release)
    files = [
        MusicFile(1, MusicTags(artist="Daft Punk", album="Discovery", track_no=1, mb_release_id="rel-1")),
        MusicFile(2, MusicTags(artist="Daft Punk", album="Discovery", track_no=2, mb_release_id="rel-1")),
        MusicFile(3, MusicTags(artist="Daft Punk", album="Discovery", title="Aerodynamic", mb_release_id="rel-1")),
    ]
    out = {m.file_id: m for m in await match_album(None, files)}
    assert out[1].matched_via == "tracknum"
