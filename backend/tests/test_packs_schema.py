"""Pack schema validation + the ReDoS sanitizer + the override⇒scope rule."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from kira.packs.schema import (
    MAX_REGEX_LEN,
    Pack,
    PackBinding,
    PackValidationError,
    compile_safe,
    parse_pack,
    safe_search,
    url_hash,
)

VALID = {
    "kira_pack": 1,
    "id": "one-pace",
    "name": "One Pace",
    "media_type": "anime",
    "show": {"title": "One Pace", "aliases": ["One Piece (One Pace)"], "year": 1999},
    "match": {"titles": ["One Pace"], "release_groups": ["One Pace"],
              "filename_regex": r"(?i)\bone[ ._-]?pace\b"},
    "episodes": [
        {"season": 1, "episode": 1, "title": "Romance Dawn 01",
         "match": {"crc32": "A1B2C3D4", "regex": r"Romance Dawn 0?1\b",
                   "arc": "Romance Dawn", "arc_episode": 1},
         "subs": [{"lang": "en", "url": "https://example.com/rd01.en.ass",
                   "format": "ass", "sync": "guaranteed"}]},
    ],
}


def test_valid_pack_parses():
    pack = parse_pack(VALID)
    assert pack.id == "one-pace"
    assert pack.episodes[0].match.crc32 == "a1b2c3d4"   # normalized lower
    assert pack.episodes[0].subs[0].sync == "guaranteed"


def test_unsupported_version_rejected():
    bad = {**VALID, "kira_pack": 99}
    with pytest.raises(PackValidationError):
        parse_pack(bad)


def test_bad_id_rejected():
    with pytest.raises(PackValidationError):
        parse_pack({**VALID, "id": "has spaces/slash"})


def test_pack_needs_a_match_signal():
    bare = {"kira_pack": 1, "id": "x", "name": "x",
            "show": {"title": ""}, "match": {}, "episodes": []}
    with pytest.raises(PackValidationError):
        parse_pack(bare)


# ── ReDoS sanitizer ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("evil", [
    r"(a+)+b",          # classic nested quantifier
    r"(a*)*",
    r"(a|aa)+",         # quantified overlapping alternation
    r"((x+)+)+",
])
def test_catastrophic_regex_rejected(evil):
    with pytest.raises(PackValidationError):
        compile_safe(evil)


@pytest.mark.parametrize("ok", [
    r"(?i)\bone[ ._-]?pace\b",
    r"Romance Dawn 0?1\b",
    r"[A-Z]{2,4}-\d+",
    r"(?:foo|bar)",      # alternation WITHOUT a quantifier on the group → fine
])
def test_safe_regex_compiles(ok):
    c = compile_safe(ok)
    assert c is not None


def test_overlong_regex_rejected():
    with pytest.raises(PackValidationError):
        compile_safe("a" * (MAX_REGEX_LEN + 1))


def test_safe_search_caps_and_matches():
    c = compile_safe(r"one pace")
    assert safe_search(c, "this is one pace ep 1") is True
    assert safe_search(c, "one piece") is False
    assert safe_search(None, "anything") is False


# ── Bindings ────────────────────────────────────────────────────────────────
def test_override_requires_scope():
    # The model validator raises a ValueError, which pydantic surfaces as its
    # own ValidationError on direct construction (the API maps this to a 422).
    with pytest.raises(ValidationError):
        PackBinding(url="https://x/y.json", id="op", authority="override", scope_paths=[])
    with pytest.raises(ValidationError):
        PackBinding(url="https://x/y.json", id="op", authority="override", scope_paths=["  "])


def test_override_with_scope_ok():
    b = PackBinding(url="https://x/y.json", id="op", authority="override",
                    scope_paths=["Z:/anime/One Pace"])
    assert b.authority == "override"


def test_fallback_needs_no_scope():
    b = PackBinding(url="https://x/y.json", id="op")
    assert b.authority == "fallback"
    assert b.scope_paths == []


def test_url_hash_distinguishes_forks():
    # Same id, different URL → different hash → different binding key + group id.
    a = PackBinding(url="https://a.com/one-pace.json", id="one-pace")
    b = PackBinding(url="https://b.com/one-pace.json", id="one-pace")
    assert url_hash(a.url) != url_hash(b.url)
    assert a.key != b.key


def test_subs_cap_enforced():
    many = {**VALID, "episodes": [
        {"season": 1, "episode": 1,
         "subs": [{"lang": "en", "url": f"https://x/{i}.srt"} for i in range(25)]}
    ]}
    with pytest.raises(PackValidationError):
        parse_pack(many)
