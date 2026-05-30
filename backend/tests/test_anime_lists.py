"""Phase 5 — anime-lists XML parser + per-episode resolver (fixture-based)."""

from __future__ import annotations

from kira.providers.anime_lists import parse_anime_list_xml, resolve_tvdb_episode

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
