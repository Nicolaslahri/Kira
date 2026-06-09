"""Jinja2 naming-engine migration (Tier 1.5, step 1).

KEYSTONE: prove the new `{{ token }}` profiles render BYTE-IDENTICAL paths to
the old `{token}` str.replace engine for representative inputs. The rename
path is sacred — this test is the guarantee the migration changed nothing
observable. Plus smoke tests for the new Jinja powers (filters / conditionals
/ defaults) and the sandbox.
"""
from __future__ import annotations

import pytest

from kira.parser import parse_filename
from kira.renamer.templates import DEFAULT_PROFILES, _build_ctx, apply_template

# Pre-migration profile strings (single-brace {token}). This is the exact
# behavior we must preserve — if a migrated profile diverges from these, the
# equivalence test below fails loudly.
OLD_PROFILES = {
    "Plex": {
        "movie": "{n} ({y})/{n} ({y}){variant} [{q}].{x}",
        "tv": "{n} ({y})/Season {s2}/{n} - S{s2}E{e2}{variant} - {t} [{q}].{x}",
        "anime": "{n}/Season {s2}/{n} - S{s2}E{e2}{variant} - {t} [{rg}].{x}",
        "music": "{artist}/{album} ({y})/{tn}{variant} - {title}.{x}",
    },
    "Jellyfin": {
        "movie": "{n} ({y})/{n} ({y}){variant}.{x}",
        "tv": "{n} ({y})/Season {s2}/{n} ({y}) - S{s2}E{e2}{variant} - {t}.{x}",
        "anime": "{n} ({y})/Season {s2}/{n} - S{s2}E{e2}{variant} - {t}.{x}",
        "music": "{artist}/{album}/{tn}{variant} {title}.{x}",
    },
    "Kodi": {
        "movie": "{n} ({y})/{n} ({y}){variant} - {q}.{x}",
        "tv": "{n}/Season {s2}/{n}.S{s2}E{e2}{variant}.{t}.{x}",
        "anime": "{n}/S{s2}/{n} - {abs}{variant} - {t}.{x}",
        "music": "{artist} - {album}/{tn}{variant}. {title}.{x}",
    },
}


def _old_apply(template: str, ctx: dict) -> str:
    """The pre-migration str.replace engine, verbatim."""
    out = template
    for k, v in ctx.items():
        out = out.replace("{" + k + "}", "" if v is None else str(v))
    return out


def _ctx_for(filename: str, **kw) -> dict:
    p = parse_filename(filename)
    return _build_ctx(p, p.title or "", p.year, **kw)


# Representative file per media type, including the tokens each profile uses.
SAMPLES = {
    "movie": ("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv", {}),
    "tv": ("Breaking.Bad.S01E05.720p.WEB-DL.mkv", {"episode_title": "Gray Matter"}),
    "anime": ("[SubsPlease] Frieren - 28 (1080p) [F2A7B3D9].mkv",
              {"episode_title": "The Journey's End"}),
    "music": ("fleetwood_mac_-_rumours_-_05_-_go_your_own_way.mp3", {}),
}


@pytest.mark.parametrize("profile_name", ["Plex", "Jellyfin", "Kodi"])
@pytest.mark.parametrize("mtype", ["movie", "tv", "anime", "music"])
def test_jinja_matches_legacy(profile_name: str, mtype: str) -> None:
    """New {{ }} profile output == old {token} output for the same context."""
    filename, kw = SAMPLES[mtype]
    ctx = _ctx_for(filename, **kw)
    new_tmpl = getattr(DEFAULT_PROFILES[profile_name], mtype)
    old_tmpl = OLD_PROFILES[profile_name][mtype]
    assert apply_template(new_tmpl, ctx) == _old_apply(old_tmpl, ctx)


def test_legacy_edge_cases_still_identical() -> None:
    """Variant suffix (dual-audio), missing episode title, absolute-only anime
    — the gnarly cases that motivated {variant} — still render identically."""
    cases = [
        # dual-audio anime → {variant} = ".JAP" etc.
        ("[Moozzi2] Kanojo, Okarishimasu - 01 [JAP][1080p].mkv", {"episode_title": "Rental Girlfriend"}, "Plex", "anime"),
        # no episode title → falls back to "Episode NN"
        ("Some.Show.S02E07.1080p.mkv", {}, "Plex", "tv"),
        # absolute-only anime → {abs} populated
        ("[SubsPlease] One Piece - 1071 (1080p).mkv", {}, "Kodi", "anime"),
    ]
    for filename, kw, profile, mtype in cases:
        ctx = _ctx_for(filename, **kw)
        new_tmpl = getattr(DEFAULT_PROFILES[profile], mtype)
        old_tmpl = OLD_PROFILES[profile][mtype]
        assert apply_template(new_tmpl, ctx) == _old_apply(old_tmpl, ctx), (
            f"divergence for {filename} / {profile}.{mtype}"
        )


# ── New Jinja powers the old engine couldn't do ──────────────────────────


def test_filter_pipe() -> None:
    assert apply_template("{{ n | upper }}", {"n": "frieren"}) == "FRIEREN"


def test_conditional() -> None:
    tmpl = "{{ n }}{% if hdr %}.HDR{% endif %}"
    assert apply_template(tmpl, {"n": "Dune", "hdr": "HDR10"}) == "Dune.HDR"
    assert apply_template(tmpl, {"n": "Dune", "hdr": ""}) == "Dune"


def test_default_filter() -> None:
    # `t` absent → default kicks in.
    assert apply_template("{{ t | default('Episode ' ~ e2) }}", {"e2": "05"}) == "Episode 05"


def test_missing_token_renders_blank() -> None:
    # Matches the old "absent token = no substitution → empty" behavior.
    assert apply_template("{{ nonexistent }}", {}) == ""


def test_none_renders_blank() -> None:
    assert apply_template("{{ y }}", {"y": None}) == ""


# ── Sandbox: a malicious template must not reach Python internals ─────────


def test_sandbox_blocks_dunder_access() -> None:
    with pytest.raises(ValueError):
        apply_template("{{ ().__class__.__bases__ }}", {})


def test_malformed_template_raises_valueerror() -> None:
    with pytest.raises(ValueError):
        apply_template("{{ unclosed ", {})


# ── DB migration regex: legacy {token} → {{token}} (idempotent) ──────────


def test_legacy_token_rewrite() -> None:
    from kira.database import _LEGACY_TOKEN_RE

    def rewrite(s: str) -> str:
        return _LEGACY_TOKEN_RE.sub(r"{{\1}}", s)

    src = "{n} ({y})/Season {s2}/{n} - S{s2}E{e2}{variant} - {t} [{q}].{x}"
    want = "{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{q}}].{{x}}"
    assert rewrite(src) == want
    # Idempotent: re-running on the migrated string is a no-op.
    assert rewrite(want) == want
    # Already-Jinja stays untouched.
    assert rewrite("{{ already | upper }}") == "{{ already | upper }}"


# ── Step 2: new tokens (purely additive — must not disturb existing ones) ─


def test_new_composite_tokens_tv() -> None:
    ctx = _ctx_for("Breaking.Bad.S01E05.720p.WEB-DL.mkv", episode_title="Gray Matter")
    assert ctx["s00e00"] == "S01E05"
    assert ctx["sxe"] == "1x05"
    assert ctx["ny"] == "Breaking Bad"        # no year in the filename
    assert ctx["mtype"] == "tv"
    # Existing tokens are exactly as before — additive change didn't disturb them.
    assert ctx["s2"] == "01" and ctx["e2"] == "05" and ctx["n"] == "Breaking Bad"


def test_new_token_ny_and_tech_with_year() -> None:
    ctx = _ctx_for("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    assert ctx["ny"] == "The Matrix (1999)"
    assert ctx["vc"] == "x264"
    assert ctx["resolution"] == "1080p"
    assert ctx["vf"] == "1080p"               # the reference renamer {vf} alias of resolution


def test_render_uses_new_tokens() -> None:
    ctx = _ctx_for("Breaking.Bad.S01E05.720p.WEB-DL.mkv", episode_title="Gray Matter")
    out = apply_template("{{ n }}/{{ s00e00 }} - {{ t }}.{{ ext }}", ctx)
    assert out == "Breaking Bad/S01E05 - Gray Matter.mkv"


def test_all_new_tokens_present_and_string() -> None:
    ctx = _ctx_for("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    for k in ("ny", "s00e00", "sxe", "e2end", "ext", "group", "original", "mtype",
              "resolution", "vf", "source", "vc", "ac", "hdr", "bitdepth",
              "edition", "cour", "airdate"):
        assert k in ctx and isinstance(ctx[k], str), f"{k} missing or non-string"


# ── Step 4: custom the reference renamer-style filters ─────────────────────────────────


def test_filter_pad() -> None:
    assert apply_template("{{ 5 | pad(2) }}", {}) == "05"
    assert apply_template("{{ 5 | pad(3) }}", {}) == "005"
    assert apply_template("{{ 128 | pad(2) }}", {}) == "128"   # already wider


def test_filter_ascii() -> None:
    assert apply_template("{{ n | ascii }}", {"n": "Frieren: Sōsō"}) == "Frieren: Soso"
    assert apply_template("{{ n | ascii }}", {"n": "Pokémon"}) == "Pokemon"


def test_filter_roman() -> None:
    assert apply_template("{{ s | roman }}", {"s": 2}) == "II"
    assert apply_template("{{ s | roman }}", {"s": 14}) == "XIV"
    assert apply_template("{{ s | roman }}", {"s": "notanumber"}) == "notanumber"


def test_filter_clean() -> None:
    assert apply_template("{{ n | clean }}", {"n": "  The   Matrix  "}) == "The Matrix"


def test_filter_sort_name() -> None:
    assert apply_template("{{ n | sortName }}", {"n": "The Matrix"}) == "Matrix, The"
    assert apply_template("{{ n | sortName }}", {"n": "Breaking Bad"}) == "Breaking Bad"


def test_filter_upper_initial() -> None:
    assert apply_template("{{ n | upperInitial }}", {"n": "the office"}) == "The Office"


def test_filters_chain_with_builtins() -> None:
    # Custom + built-in filters compose, e.g. pad then nothing, ascii then upper.
    assert apply_template("{{ n | ascii | upper }}", {"n": "Pokémon"}) == "POKEMON"


# ── Step 2b: provider-metadata tokens (opt-in via the metadata arg) ───────


def test_metadata_tokens_render() -> None:
    p = parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    md = {
        "director": "Lana Wachowski",
        "genres": ["Sci-Fi", "Action"],
        "cast": ["Keanu Reeves", "Carrie-Anne Moss"],
        "runtime": 136,
        "tmdbid": "603",
    }
    ctx = _build_ctx(p, p.title, p.year, metadata=md)
    assert ctx["director"] == "Lana Wachowski"
    assert ctx["genres"] == "Sci-Fi, Action"
    assert ctx["genre"] == "Sci-Fi"
    assert ctx["cast"] == "Keanu Reeves, Carrie-Anne Moss"
    assert ctx["actors"] == ctx["cast"]
    assert ctx["runtime"] == "136"
    assert ctx["tmdbid"] == "603"


def test_metadata_none_keeps_tokens_empty() -> None:
    # The default (no metadata) path the equivalence test exercises: every
    # metadata token is present but empty, so existing renders are unchanged.
    p = parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    ctx = _build_ctx(p, p.title, p.year)
    for k in ("director", "genres", "genre", "cast", "actors", "network",
              "studio", "language", "country", "runtime", "label",
              "yearrange", "tmdbid", "tvdbid", "anidbid", "imdbid"):
        assert ctx[k] == "", f"{k} should be empty without metadata"


def test_render_with_metadata_tokens() -> None:
    p = parse_filename("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    ctx = _build_ctx(p, p.title, p.year, metadata={"director": "Lana Wachowski", "tmdbid": "603"})
    out = apply_template("{{ n }} ({{ y }}) - {{ director }} [tmdb-{{ tmdbid }}].{{ x }}", ctx)
    assert out == "The Matrix (1999) - Lana Wachowski [tmdb-603].mkv"


# ── Step 5: preset macros ({{ plex }} / {{ kodi }} / {{ jellyfin }}) ──────


def test_preset_macro_equals_full_profile_render() -> None:
    # `{{ plex }}` must equal rendering the Plex profile for this media type.
    ctx = _ctx_for("Breaking.Bad.S01E05.720p.WEB-DL.mkv", episode_title="Gray Matter")
    expected = apply_template(DEFAULT_PROFILES["Plex"].tv, ctx)
    assert ctx["plex"] == expected
    assert "Breaking Bad" in ctx["plex"] and "S01E05" in ctx["plex"]


def test_preset_macros_all_present() -> None:
    ctx = _ctx_for("The.Matrix.1999.1080p.BluRay.x264-GROUP.mkv")
    for k in ("plex", "jellyfin", "kodi", "emby"):
        assert k in ctx and isinstance(ctx[k], str) and ctx[k]
    assert ctx["emby"] == ctx["jellyfin"]   # Emby reuses Jellyfin's layout


def test_render_via_plex_macro() -> None:
    # A user template that's just the macro produces the canonical Plex path.
    ctx = _ctx_for("Breaking.Bad.S01E05.720p.WEB-DL.mkv", episode_title="Gray Matter")
    assert apply_template("{{ plex }}", ctx) == ctx["plex"]


# ── New tokens/filters (verify + finish gaps) ────────────────────────────

def test_decade_token() -> None:
    ctx = _ctx_for("The.Matrix.1999.1080p.BluRay.x264.mkv")
    assert ctx["decade"] == "1990s"
    # No year → blank, not "Nones" or an error.
    assert _ctx_for("Some.Show.S01E01.mkv")["decade"] == ""


def test_file_size_tokens() -> None:
    # 3.5 GiB → gigabytes "3.5", megabytes "3584", bytes exact.
    size = 3758096384  # 3.5 * 1024^3
    ctx = _ctx_for("The.Matrix.1999.1080p.mkv", file_size=size)
    assert ctx["gigabytes"] == "3.5"
    assert ctx["megabytes"] == "3584"
    assert ctx["bytes"] == str(size)
    # Absent size → all blank (templates that don't use them are unaffected).
    none_ctx = _ctx_for("The.Matrix.1999.1080p.mkv")
    assert none_ctx["gigabytes"] == "" and none_ctx["megabytes"] == "" and none_ctx["bytes"] == ""


def test_filter_acronym() -> None:
    assert apply_template("{{ n | acronym }}", {"n": "The Lord of the Rings"}) == "TLOTR"
    assert apply_template("{{ n | acronym }}", {"n": "Breaking Bad"}) == "BB"


def test_safe_strips_empty_optional_token_residue() -> None:
    # Missing optional tokens leave empty bracket/paren groups in the rendered
    # name ("{{n}} ({{y}})" with no year -> "Title ()"; "[{{rg}}]" with no group
    # -> "Title []" or the "[_]" blank placeholder). _safe must scrub them.
    from kira.renamer.templates import _safe
    assert _safe("Bleach - Thousand-Year Blood War ()") == "Bleach - Thousand-Year Blood War"
    assert _safe("Show - S01E01 - Title [_].mkv") == "Show - S01E01 - Title.mkv"
    assert _safe("Show - S01E01 - Title [].mkv") == "Show - S01E01 - Title.mkv"
    assert _safe("Movie {}") == "Movie"
    # Non-empty groups are preserved.
    assert _safe("Movie (2022)") == "Movie (2022)"
    assert _safe("Show - S01E01 [1080p].mkv") == "Show - S01E01 [1080p].mkv"
    assert _safe("Show [EMBER].mkv") == "Show [EMBER].mkv"
