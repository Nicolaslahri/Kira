"""Music embedded-tag reader — the gold signal for the matcher. mutagen.File is
mocked so this runs with no real audio files; the field mapping + MBID/AcoustID
capture + the untagged/non-audio fallbacks are what matter."""
from __future__ import annotations

import kira.music.tags as tagmod
from kira.music.tags import _num_pair, _year_from, read_tags


def test_num_pair():
    assert _num_pair("5") == (5, None)
    assert _num_pair("5/12") == (5, 12)
    assert _num_pair(None) == (None, None)
    assert _num_pair("") == (None, None)
    assert _num_pair("A/B") == (None, None)


def test_year_from():
    assert _year_from("2009-05-01") == 2009
    assert _year_from("2009") == 2009
    assert _year_from(None) is None
    assert _year_from("nope") is None


class _Info:
    def __init__(self, length):
        self.length = length


class _Audio:
    def __init__(self, tags, length=200.0):
        self.tags = tags
        self.info = _Info(length)


def _patch_file(monkeypatch, audio):
    monkeypatch.setattr(tagmod.mutagen, "File", lambda *a, **k: audio)


def test_read_tags_full(monkeypatch):
    tags = {
        "artist": ["Daft Punk"],
        "albumartist": ["Daft Punk"],
        "album": ["Discovery"],
        "title": ["One More Time"],
        "tracknumber": ["1/14"],
        "discnumber": ["1/1"],
        "date": ["2001-03-12"],
        "genre": ["Electronic"],
        "musicbrainz_albumid": ["rel-123"],
        "musicbrainz_trackid": ["rec-456"],
        "musicbrainz_artistid": ["art-789"],
        "musicbrainz_releasegroupid": ["rg-000"],
        "acoustid_id": ["acid-1"],
        "acoustid_fingerprint": ["AQAD..."],
    }
    _patch_file(monkeypatch, _Audio(tags, length=320.4))
    t = read_tags("/x.flac")
    assert t.artist == "Daft Punk" and t.album_artist == "Daft Punk"
    assert t.album == "Discovery" and t.title == "One More Time"
    assert (t.track_no, t.track_total) == (1, 14)
    assert (t.disc_no, t.disc_total) == (1, 1)
    assert t.year == 2001 and t.date == "2001-03-12"
    assert t.duration == 320.4
    assert t.mb_release_id == "rel-123" and t.mb_recording_id == "rec-456"
    assert t.mb_artist_id == "art-789" and t.mb_release_group_id == "rg-000"
    assert t.acoustid_id == "acid-1" and t.acoustid_fingerprint == "AQAD..."
    assert t.has_mbid is True


def test_read_tags_untagged_keeps_duration(monkeypatch):
    _patch_file(monkeypatch, _Audio(None, length=180.0))
    t = read_tags("/x.mp3")
    assert t is not None and t.duration == 180.0
    assert t.artist is None and t.has_mbid is False


def test_read_tags_non_audio_returns_none(monkeypatch):
    _patch_file(monkeypatch, None)
    assert read_tags("/x.txt") is None
