"""Phase 17 — base token-table externalization (release_tokens.json)."""

from __future__ import annotations

import json

from kira.parser import format_stripper as fs
from kira.parser import parse_filename


def test_shipped_data_file_loads_and_matches_in_code_defaults() -> None:
    """The shipped JSON exists, is valid, and carries every curated table —
    so loading it changes nothing vs the in-code defaults."""
    base = fs._load_base_tables()
    assert base, "release_tokens.json should load as a non-empty dict"
    for key in ("sources", "codecs", "resolutions", "audio",
                "subtitles", "editions", "hdr", "release_flags"):
        assert key in base and isinstance(base[key], list) and base[key]
    # wxh map + structural tables present too.
    assert isinstance(base.get("wxh_to_p"), dict)
    assert base.get("bit_depth")
    assert base.get("sources_ambiguous")


def test_base_list_falls_back_when_key_absent() -> None:
    assert fs._base_list({}, "sources", ["FALLBACK"]) == ["FALLBACK"]
    assert fs._base_list({"sources": []}, "sources", ["FALLBACK"]) == ["FALLBACK"]
    assert fs._base_list({"sources": ["A", "B"]}, "sources", ["X"]) == ["A", "B"]


def test_missing_data_file_falls_back_to_in_code(monkeypatch, tmp_path) -> None:
    """Point the loader at a nonexistent file → {} → in-code literals used,
    and stripping still works (regression guard for a missing data file)."""
    monkeypatch.setattr(fs, "_BASE_TABLE_FILE", tmp_path / "nope.json")
    try:
        assert fs._load_base_tables() == {}
        fs.reload_rules()  # rebuilds from in-code defaults
        parsed = parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
        assert parsed.quality == "1080p"
        assert parsed.source == "BluRay"
        assert parsed.codec == "x264"
    finally:
        fs.reload_rules()  # restore the real shipped tables


def test_custom_data_file_drives_stripping(monkeypatch, tmp_path) -> None:
    """A data file with a NEW source token strips that token."""
    p = tmp_path / "release_tokens.json"
    p.write_text(json.dumps({"sources": ["MYSOURCE", "BluRay"]}), encoding="utf-8")
    monkeypatch.setattr(fs, "_BASE_TABLE_FILE", p)
    try:
        fs.reload_rules()
        parsed = parse_filename("Show.S01E01.MYSOURCE.mkv")
        assert parsed.source == "MYSOURCE"
        assert "MYSOURCE" not in parsed.title
    finally:
        fs.reload_rules()


def test_malformed_data_file_is_ignored(monkeypatch, tmp_path) -> None:
    p = tmp_path / "release_tokens.json"
    p.write_text("{ not valid json", encoding="utf-8")
    monkeypatch.setattr(fs, "_BASE_TABLE_FILE", p)
    try:
        assert fs._load_base_tables() == {}
        fs.reload_rules()
        # Still strips via in-code fallback.
        parsed = parse_filename("Movie.2020.720p.WEB-DL.mkv")
        assert parsed.quality == "720p"
    finally:
        fs.reload_rules()
