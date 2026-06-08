"""Flat-umbrella local→absolute remap — the One Piece "S23E04" → 1159 fix.

One Piece's whole run lives under ONE flat AniDB AID (69) numbered absolutely;
Fribb carries no `season.tvdb` for it (`tvdb_season(69) is None`). A file that
arrives in TVDB-season-LOCAL form — `One.Piece.1999.S23E04` — parses episode=4
and the bipartite pairs it to the Elbaf cour's LOCAL episode 4, whose
`absolute_number` is 1159. Storing the local `4` was the bug: it labelled the
file as the 1999 "Red-Haired Shanks" instead of the 2025 "Destroy the Miniature
Garden" (1159) — which is in fact a DUPLICATE of the user's `S23E1159` file.

`remap_umbrella_local_to_absolute` is the INVERSE of the abs→local cour bridge:
for a flat umbrella it rewrites the stored number local→absolute so dups line up.
These tests lock the fix AND, crucially, the things that must NOT regress:
  • a per-season AID (Frieren S2, tvdb_season=2) keeps LOCAL numbering;
  • normal western TV (no absolute_number on episodes) is untouched;
  • absolute-named One Piece siblings keep their absolute number;
  • an early-cour file where absolute == local is a no-op.

The end-to-end test runs the REAL 10-file Elbaf cluster through the actual
bipartite, then the remap, and asserts every final episode_number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from kira.matcher.bipartite import assign_files_to_episodes
from kira.matcher.cour_routing import remap_umbrella_local_to_absolute


# One Piece Elbaf cour (TVDB S23): local 1..13 ↔ absolute 1156..1168.
OP_L2A = {i: 1155 + i for i in range(1, 14)}      # {1:1156 … 4:1159 … 13:1168}


# ── 1. the pure helper: the fix + every safety gate ─────────────────────────
def test_umbrella_local_file_remaps_to_absolute():
    # THE FIX: a flat-umbrella file paired to LOCAL ep 4 stores absolute 1159.
    assert remap_umbrella_local_to_absolute(
        4, is_flat_umbrella=True, routed_aid=None, local_to_abs=OP_L2A
    ) == 1159
    assert remap_umbrella_local_to_absolute(
        1, is_flat_umbrella=True, routed_aid=None, local_to_abs=OP_L2A
    ) == 1156


def test_umbrella_absolute_named_sibling_untouched():
    # Files already numbered absolutely (1159, 1164) are NOT in local_to_abs
    # (keys are the locals 1..13) → returned unchanged. This is what keeps the
    # 9 correct sibling files correct.
    assert remap_umbrella_local_to_absolute(
        1159, is_flat_umbrella=True, routed_aid=None, local_to_abs=OP_L2A
    ) == 1159
    assert remap_umbrella_local_to_absolute(
        1164, is_flat_umbrella=True, routed_aid=None, local_to_abs=OP_L2A
    ) == 1164


def test_per_season_aid_local_preserved():
    # Frieren S2 (tvdb_season=2 → is_flat_umbrella False). Even if the cross-ref
    # episode list carried absolute_numbers (29..40), a per-season AID's episodes
    # ARE local — remapping S2E03 to 31 would be the bug. Caller passes
    # is_flat_umbrella=False, so local 3 is preserved.
    frieren_s2 = {1: 29, 2: 30, 3: 31, 4: 32}
    assert remap_umbrella_local_to_absolute(
        3, is_flat_umbrella=False, routed_aid=None, local_to_abs=frieren_s2
    ) == 3


def test_routed_file_untouched():
    # Defensive gate: if cour routing already placed the file (routed_aid set),
    # the remap never fires. A real flat umbrella has no cours, so this can't
    # collide in practice — but the guard makes the two systems provably disjoint.
    assert remap_umbrella_local_to_absolute(
        4, is_flat_umbrella=True, routed_aid=14977, local_to_abs=OP_L2A
    ) == 4


def test_normal_tv_empty_map_noop():
    # Western TV episodes carry no absolute_number → local_to_abs is empty → the
    # helper is a strict no-op regardless of the umbrella flag.
    assert remap_umbrella_local_to_absolute(
        4, is_flat_umbrella=False, routed_aid=None, local_to_abs={}
    ) == 4
    assert remap_umbrella_local_to_absolute(
        4, is_flat_umbrella=True, routed_aid=None, local_to_abs={}
    ) == 4


def test_early_cour_self_map_is_noop():
    # One Piece's 1999 season: early absolute == local (ep 4 IS absolute 4).
    # local_to_abs[4] == 4 → self-map → a genuine local-4 file stays 4.
    op_1999 = {1: 1, 2: 2, 3: 3, 4: 4, 5: 5}
    assert remap_umbrella_local_to_absolute(
        4, is_flat_umbrella=True, routed_aid=None, local_to_abs=op_1999
    ) == 4


def test_none_episode_passthrough():
    assert remap_umbrella_local_to_absolute(
        None, is_flat_umbrella=True, routed_aid=None, local_to_abs=OP_L2A
    ) is None


# ── 2. end-to-end: REAL Elbaf cluster through bipartite + remap ─────────────
@dataclass
class _P:
    """Minimal ParsedFile stand-in carrying only what bipartite reads."""
    media_type: str
    season: int | None
    episode: int | None
    absolute_episode: int | None = None
    air_date: str | None = None
    episode_title_guess: str | None = None


def _elbaf_episodes():
    titles = ["Elbaph", "Nami", "Quest", "Destroy the Miniature Garden", "Snowfield",
              "Loki", "Wave", "Praise", "Saul", "Cuisine", "", "", ""]
    return [
        {"season": 1, "episode": i + 1, "absolute_number": 1156 + i, "title": t}
        for i, t in enumerate(titles)
    ]


def _final_ep(assignment, parsed, local_to_abs):
    """Mirror scans.py: bipartite number (or parsed fallback) → umbrella remap."""
    if assignment is not None and assignment.matched_via != "unpaired":
        ep = assignment.episode_number
    else:
        ep = parsed.absolute_episode if parsed.absolute_episode is not None else parsed.episode
    return remap_umbrella_local_to_absolute(
        ep, is_flat_umbrella=True, routed_aid=None, local_to_abs=local_to_abs
    )


def test_one_piece_elbaf_cluster_end_to_end():
    # The exact cluster from the user's DB: one local-numbered dup (S23E04) +
    # one absolute-numbered dup (S23E1159) of the SAME episode 1159, plus eight
    # normal absolute files (1156-1164, some SxE-form, some EP-form).
    files = [
        (368, _P("anime", 23, 4, None, episode_title_guess="Destroy the Miniature Garden")),
        (372, _P("anime", 23, 1156, 1156)),
        (373, _P("anime", 23, 1157, 1157)),
        (369, _P("anime", 23, 1158, 1158)),
        (364, _P("anime", 23, 1159, None, episode_title_guess="Destroy the Miniature Garden")),
        (374, _P("anime", 23, 1160, 1160)),
        (366, _P("anime", 23, 1161, None)),
        (367, _P("anime", 23, 1162, None)),
        (370, _P("anime", 23, 1163, 1163)),
        (371, _P("anime", 23, 1164, 1164)),
    ]
    by_id = dict(files)
    eps = _elbaf_episodes()
    asn = assign_files_to_episodes(files, eps)
    abs_to_local = {e["absolute_number"]: e["episode"] for e in eps}
    local_to_abs = {loc: ab for ab, loc in abs_to_local.items()}

    finals = {fid: _final_ep(asn.get(fid), by_id[fid], local_to_abs) for fid, _ in files}

    # THE FIX: the TVDB-local dup (S23E04) now stores absolute 1159 …
    assert finals[368] == 1159
    # … and the absolute-named dup (S23E1159) is ALSO 1159 — both recognised as
    # the same episode (the bipartite gives the slot to one; the parsed fallback
    # keeps the other at 1159 since 1159 ∉ local_to_abs).
    assert finals[364] == 1159
    # every other file keeps its correct absolute number — no regression.
    assert finals[372] == 1156
    assert finals[373] == 1157
    assert finals[369] == 1158
    assert finals[374] == 1160
    assert finals[366] == 1161
    assert finals[367] == 1162
    assert finals[370] == 1163
    assert finals[371] == 1164
    # and the local file inherited the RIGHT title via its bipartite pair.
    assert asn[368].episode_title == "Destroy the Miniature Garden"


def test_normal_western_tv_cluster_unaffected():
    # A normal SxE cluster: provider episodes have NO absolute_number, so
    # local_to_abs is empty and the remap is a strict no-op. The bipartite pairs
    # each file exactly; numbers are preserved end-to-end.
    files = [
        (1, _P("tv", 1, 1)),
        (2, _P("tv", 1, 2)),
        (3, _P("tv", 1, 3)),
        (4, _P("tv", 1, 4)),
    ]
    by_id = dict(files)
    eps = [{"season": 1, "episode": e, "title": f"E{e}"} for e in (1, 2, 3, 4)]
    asn = assign_files_to_episodes(files, eps)
    abs_to_local = {e["absolute_number"]: e["episode"] for e in eps if e.get("absolute_number")}
    local_to_abs = {loc: ab for ab, loc in abs_to_local.items()}
    assert local_to_abs == {}
    for fid, p in files:
        a = asn.get(fid)
        # NOT an umbrella, empty map → unchanged.
        out = remap_umbrella_local_to_absolute(
            a.episode_number, is_flat_umbrella=False, routed_aid=None, local_to_abs=local_to_abs
        )
        assert out == p.episode


def test_frieren_s2_cluster_keeps_local_numbers():
    # Frieren S2 is a per-season AID (tvdb_season=2). Its cross-ref list DOES
    # carry absolute_numbers (29..32), so local_to_abs is non-empty — but because
    # the caller passes is_flat_umbrella=False, S2E01..S2E04 keep LOCAL 1..4.
    # This is THE regression that the umbrella remap must never cause.
    files = [(i, _P("anime", 2, i)) for i in (1, 2, 3, 4)]
    eps = [{"season": 1, "episode": i, "absolute_number": 28 + i, "title": f"S2E{i}"}
           for i in (1, 2, 3, 4)]
    asn = assign_files_to_episodes(files, eps)
    abs_to_local = {e["absolute_number"]: e["episode"] for e in eps}
    local_to_abs = {loc: ab for ab, loc in abs_to_local.items()}
    assert local_to_abs == {1: 29, 2: 30, 3: 31, 4: 32}   # absolutes ARE present
    for fid, p in files:
        a = asn.get(fid)
        out = remap_umbrella_local_to_absolute(
            a.episode_number, is_flat_umbrella=False, routed_aid=None, local_to_abs=local_to_abs
        )
        assert out == p.episode, f"Frieren S2E0{p.episode} must stay local, got {out}"
