"""Phase 5 — anime-lists XML parser + per-episode resolver (fixture-based)."""

from __future__ import annotations

from kira.providers.anime_lists import (
    parse_anime_list_xml,
    resolve_anidb_episode,
    resolve_tvdb_episode,
)

_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<anime-list>
  <anime anidbid="100" tvdbid="500" defaulttvdbseason="1" episodeoffset="0"/>
  <anime anidbid="101" tvdbid="500" defaulttvdbseason="2" episodeoffset="12"/>
  <anime anidbid="200" tvdbid="600" defaulttvdbseason="1">
    <mapping-list>
      <mapping anidbseason="1" tvdbseason="1" start="1" end="12" offset="0"/>
    </mapping-list>
  </anime>
  <anime anidbid="300" tvdbid="700" defaulttvdbseason="1">
    <mapping-list>
      <mapping anidbseason="1" tvdbseason="1">;1-0;2-1;3-2;</mapping>
    </mapping-list>
  </anime>
  <anime anidbid="400"/>
</anime-list>
"""


def _index():
    return parse_anime_list_xml(_FIXTURE)


def test_parse_indexes_by_tvdb_and_drops_idless() -> None:
    idx = _index()
    assert set(idx.keys()) == {500, 600, 700}   # aid 400 (no tvdbid) dropped
    assert len(idx[500]) == 2                    # two AIDs share tvdb 500


def test_flat_default_season() -> None:
    idx = _index()
    assert resolve_tvdb_episode(idx, 500, 1, 5) == (100, 5)


def test_flat_offset_second_season() -> None:
    idx = _index()
    # tvdb S2E13 → anidb 101 ep 1 (offset 12)
    assert resolve_tvdb_episode(idx, 500, 2, 13) == (101, 1)


def test_range_mapping() -> None:
    idx = _index()
    assert resolve_tvdb_episode(idx, 600, 1, 5) == (200, 5)
    # Out of the [1,12] range → no match.
    assert resolve_tvdb_episode(idx, 600, 1, 99) is None


def test_explicit_mapping_inverts_pairs() -> None:
    idx = _index()
    # ;1-0;2-1;3-2; → anidb ep N maps to tvdb ep N-1; invert: tvdb 1 → anidb 2
    assert resolve_tvdb_episode(idx, 700, 1, 0) == (300, 1)
    assert resolve_tvdb_episode(idx, 700, 1, 1) == (300, 2)
    assert resolve_tvdb_episode(idx, 700, 1, 2) == (300, 3)


def test_unknown_tvdb_id() -> None:
    assert resolve_tvdb_episode(_index(), 99999, 1, 1) is None


def test_malformed_xml_safe() -> None:
    assert parse_anime_list_xml("<not-anime-list>") == {}


# ── Reverse resolver: AniDB (id, episode) → TVDB (season, episode) ────────────
# The seasonal-placement keystone. Mirrors the REAL ScudLee shapes:
#   • One Piece (AID 69): a flat umbrella split into per-arc season RANGES, so a
#     seasonless absolute series gets real TVDB seasons (1156 → S23E01).
#   • Bleach TYBW cour 2 (AID 17765): no mapping-list, flat defaulttvdbseason=17
#     + episodeoffset=13, so cours land continuously in Season 17.
_REV_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<anime-list>
  <anime anidbid="69" tvdbid="81797" defaulttvdbseason="a">
    <mapping-list>
      <mapping anidbseason="1" tvdbseason="22" start="1086" end="1155" offset="-1085"/>
      <mapping anidbseason="1" tvdbseason="23" start="1156" offset="-1155"/>
    </mapping-list>
  </anime>
  <anime anidbid="17765" tvdbid="74796" defaulttvdbseason="17" episodeoffset="13"/>
</anime-list>
"""


def _rev_index():
    return parse_anime_list_xml(_REV_FIXTURE)


def test_reverse_flat_umbrella_real_tvdb_season() -> None:
    idx = _rev_index()
    # One Piece absolute → real TVDB Season 23 (the user's exact case).
    assert resolve_anidb_episode(idx, 69, 1156) == (23, 1)
    assert resolve_anidb_episode(idx, 69, 1159) == (23, 4)   # the "S23E04" file
    assert resolve_anidb_episode(idx, 69, 1165) == (23, 10)
    # Just below the boundary stays in Season 22.
    assert resolve_anidb_episode(idx, 69, 1155) == (22, 70)
    assert resolve_anidb_episode(idx, 69, 1086) == (22, 1)


def test_reverse_cour_flat_default_offset() -> None:
    idx = _rev_index()
    # Bleach TYBW cour 2: season-continuous via default season + offset.
    assert resolve_anidb_episode(idx, 17765, 1) == (17, 14)
    assert resolve_anidb_episode(idx, 17765, 13) == (17, 26)


def test_reverse_unknown_and_out_of_range() -> None:
    idx = _rev_index()
    assert resolve_anidb_episode(idx, 99999, 1) is None       # unknown AID
    assert resolve_anidb_episode(idx, 69, 0) is None          # invalid episode
    # Below every range for AID 69 (ranges start at 1086) → no mapping.
    assert resolve_anidb_episode(idx, 69, 5) is None
