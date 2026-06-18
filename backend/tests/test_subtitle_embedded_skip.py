"""`languages_needing_fetch` embedded-track awareness (kira/subtitles/_common.py).

The bug: the subtitle auto-fetch downloaded an EXTERNAL sub for a language that
was already EMBEDDED in the video container (a One Piece .mkv with `eng` text
tracks still got an external `.en.srt`). The fetch filter only looked at sidecar
files on disk, never the container's own tracks. These pin the fix:

  • an embedded language (3-letter `eng`, as MediaInfo emits) drops a wanted
    2-letter `en` via the shared normalization helper, while other wanted langs
    are kept;
  • `embedded=None` is the unchanged old behavior (MediaInfo may not have run) —
    nothing is dropped on that basis;
  • an existing on-disk sidecar still drops its language regardless of embedded.
"""

from __future__ import annotations

from kira.subtitles import _common
from kira.subtitles.naming import subtitle_sidecar_name


def test_embedded_lang_dropped_other_kept():
    # `eng` embedded (MediaInfo's 3-letter code) satisfies a wanted `en`;
    # `es` has no embedded track, so it survives for an external fetch.
    assert _common.languages_needing_fetch(
        "/x/Show.mkv", ["en", "es"], embedded=["eng"]) == ["es"]


def test_embedded_none_keeps_all():
    # No embedded info (MediaInfo didn't run) → unchanged old behavior: both
    # wanted langs remain (nothing on disk either).
    assert _common.languages_needing_fetch(
        "/x/Show.mkv", ["en", "es"], embedded=None) == ["en", "es"]


def test_embedded_empty_keeps_all():
    # An empty embedded set is treated like None — no language dropped on that
    # basis (so a container with zero text tracks doesn't suppress fetches).
    assert _common.languages_needing_fetch(
        "/x/Show.mkv", ["en", "es"], embedded=[]) == ["en", "es"]


def test_existing_sidecar_still_drops_its_language(tmp_path):
    # The pre-existing on-disk-sidecar drop must keep working unchanged: an
    # `.en.srt` already beside the video removes `en` even with embedded=None.
    video = tmp_path / "Show - S01E01.mkv"
    video.write_text("video")
    (tmp_path / subtitle_sidecar_name(str(video), "en", ext="srt")).write_text("subs")

    assert _common.languages_needing_fetch(
        str(video), ["en", "es"], embedded=None) == ["es"]


def test_embedded_and_sidecar_compose(tmp_path):
    # Embedded `eng` drops `en`; an on-disk `.es.srt` drops `es`; the
    # un-satisfied `fr` is the only one left to fetch.
    video = tmp_path / "Show - S01E01.mkv"
    video.write_text("video")
    (tmp_path / subtitle_sidecar_name(str(video), "es", ext="srt")).write_text("subs")

    assert _common.languages_needing_fetch(
        str(video), ["en", "es", "fr"], embedded=["eng"]) == ["fr"]
