"""Embedded subtitle extraction (kira/subtitles/embedded.py).

Pull TEXT subtitle tracks already inside an MKV/MP4 out to language-tagged
sidecars — offline, no key, the highest-yield source for anime. These pin the
pure logic (language + codec mapping, track enumeration with image-sub skipping)
and the extract orchestration (track selection, exists-skip, forced fallback)
with MediaInfo + ffmpeg mocked, so no native lib or real video is needed.
"""

from __future__ import annotations

import pytest

from kira.subtitles import embedded


class _T:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Info:
    def __init__(self, tracks):
        self.tracks = tracks


# ── pure helpers ──────────────────────────────────────────────────────────
def test_normalize_lang():
    assert embedded.normalize_lang("eng") == "en"
    assert embedded.normalize_lang("English") == "en"
    assert embedded.normalize_lang("jpn") == "ja"
    assert embedded.normalize_lang("Japanese") == "ja"
    assert embedded.normalize_lang("en") == "en"
    assert embedded.normalize_lang("kli") == "kli"     # unknown → lowercased raw
    assert embedded.normalize_lang(None) is None
    assert embedded.normalize_lang("  ") is None


def test_codec_to_ext():
    assert embedded.codec_to_ext("UTF-8") == "srt"
    assert embedded.codec_to_ext("SubRip") == "srt"
    assert embedded.codec_to_ext("ASS") == "ass"
    assert embedded.codec_to_ext("SSA") == "ass"
    assert embedded.codec_to_ext("WebVTT") == "vtt"
    assert embedded.codec_to_ext("PGS") is None        # image sub → no text sidecar
    assert embedded.codec_to_ext("VobSub") is None
    assert embedded.codec_to_ext(None) is None


# ── track enumeration ───────────────────────────────────────────────────────
def test_list_text_tracks_indices_and_image_skip(monkeypatch):
    tracks = [
        _T(track_type="Video"),
        _T(track_type="Audio", language="jpn"),
        _T(track_type="Text", language="eng", format="UTF-8", title="English", forced="No"),
        _T(track_type="Text", language="jpn", format="ASS", title="Signs", forced="No"),
        _T(track_type="Text", language="eng", format="PGS", title="SDH", forced="No"),  # image
        _T(track_type="Text", language="spa", format="SubRip", title="Spanish", forced="Yes"),
    ]
    monkeypatch.setattr(embedded, "_MI_AVAILABLE", True)
    monkeypatch.setattr(embedded, "_MediaInfo",
                        type("MI", (), {"parse": staticmethod(lambda p: _Info(tracks))}))

    got = embedded.list_text_tracks("x.mkv")
    # 3 text tracks emitted (the PGS image sub is skipped) ...
    assert [(t["sindex"], t["lang"], t["ext"], t["forced"]) for t in got] == [
        (0, "en", "srt", False),   # 1st text stream
        (1, "ja", "ass", False),   # 2nd text stream
        # 3rd text stream (PGS) skipped — but its ordinal (2) is consumed so ...
        (3, "es", "srt", True),    # ... the 4th text stream keeps ffmpeg index 3
    ]


def test_list_text_tracks_no_lib(monkeypatch):
    monkeypatch.setattr(embedded, "_MI_AVAILABLE", False)
    assert embedded.list_text_tracks("x.mkv") == []


# ── extract orchestration ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_extract_picks_right_track_per_language(monkeypatch, tmp_path):
    video = str(tmp_path / "Show - S01E01.mkv")
    monkeypatch.setattr(embedded, "available", lambda: True)
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 0, "lang": "en", "ext": "srt", "title": "English", "forced": False},
        {"sindex": 1, "lang": "ja", "ext": "ass", "title": "JP", "forced": False},
    ])
    calls: list[tuple[int, str]] = []

    async def _fake_ffmpeg(vp, sindex, dest):
        calls.append((sindex, dest))
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    saved = await embedded.extract(video, ["en", "ja"])
    assert len(saved) == 2
    assert calls[0][0] == 0 and calls[0][1].endswith(".en.srt")
    assert calls[1][0] == 1 and calls[1][1].endswith(".ja.ass")   # native ASS kept


@pytest.mark.asyncio
async def test_extract_skips_when_sidecar_exists(monkeypatch, tmp_path):
    video = tmp_path / "Show - S01E01.mkv"
    (tmp_path / "Show - S01E01.en.srt").write_text("existing")   # already have EN
    monkeypatch.setattr(embedded, "available", lambda: True)
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 0, "lang": "en", "ext": "srt", "title": "EN", "forced": False},
    ])
    called = False

    async def _fake_ffmpeg(vp, sindex, dest):
        nonlocal called
        called = True
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    saved = await embedded.extract(str(video), ["en"])
    assert saved == [] and called is False     # skipped — never shelled out


@pytest.mark.asyncio
async def test_extract_prefers_non_forced_then_falls_back(monkeypatch, tmp_path):
    video = str(tmp_path / "Movie.mkv")
    monkeypatch.setattr(embedded, "available", lambda: True)
    # Two EN tracks: a forced (signs) one first, a full one second.
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 0, "lang": "en", "ext": "srt", "title": "Signs", "forced": True},
        {"sindex": 1, "lang": "en", "ext": "srt", "title": "Full", "forced": False},
    ])
    picked: list[int] = []

    async def _fake_ffmpeg(vp, sindex, dest):
        picked.append(sindex)
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    await embedded.extract(video, ["en"])
    assert picked == [1]     # the non-forced full track, not the forced one


@pytest.mark.asyncio
async def test_extract_forced_only_prefers_forced_track(monkeypatch, tmp_path):
    """`forced=only` flips the choice — the signs/songs track wins over the full
    one. (The bug: this path used to always hand back the non-forced track.)"""
    video = str(tmp_path / "Movie.mkv")
    monkeypatch.setattr(embedded, "available", lambda: True)
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 0, "lang": "en", "ext": "srt", "title": "Signs", "forced": True},
        {"sindex": 1, "lang": "en", "ext": "srt", "title": "Full", "forced": False},
    ])
    picked: list[int] = []

    async def _fake_ffmpeg(vp, sindex, dest):
        picked.append(sindex)
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    await embedded.extract(video, ["en"], forced="only")
    assert picked == [0]     # the forced signs track, as asked


@pytest.mark.asyncio
async def test_extract_forced_only_falls_back_to_full(monkeypatch, tmp_path):
    """`forced=only` with no forced track present still saves the full one
    (soft fallback — better a sub than none, mirroring the external path)."""
    video = str(tmp_path / "Movie.mkv")
    monkeypatch.setattr(embedded, "available", lambda: True)
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 1, "lang": "en", "ext": "srt", "title": "Full", "forced": False},
    ])
    picked: list[int] = []

    async def _fake_ffmpeg(vp, sindex, dest):
        picked.append(sindex)
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    await embedded.extract(video, ["en"], forced="only")
    assert picked == [1]


@pytest.mark.asyncio
async def test_extract_forced_exclude_never_emits_forced(monkeypatch, tmp_path):
    """`forced=exclude` must not hand back a forced track even when it's the
    ONLY one for the language — it saves nothing rather than the signs track."""
    video = str(tmp_path / "Movie.mkv")
    monkeypatch.setattr(embedded, "available", lambda: True)
    monkeypatch.setattr(embedded, "list_text_tracks", lambda p: [
        {"sindex": 0, "lang": "en", "ext": "srt", "title": "Signs", "forced": True},
    ])
    called = False

    async def _fake_ffmpeg(vp, sindex, dest):
        nonlocal called
        called = True
        return True
    monkeypatch.setattr(embedded, "_ffmpeg_extract", _fake_ffmpeg)

    saved = await embedded.extract(video, ["en"], forced="exclude")
    assert saved == [] and called is False


@pytest.mark.asyncio
async def test_extract_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(embedded, "available", lambda: False)
    assert await embedded.extract("x.mkv", ["en"]) == []
