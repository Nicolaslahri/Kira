"""Pure-parse tests for the additional subtitle providers (SubDL, Podnapisi,
SubSource, AnimeTosho) + the shared zip/save helpers.

These lock the PARSING contract — the network shapes for SubSource/AnimeTosho
are marked verify-against-live in their modules, but the defensive parsers must
behave predictably on the shapes we DO expect, and must never raise."""
from __future__ import annotations

import io
import zipfile

from kira.subtitles import _common
from kira.subtitles.animetosho import parse_subtitle_links
from kira.subtitles.podnapisi import parse_results
from kira.subtitles.subdl import download_url, parse_subtitles as subdl_parse
from kira.subtitles.subsource import parse_movie_id, parse_subtitles as subsource_parse


# ── SubDL ────────────────────────────────────────────────────────────
def test_subdl_parse_normalizes_language_and_filters():
    payload = {"subtitles": [
        {"lang": "EN", "url": "/subtitle/a.zip", "release_name": "A"},
        {"language": "Japanese", "url": "/subtitle/b.zip"},
        {"lang": "FR", "url": "/subtitle/c.zip"},          # not wanted → dropped
        {"lang": "EN"},                                     # no url → dropped
    ]}
    got = subdl_parse(payload, ["en", "ja"])
    assert [c["lang"] for c in got] == ["en", "ja"]
    assert got[0]["release"] == "A"


def test_subdl_download_url():
    assert download_url("/subtitle/x.zip") == "https://dl.subdl.com/subtitle/x.zip"
    assert download_url("https://dl.subdl.com/y.zip") == "https://dl.subdl.com/y.zip"


def test_subdl_parse_garbage_is_empty():
    assert subdl_parse({}, ["en"]) == []
    assert subdl_parse({"subtitles": "nope"}, ["en"]) == []
    assert subdl_parse(None, ["en"]) == []


# ── Podnapisi ────────────────────────────────────────────────────────
def test_podnapisi_parse_download_and_id_fallback():
    payload = {"data": [
        {"language": "en", "download": "/subtitles/abc/download"},
        {"language": "en", "id": "xyz"},                    # no download → derive
        {"language": "de"},                                  # not wanted
    ]}
    got = parse_results(payload, ["en"])
    assert got[0]["download"] == "/subtitles/abc/download"
    assert got[1]["download"] == "/subtitles/xyz/download"
    assert all(c["lang"] == "en" for c in got)


def test_podnapisi_parse_garbage_is_empty():
    assert parse_results({"data": {}}, ["en"]) == []
    assert parse_results("x", ["en"]) == []


# ── SubSource (verified-live shape: search → list → download) ────────
def test_subsource_movie_id_prefers_imdb_then_season():
    payload = {"success": True, "data": [
        {"movieId": 100, "imdbId": "tt9999", "season": None},
        {"movieId": 200, "imdbId": "tt1375666", "season": None},
    ]}
    assert parse_movie_id(payload, imdb_id="tt1375666", season=None) == 200
    # no imdb match → season match for TV
    tv = {"data": [{"movieId": 1, "season": 1}, {"movieId": 2, "season": 3}]}
    assert parse_movie_id(tv, imdb_id=None, season=3) == 2
    # fallback to first
    assert parse_movie_id({"data": [{"movieId": 7}]}, imdb_id=None, season=None) == 7


def test_subsource_parse_subtitles_filters_and_maps_fullname_langs():
    payload = {"data": [
        {"subtitleId": 1, "language": "english", "releaseInfo": ["X.S01E05"]},
        {"subtitleId": 2, "language": "brazilian_portuguese", "releaseInfo": []},  # → pt
        {"subtitleId": 3, "language": "french", "releaseInfo": []},                # not wanted
        {"subtitleId": 4, "language": "english"},                                   # ep-unmatched
    ]}
    got = subsource_parse(payload, {"en", "pt"}, episode=5)
    langs = [c["lang"] for c in got]
    assert "fr" not in langs and "pt" in langs
    # episode-matched english (S01E05) ranks before the unmatched english
    en = [c for c in got if c["lang"] == "en"]
    assert en[0]["subtitle_id"] == 1


def test_subsource_parse_garbage_is_empty():
    assert subsource_parse({"weird": 1}, {"en"}, episode=None) == []
    assert subsource_parse(42, {"en"}, episode=None) == []
    assert parse_movie_id({"data": []}, imdb_id=None, season=None) is None


# ── AnimeTosho (defensive URL scan) ──────────────────────────────────
def test_animetosho_finds_nested_sub_urls_and_prefers_episode():
    payload = [
        {"title": "Show - 07 [Group]", "files": [{"url": "https://x/storage/ep07.ass"}]},
        {"title": "Show - 06 [Group]", "attachments": [{"link": "https://x/storage/ep06.srt"}]},
    ]
    got = parse_subtitle_links(payload, episode=7)
    assert "https://x/storage/ep07.ass" in got
    assert got[0] == "https://x/storage/ep07.ass"   # episode-7 match ranked first


def test_animetosho_ignores_non_sub_urls_and_garbage():
    assert parse_subtitle_links([{"title": "x", "link": "https://x/page.html"}], None) == []
    assert parse_subtitle_links("nope", 1) == []
    assert parse_subtitle_links({"entries": []}, 1) == []


# ── Shared zip helper ────────────────────────────────────────────────
def test_subtitle_from_zip_prefers_srt():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "junk")
        zf.writestr("movie.ass", "[Script Info]")
        zf.writestr("movie.srt", "1\n00:00\nhi")
    out = _common.subtitle_from_zip(buf.getvalue())
    assert out is not None
    data, ext = out
    assert ext == "srt" and b"hi" in data


def test_subtitle_from_zip_garbage_is_none():
    assert _common.subtitle_from_zip(b"not a zip") is None


def test_subtitle_from_zip_picks_episode_from_season_pack():
    """A whole-season pack must yield the WANTED episode, not the first file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Show.S01E01.srt", "ep1")
        zf.writestr("Show.S01E02.srt", "ep2")
        zf.writestr("Show.S01E03.srt", "ep3")
    out = _common.subtitle_from_zip(buf.getvalue(), season=1, episode=2)
    assert out is not None and out[0] == b"ep2"


def test_subtitle_from_zip_episode_only_match():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("E07 - title.srt", "seven")
        zf.writestr("E08 - title.srt", "eight")
    out = _common.subtitle_from_zip(buf.getvalue(), episode=8)
    assert out is not None and out[0] == b"eight"


def test_subtitle_from_zip_no_episode_hint_takes_srt_first():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("only.ass", "a")
        zf.writestr("only.srt", "s")
    out = _common.subtitle_from_zip(buf.getvalue())
    assert out is not None and out[1] == "srt"
