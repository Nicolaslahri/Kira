"""write_tags — the matched metadata is written INTO the audio file's embedded
tags (music's canonical metadata). mutagen mocked: validates the field mapping
and the best-effort contract (never raises; False on an unreadable file)."""
from __future__ import annotations

import kira.music.tags as tagsmod


class _FakeAudio(dict):
    saved = False

    def save(self):
        self.saved = True


def test_write_tags_maps_all_fields(monkeypatch):
    fake = _FakeAudio()
    monkeypatch.setattr(tagsmod.mutagen, "File", lambda path, easy=False: fake)
    ok = tagsmod.write_tags(
        "x.flac", artist="Justin Bieber", album="My World", title="One Time",
        track_no=1, track_total=7, disc_no=1, year=2009,
        mb_release_id="rel-1", mb_recording_id="rec-1", mb_artist_id="art-1",
    )
    assert ok and fake.saved
    assert fake["artist"] == ["Justin Bieber"]
    assert fake["albumartist"] == ["Justin Bieber"]          # falls back to artist
    assert fake["album"] == ["My World"] and fake["title"] == ["One Time"]
    assert fake["tracknumber"] == ["1/7"] and fake["discnumber"] == ["1"]
    assert fake["date"] == ["2009"]
    assert fake["musicbrainz_trackid"] == ["rec-1"]          # recording mbid by convention
    assert fake["musicbrainz_albumid"] == ["rel-1"]
    assert fake["musicbrainz_artistid"] == ["art-1"]


def test_write_tags_skips_empty_and_is_best_effort(monkeypatch):
    fake = _FakeAudio()
    monkeypatch.setattr(tagsmod.mutagen, "File", lambda path, easy=False: fake)
    assert tagsmod.write_tags("x.flac", artist="A", album=None, track_no=3) is True
    assert "album" not in fake and "title" not in fake        # empties skipped
    assert fake["tracknumber"] == ["3"]                       # no total → bare number

    # Unreadable / non-audio → None from mutagen → False, no raise.
    monkeypatch.setattr(tagsmod.mutagen, "File", lambda path, easy=False: None)
    assert tagsmod.write_tags("x.flac", artist="A") is False

    # mutagen raising → still best-effort False.
    def _boom(path, easy=False):
        raise OSError("locked")
    monkeypatch.setattr(tagsmod.mutagen, "File", _boom)
    assert tagsmod.write_tags("x.flac", artist="A") is False
