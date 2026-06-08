"""MediaInfo embedded-title rescue — last-ditch identity for files the
filename couldn't crack. Additive: only fires for no-title / 'unknown' files
(which would otherwise never match), and only adopts a re-parse that actually
yields a title without regressing a file that already had one."""

from __future__ import annotations

from kira.api import scans as scans_mod
from kira.parser import mediainfo
from kira.models import MediaFile


async def test_rescue_unknown_file_via_embedded_title(monkeypatch) -> None:
    monkeypatch.setattr(scans_mod._mediainfo, "available", lambda: True)
    monkeypatch.setattr(scans_mod._mediainfo, "read_embedded_title", lambda p: "Inception (2010)")
    mf = MediaFile(file_path="Z:/dump/x7f3a9.mkv", media_type="unknown",
                   parsed_data={"title": None}, status="discovered")
    rescued = await scans_mod._maybe_rescue_title_from_mediainfo(mf)
    assert rescued is True
    assert "inception" in (mf.parsed_data.get("title") or "").lower()
    assert mf.media_type == "movie"           # year present → movie, no longer 'unknown'
    assert mf.parsed_data.get("year") == 2010  # parsed_data fully re-derived from the title


async def test_rescue_skips_file_that_already_has_a_title(monkeypatch) -> None:
    reads: list[str] = []
    monkeypatch.setattr(scans_mod._mediainfo, "available", lambda: True)
    monkeypatch.setattr(scans_mod._mediainfo, "read_embedded_title",
                        lambda p: reads.append(p) or "Whatever")
    mf = MediaFile(file_path="Z:/tv/Show/Show.S01E01.mkv", media_type="tv",
                   parsed_data={"title": "Show", "season": 1, "episode": 1}, status="discovered")
    assert await scans_mod._maybe_rescue_title_from_mediainfo(mf) is False
    assert reads == []                        # short-circuits BEFORE the MediaInfo read


async def test_rescue_noop_when_no_embedded_title(monkeypatch) -> None:
    monkeypatch.setattr(scans_mod._mediainfo, "available", lambda: True)
    monkeypatch.setattr(scans_mod._mediainfo, "read_embedded_title", lambda p: None)
    mf = MediaFile(file_path="Z:/dump/x7f3a9.mkv", media_type="unknown",
                   parsed_data={}, status="discovered")
    assert await scans_mod._maybe_rescue_title_from_mediainfo(mf) is False


async def test_rescue_noop_when_lib_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(scans_mod._mediainfo, "available", lambda: False)
    mf = MediaFile(file_path="Z:/dump/x7f3a9.mkv", media_type="unknown",
                   parsed_data={}, status="discovered")
    assert await scans_mod._maybe_rescue_title_from_mediainfo(mf) is False


def test_read_embedded_title_graceful_without_lib(monkeypatch) -> None:
    monkeypatch.setattr(mediainfo, "_AVAILABLE", False)
    assert mediainfo.read_embedded_title("anything.mkv") is None
