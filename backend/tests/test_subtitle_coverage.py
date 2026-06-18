"""Pure-logic tests for subtitle coverage detection."""
from __future__ import annotations

from kira.subtitles.coverage import (
    has_been_inspected,
    missing_languages,
    normalize_langs,
    present_languages,
    scan_sidecar_langs,
    sidecar_lang,
)


def test_normalize_folds_three_letter_and_names():
    assert normalize_langs(["eng", "en", "English", "jpn"]) == {"en", "ja"}


def test_present_unions_embedded_and_sidecars():
    parsed = {"sub_langs": ["jpn"], "sub_sidecars": ["en"]}
    assert present_languages(parsed) == {"ja", "en"}


def test_present_empty_for_none():
    assert present_languages(None) == set()


def test_inspected_requires_mi_stamp_or_sidecars():
    assert has_been_inspected({"mi_stamp": [123, 456]}) is True
    assert has_been_inspected({"sub_sidecars": []}) is True   # we looked; found none
    assert has_been_inspected({"sub_langs": ["eng"]}) is False  # parsed only, never read
    assert has_been_inspected({}) is False


def test_missing_none_when_no_wanted():
    assert missing_languages({"mi_stamp": [1, 2]}, []) is None


def test_missing_none_when_never_inspected():
    # Wants English, has no read stamp → unknown, not "missing".
    assert missing_languages({"sub_langs": ["eng"]}, ["en"]) is None


def test_missing_empty_when_covered_by_embedded():
    parsed = {"mi_stamp": [1, 2], "sub_langs": ["eng", "jpn"]}
    assert missing_languages(parsed, ["en"]) == []


def test_missing_empty_when_covered_by_sidecar():
    parsed = {"mi_stamp": [1, 2], "sub_langs": ["jpn"], "sub_sidecars": ["en"]}
    assert missing_languages(parsed, ["en"]) == []


def test_missing_reports_wanted_codes_in_order():
    parsed = {"mi_stamp": [1, 2], "sub_langs": ["jpn"]}
    assert missing_languages(parsed, ["en", "es"]) == ["en", "es"]


def test_missing_partial_coverage():
    parsed = {"mi_stamp": [1, 2], "sub_langs": ["eng"]}
    assert missing_languages(parsed, ["en", "es"]) == ["es"]


def test_sidecar_lang_extraction():
    assert sidecar_lang("Show.S01E01", "Show.S01E01.en.srt") == "en"
    assert sidecar_lang("Show.S01E01", "Show.S01E01.eng.ass") == "en"
    assert sidecar_lang("Show.S01E01", "Show.S01E01.srt") is None       # no lang tag
    assert sidecar_lang("Show.S01E01", "Other.S01E02.en.srt") is None   # wrong stem
    assert sidecar_lang("Show.S01E01", "Show.S01E01.en.forced.srt") is None  # multi-seg
    assert sidecar_lang("Show.S01E01", "Show.S01E01.en.txt") is None    # not a sub ext


def test_scan_sidecar_langs(tmp_path):
    vid = tmp_path / "Show.S01E01.mkv"
    vid.write_text("x")
    (tmp_path / "Show.S01E01.en.srt").write_text("x")
    (tmp_path / "Show.S01E01.es.ass").write_text("x")
    (tmp_path / "Show.S01E01.srt").write_text("x")        # untagged → ignored
    (tmp_path / "Other.mkv").write_text("x")              # unrelated
    result = scan_sidecar_langs([str(vid)])
    assert set(result[str(vid)]) == {"en", "es"}


def test_scan_sidecar_langs_one_listing_per_parent(tmp_path, monkeypatch):
    import kira.subtitles.coverage as cov
    a = tmp_path / "a.mkv"; a.write_text("x")
    b = tmp_path / "b.mkv"; b.write_text("x")
    (tmp_path / "a.en.srt").write_text("x")
    calls = {"n": 0}
    real = cov.os.scandir

    def counting(path):
        calls["n"] += 1
        return real(path)

    monkeypatch.setattr(cov.os, "scandir", counting)
    cov.scan_sidecar_langs([str(a), str(b)])
    assert calls["n"] == 1  # both files share one parent → one scandir
