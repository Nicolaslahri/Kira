"""The pure pack resolver: the gate (which files a pack may touch) and the
episode-claim precedence ladder."""
from __future__ import annotations

from kira.packs.resolver import claim, gate, in_scope
from kira.packs.schema import PackBinding, parse_pack
from kira.parser.parser import ParsedFile

PACK = parse_pack({
    "kira_pack": 1, "id": "one-pace", "name": "One Pace", "media_type": "anime",
    "show": {"title": "One Pace", "aliases": ["One Piece (One Pace)"], "year": 1999},
    "match": {"titles": ["One Pace"], "release_groups": ["One Pace"],
              "filename_regex": r"(?i)\bone[ ._-]?pace\b"},
    "episodes": [
        {"season": 1, "episode": 1, "title": "Romance Dawn 01",
         "match": {"crc32": "a1b2c3d4", "regex": r"Romance Dawn 0?1\b",
                   "release": "[One Pace][RD01]", "arc": "Romance Dawn", "arc_episode": 1}},
        {"season": 1, "episode": 2, "title": "Romance Dawn 02",
         "match": {"crc32": "deadbeef", "arc": "Romance Dawn", "arc_episode": 2}},
        {"season": 2, "episode": 1, "title": "Orange Town 01",
         "match": {"regex": r"Orange Town 0?1\b"}},
    ],
})


def _parsed(filename, *, title="One Pace", group="One Pace", episode=None,
            absolute=None, season=None):
    return ParsedFile(
        original_filename=filename, media_type="anime", title=title,
        release_group=group, episode=episode, absolute_episode=absolute, season=season,
    )


def _binding(scope=None):
    return PackBinding(url="https://x/one-pace.json", id="one-pace", scope_paths=scope or [])


# ── gate ────────────────────────────────────────────────────────────────────
def test_gate_claims_one_pace():
    p = _parsed("[One Pace] Romance Dawn 01 [1080p][A1B2C3D4].mkv")
    assert gate(p, "/anime/One Pace/rd01.mkv", PACK, _binding()) is True


def test_gate_rejects_one_piece():
    # The real One Piece must NOT be claimed by a One Pace pack.
    p = _parsed("[Erai-raws] One Piece - 1080 [1080p].mkv", title="One Piece", group="Erai-raws")
    assert gate(p, "/anime/One Piece/1080.mkv", PACK, _binding()) is False


def test_gate_rejects_unrelated_title():
    p = _parsed("Breaking Bad S01E01.mkv", title="Breaking Bad", group="GROUP")
    assert gate(p, "/tv/Breaking Bad/x.mkv", PACK, _binding()) is False


def test_gate_release_group_signal():
    p = _parsed("rd01.mkv", title="", group="One Pace")
    assert gate(p, "/anime/x/rd01.mkv", PACK, _binding()) is True


# ── scope ───────────────────────────────────────────────────────────────────
def test_in_scope_empty_is_whole_library():
    assert in_scope("/anywhere/file.mkv", []) is True


def test_in_scope_narrows():
    assert in_scope("Z:/anime/One Pace/rd01.mkv", ["Z:/anime/One Pace"]) is True
    assert in_scope("Z:/anime/One Piece/1080.mkv", ["Z:/anime/One Pace"]) is False


def test_gate_respects_scope():
    p = _parsed("[One Pace] Romance Dawn 01 [A1B2C3D4].mkv")
    b = _binding(scope=["Z:/anime/One Pace"])
    assert gate(p, "Z:/anime/One Pace/rd01.mkv", PACK, b) is True
    assert gate(p, "Z:/anime/Other/rd01.mkv", PACK, b) is False


# ── claim ladder ────────────────────────────────────────────────────────────
def test_claim_crc32_wins_over_numbers():
    # Filename's episode number is wrong/absent but the CRC nails episode 2.
    p = _parsed("garbled name [DEADBEEF].mkv", episode=99)
    ep = claim(p, "/x/y.mkv", PACK)
    assert ep is not None and ep.episode == 2 and ep.season == 1


def test_claim_regex():
    p = _parsed("[One Pace] Orange Town 01 [1080p].mkv")
    ep = claim(p, "/x/y.mkv", PACK)
    assert ep is not None and ep.season == 2 and ep.episode == 1


def test_claim_release_substring():
    p = _parsed("[One Pace][RD01] something.mkv")
    ep = claim(p, "/x/y.mkv", PACK)
    assert ep is not None and ep.episode == 1


def test_claim_arc_plus_number():
    p = _parsed("One Pace - Romance Dawn 02.mkv")  # no crc/release; arc+number
    ep = claim(p, "/x/y.mkv", PACK)
    assert ep is not None and ep.episode == 2


def test_claim_none_when_no_episode_signal():
    p = _parsed("One Pace - mystery special.mkv")
    assert claim(p, "/x/y.mkv", PACK) is None
