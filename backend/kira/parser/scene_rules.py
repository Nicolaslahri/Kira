"""Phase 17 — user-extensible scene-rules data.

the reference renamer ships a large, independently-refreshed `ReleaseInfo` dataset (release
groups, source/format tokens, clutter patterns). Kira keeps a curated set in
code (see `parser._FANSUB_GROUPS`, `format_stripper`'s token tuples), but a
power user inevitably has a release group or quality token we don't know yet.

This module reads an OPTIONAL user JSON so they can teach Kira without editing
source. Nothing ships in it by default — absent file → empty extras → the
in-code curated sets are used unchanged. Read once at import; edits take
effect on the next restart.

Location (first that exists):
  1. ``$KIRA_SCENE_RULES``  (explicit path)
  2. ``<backend>/.cache/scene-rules.json``

Format (all keys optional; every value is a list of strings):
  {
    "fansub_groups": ["mygroup", "another-group"],
    "sources":       ["MYNET", "UHD2"],
    "codecs":        ["mycodec"],
    "resolutions":   ["1440p"],
    "audio":         ["DTS-HD.MA"],
    "subtitles":     ["VOSTFR"],
    "editions":      ["My Special Cut"],
    "hdr":           ["HDR10++"],
    "release_flags": ["MYSCENEFLAG"]
  }

User tokens are matched literally (regex-escaped) and fold ON TOP of the
in-code curated tables — they never replace them. Source / codec / resolution /
audio / subtitle / edition / HDR extras are case-INSENSITIVE; `release_flags`
extras are matched CASE-SENSITIVELY (kept as authored) because release flags
strip case-sensitively to protect real title words. Changes take effect on the
next restart, or immediately via ``format_stripper.reload_rules()``.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

_CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


def _rules_path() -> Path:
    env = os.environ.get("KIRA_SCENE_RULES")
    if env:
        return Path(env)
    return _CACHE_DIR / "scene-rules.json"


def load_rules() -> dict:
    """Return the parsed user rules dict, or {} when absent / malformed."""
    p = _rules_path()
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:  # never let a bad user file break parsing
        print(f"scene_rules: ignoring unreadable {p}: {e!r}")
    return {}


def _str_set(values) -> set[str]:
    """Lowercased set — for case-INSENSITIVE token tables."""
    if not isinstance(values, list):
        return set()
    return {str(v).strip().lower() for v in values if str(v).strip()}


def _str_set_cased(values) -> set[str]:
    """Case-PRESERVED set — for tables matched case-sensitively (release flags)."""
    if not isinstance(values, list):
        return set()
    return {str(v).strip() for v in values if str(v).strip()}


def extra_fansub_groups() -> set[str]:
    """User-supplied additional anime release/sub group names (lowercased)."""
    return _str_set(load_rules().get("fansub_groups"))


# ── format_stripper token-table extras (folded in by `format_stripper._build`) ──
# All case-insensitive except release_flags, which strips case-sensitively.

def extra_sources() -> set[str]:
    return _str_set(load_rules().get("sources"))


def extra_codecs() -> set[str]:
    return _str_set(load_rules().get("codecs"))


def extra_resolutions() -> set[str]:
    return _str_set(load_rules().get("resolutions"))


def extra_audio() -> set[str]:
    return _str_set(load_rules().get("audio"))


def extra_subtitles() -> set[str]:
    return _str_set(load_rules().get("subtitles"))


def extra_editions() -> set[str]:
    return _str_set(load_rules().get("editions"))


def extra_hdr() -> set[str]:
    return _str_set(load_rules().get("hdr"))


def extra_release_flags() -> set[str]:
    """Case-preserved — release flags strip case-sensitively to protect titles."""
    return _str_set_cased(load_rules().get("release_flags"))
