"""Phase 17 — user-extensible scene rules."""

from __future__ import annotations

import json

from kira.parser import scene_rules


def test_absent_file_yields_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KIRA_SCENE_RULES", str(tmp_path / "nope.json"))
    assert scene_rules.load_rules() == {}
    assert scene_rules.extra_fansub_groups() == set()


def test_loads_user_groups(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({"fansub_groups": ["MyGroup", "Another-Group", " "]}))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    assert scene_rules.extra_fansub_groups() == {"mygroup", "another-group"}


def test_malformed_file_is_ignored(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text("{ not valid json")
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    assert scene_rules.load_rules() == {}


def test_non_list_groups_ignored(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({"fansub_groups": "notalist"}))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    assert scene_rules.extra_fansub_groups() == set()


def test_token_table_extras_lowercased(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({
        "sources": ["MyNet"],
        "codecs": ["MyCodec"],
        "resolutions": ["1440P"],
        "audio": ["DTS-HD.MA"],
        "subtitles": ["VOSTFR"],
        "editions": ["My Cut"],
        "hdr": ["HDR10++"],
    }))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    assert scene_rules.extra_sources() == {"mynet"}
    assert scene_rules.extra_codecs() == {"mycodec"}
    assert scene_rules.extra_resolutions() == {"1440p"}
    assert scene_rules.extra_audio() == {"dts-hd.ma"}
    assert scene_rules.extra_subtitles() == {"vostfr"}
    assert scene_rules.extra_editions() == {"my cut"}
    assert scene_rules.extra_hdr() == {"hdr10++"}


def test_release_flags_extras_case_preserved(monkeypatch, tmp_path) -> None:
    p = tmp_path / "scene-rules.json"
    p.write_text(json.dumps({"release_flags": ["MyFlag", "PROPER2", " "]}))
    monkeypatch.setenv("KIRA_SCENE_RULES", str(p))
    # Release flags strip case-sensitively, so their case is preserved.
    assert scene_rules.extra_release_flags() == {"MyFlag", "PROPER2"}


def test_table_extras_absent_yield_empty(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("KIRA_SCENE_RULES", str(tmp_path / "nope.json"))
    assert scene_rules.extra_sources() == set()
    assert scene_rules.extra_release_flags() == set()
