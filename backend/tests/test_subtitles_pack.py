"""Season-pack intelligence — entry ranking + confidence + extraction.

A provider often only has a whole-season ZIP. These pin the logic that turns
that archive into the RIGHT single episode using everything the matcher knows
(SxxEyy, absolute number, episode title, runtime, release group) — and, crucially,
that it REFUSES to auto-pick when the signals are weak (so the UI asks the user
instead of saving a wrong episode).
"""

from __future__ import annotations

import io
import zipfile

from kira.subtitles import pack


def _zip(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return buf.getvalue()


def _srt(last_h: int, last_m: int, last_s: int) -> str:
    """A minimal SRT whose final cue ENDS at the given time (≈ runtime)."""
    start_s = max(0, last_s - 2)
    return (f"1\n00:00:01,000 --> 00:00:03,000\nhi\n\n"
            f"2\n{last_h:02d}:{last_m:02d}:{start_s:02d},000 --> "
            f"{last_h:02d}:{last_m:02d}:{last_s:02d},000\nbye\n")


# ── pure entry scoring ────────────────────────────────────────────────────────
def test_sxe_match_is_strongest():
    e = pack.score_entry("Show.S01E06.x264.srt", season=1, episode=6, absolute=None,
                         episode_title=None, release_group=None,
                         entry_seconds=None, target_seconds=None)
    assert e.score >= 55 and e.guessed_episode == 6


def test_absolute_number_match():
    e = pack.score_entry("[Grp] One Piece - 1075.srt", season=None, episode=None,
                         absolute=1075, episode_title=None, release_group=None,
                         entry_seconds=None, target_seconds=None)
    assert e.score >= 40 and "absolute #1075" in " ".join(e.reasons)


def test_episode_title_match():
    e = pack.score_entry("got - The Rains of Castamere.srt", season=3, episode=9,
                         absolute=None, episode_title="The Rains of Castamere",
                         release_group=None, entry_seconds=None, target_seconds=None)
    assert any("title" in r for r in e.reasons) and e.score >= 35


def test_runtime_signal_adds_confidence():
    near = pack.score_entry("ep.srt", season=1, episode=6, absolute=None,
                            episode_title=None, release_group=None,
                            entry_seconds=1420, target_seconds=1430)  # ~10s apart
    far = pack.score_entry("ep.srt", season=1, episode=6, absolute=None,
                           episode_title=None, release_group=None,
                           entry_seconds=600, target_seconds=1430)    # way off
    assert near.score > far.score


def test_wrong_season_same_episode_is_vetoed():
    # The complete-series-pack trap: we want S01E02, the pack offers S02E02.
    # The episode number (2) coincides and a runtime+group hit would push it
    # over the confident floor — but a DIFFERENT season must zero it out, or
    # we'd silently save season 2's subtitle onto a season 1 file.
    e = pack.score_entry("Show.S02E02.1080p-GRP.srt", season=1, episode=2,
                         absolute=None, episode_title=None, release_group="GRP",
                         entry_seconds=1430, target_seconds=1430)
    assert e.score == 0
    assert any("S02E02" in r or "season" in r for r in e.reasons)


def test_correct_season_same_episode_still_scores():
    # Sanity: the guard must not punish the RIGHT season's matching episode.
    e = pack.score_entry("Show.S01E02.1080p-GRP.srt", season=1, episode=2,
                         absolute=None, episode_title=None, release_group="GRP",
                         entry_seconds=1430, target_seconds=1430)
    assert e.score >= 55


def test_zero_padded_absolute_matches():
    # Erai-raws/SubsPlease/Moozzi2 pad to 2-3 digits: "09" must match absolute 9
    # (the primary +40 anime identifier was silently dead without `0*`).
    e = pack.score_entry("[Erai-raws] Show - 09 [1080p].srt", season=None, episode=None,
                         absolute=9, episode_title=None, release_group=None,
                         entry_seconds=None, target_seconds=None)
    assert e.score >= 40 and "absolute #9" in " ".join(e.reasons)


def test_zero_padded_absolute_three_digits():
    e = pack.score_entry("[Grp] One Piece - 007.srt", season=None, episode=None,
                         absolute=7, episode_title=None, release_group=None,
                         entry_seconds=None, target_seconds=None)
    assert e.score >= 40 and "absolute #7" in " ".join(e.reasons)


def test_crc_hash_digits_dont_masquerade_as_episode():
    # The real bug: "[RaX]Nana_-_33_-_[D383C47E].srt" is EPISODE 33, but the
    # CRC hash "C47E" made it read as absolute #47 and win. Denoise must strip
    # the hash so episode 33 is correctly rejected and the real 47 wins.
    z = _zip({
        "[RaX]Nana_-_33_-_[x264_aac]_[D383C47E].srt": "thirtythree",
        "[RaX]Nana_-_47_-_[x264_aac]_[60E461CD].srt": "fortyseven",
    })
    choice = pack.choose_from_pack(z, season=1, episode=47, absolute=47)
    assert choice and choice.confident
    assert "_47_" in choice.best.name and pack.extract_entry(z, choice.best.name)[0] == b"fortyseven"


def test_resolution_and_codec_digits_ignored():
    # Episode 3 vs an entry stuffed with 1920x1080 / x264. Those digits must NOT
    # read as the episode — for episode 264 the codec must not false-match.
    real = pack.score_entry("Show 1920x1080 x264 - 03.srt", season=1, episode=3, absolute=None,
                            episode_title=None, release_group=None, entry_seconds=None, target_seconds=None)
    assert real.score > 0 and any("3" in r for r in real.reasons)
    bogus = pack.score_entry("Show 1920x1080 x264 ep01.srt", season=1, episode=264, absolute=264,
                             episode_title=None, release_group=None, entry_seconds=None, target_seconds=None)
    assert bogus.score == 0  # "264" from x264 stripped; it's really episode 1


def test_wrong_episode_is_zeroed():
    # Explicitly episode 5 — must NOT be offered as episode 6 even if other
    # weak tokens (title/group) coincidentally hit.
    e = pack.score_entry("Show.S01E05.GROUP.srt", season=1, episode=6, absolute=None,
                         episode_title=None, release_group="GROUP",
                         entry_seconds=None, target_seconds=None)
    assert e.score == 0 and e.other_episode == 5


# ── archive-level confidence decision ─────────────────────────────────────────
def test_confident_when_clear_sxe_winner():
    z = _zip({"Show.S01E05.srt": "x", "Show.S01E06.srt": "x", "Show.S01E07.srt": "x"})
    choice = pack.choose_from_pack(z, season=1, episode=6)
    assert choice and choice.confident and choice.is_pack
    assert "S01E06" in choice.best.name


def test_not_confident_when_no_signal():
    # Generic names, no episode/title/runtime signal → we must ask the user.
    z = _zip({"track_a.srt": "x", "track_b.srt": "x", "track_c.srt": "x"})
    choice = pack.choose_from_pack(z, season=1, episode=6)
    assert choice and not choice.confident
    assert len(choice.entries) == 3


def test_confident_when_variants_of_the_wanted_episode():
    # Two entries that BOTH explicitly carry the wanted episode (different groups
    # / a full + signs variant) are EACH correct — take the best, never make the
    # user choose between equally-right files. (Previously this "tied" and asked,
    # which is exactly the spurious "choose Episode N" dialog on a single-episode
    # archive that bundles multiple subtitle files.)
    z = _zip({"[A] Show - 06.srt": "x", "[B] Show - 06.srt": "x"})
    choice = pack.choose_from_pack(z, season=1, episode=6)
    assert choice and choice.confident
    assert choice.best.matched


def test_single_entry_is_always_confident():
    z = _zip({"whatever.srt": "x"})
    choice = pack.choose_from_pack(z, season=1, episode=6)
    assert choice and choice.confident and not choice.is_pack


def test_runtime_ranks_but_does_not_auto_pick():
    # No episode tokens at all. Runtime is CORROBORATING, not identifying (two
    # ~24-min episodes look alike), so the runtime-matched entry ranks FIRST but
    # we still ask the user rather than auto-saving on runtime alone.
    z = _zip({
        "part_a.srt": _srt(0, 23, 40),    # ~1420s — matches the target
        "part_b.srt": _srt(0, 12, 0),     # ~720s
        "part_c.srt": _srt(0, 47, 0),     # ~2820s
    })
    choice = pack.choose_from_pack(z, season=1, episode=6, target_seconds=1420)
    assert choice and not choice.confident
    assert choice.best.name == "part_a.srt"   # runtime still ranks it on top


def test_choose_returns_none_for_no_subs():
    z = _zip({"readme.txt": "hi", "fonts/arial.ttf": "x"})
    assert pack.choose_from_pack(z, season=1, episode=6) is None


# ── extraction + cache ────────────────────────────────────────────────────────
def test_extract_entry_returns_named_file():
    z = _zip({"a.srt": "AAA", "b.srt": "BBB"})
    out = pack.extract_entry(z, "b.srt")
    assert out and out[0] == b"BBB" and out[1] == "srt"


def test_extract_entry_missing_is_none():
    z = _zip({"a.srt": "AAA"})
    assert pack.extract_entry(z, "missing.srt") is None


def test_pack_byte_cache_roundtrip():
    pack.cache_pack("subsource", "999", b"PACKBYTES")
    assert pack.get_cached_pack("subsource", "999") == b"PACKBYTES"
    assert pack.get_cached_pack("subsource", "nope") is None


def _7z(entries: dict[str, str]) -> bytes:
    import py7zr
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        for name, body in entries.items():
            z.writestr(body, name)
    return buf.getvalue()


# ── 7z packs (pure-python backend, no external tool) ──────────────────────────
def test_7z_pack_extracts_matching_episode():
    z = _7z({"Show.S01E05.srt": "five", "Show.S01E06.srt": "six", "Show.S01E07.srt": "seven"})
    assert pack.archive_kind(z) == "7z"
    choice = pack.choose_from_pack(z, season=1, episode=6)
    assert choice and choice.confident and "S01E06" in choice.best.name
    out = pack.extract_entry(z, choice.best.name)
    assert out and out[0] == b"six" and out[1] == "srt"


def test_7z_read_subtitle_entries_filters_non_subs():
    z = _7z({"ep.srt": "x", "readme.txt": "nope", "font.ttf": "nope"})
    subs = pack.read_subtitle_entries(z)
    assert subs is not None and set(subs) == {"ep.srt"}


def test_archive_kind_detection():
    assert pack.archive_kind(_zip({"a.srt": "x"})) == "zip"
    assert pack.archive_kind(b"Rar!\x1a\x07\x00rest") == "rar"
    assert pack.archive_kind(b"7z\xbc\xaf\x27\x1crest") == "7z"
    assert pack.archive_kind(b"\x1f\x8bgziped") == "gzip"
    assert pack.archive_kind(b"1\n00:00:01,000 --> hi") is None   # plain srt
    assert pack.archive_kind(b"") is None


def test_rank_entries_reuses_preread_dict_for_harvest():
    # The harvest reads a pack ONCE, then ranks the same entries against each
    # sibling episode. Pin that rank_entries picks the right entry per episode.
    z = _zip({"Show.S01E05.srt": "five", "Show.S01E06.srt": "six", "Show.S01E07.srt": "seven"})
    subs = pack.read_subtitle_entries(z)
    durations = pack.entry_durations(subs)
    for ep, body in ((5, b"five"), (6, b"six"), (7, b"seven")):
        choice = pack.rank_entries(subs, durations, season=1, episode=ep)
        assert choice and choice.confident
        assert subs[choice.best.name] == body   # each episode → its own entry


def test_srt_last_cue_seconds():
    assert pack.srt_last_cue_seconds(_srt(0, 23, 40).encode()) == 23 * 60 + 40
    assert pack.srt_last_cue_seconds(b"no timestamps here") is None


def test_choose_confident_with_runtime_plus_number():
    # Runtime alone won't auto-pick, but runtime + a matching episode number
    # together clear the bar — the combination is identifying.
    z = _zip({
        "Show - 06.srt": _srt(0, 23, 40),
        "Show - 05.srt": _srt(0, 23, 50),
        "Show - 07.srt": _srt(0, 24, 10),
    })
    choice = pack.choose_from_pack(z, season=1, episode=6, target_seconds=1420)
    assert choice and choice.confident
    assert "06" in choice.best.name


# ── episode_match: candidate-level episode matching (both S/E + absolute) ──
def test_episode_match_absolute_anime():
    # Absolute-numbered anime: the right number matches; the resolution "1080p"
    # is denoised so it can't masquerade as episode 1080.
    m = lambda n, **k: pack.episode_match(n, season=k.get("s"), episode=k.get("e"), absolute=k.get("a"))
    assert m("[SubsPlease] One Piece - 1080 (1080p) [F00D].srt", a=1080, e=8, s=21) == "match"
    assert m("[Erai-raws] Show - 09 [720p].srt", a=9) == "match"          # zero-pad
    # A different absolute we can't read as a small token → not buried (unknown),
    # but the right one still wins via its boost.
    assert m("[SubsPlease] One Piece - 1079.srt", a=1080, e=8, s=21) == "unknown"


def test_episode_match_tv_sxe_and_mismatch():
    m = lambda n, **k: pack.episode_match(n, season=k.get("s"), episode=k.get("e"), absolute=k.get("a"))
    assert m("Show.S02E05.1080p.WEB-DL.srt", s=2, e=5) == "match"
    assert m("Show.S02E06.1080p.srt", s=2, e=5) == "mismatch"        # explicit wrong ep
    assert m("Show.S03E05.srt", s=2, e=5) == "mismatch"             # wrong season
    assert m("Show - 05 - The Title.srt", s=1, e=5) == "match"      # "- 05 -" token
    assert m("Show - 06 - Other.srt", s=1, e=5) == "mismatch"


def test_episode_match_conservative_cases():
    m = lambda n, **k: pack.episode_match(n, season=k.get("s"), episode=k.get("e"), absolute=k.get("a"))
    # CRC32 hash must NOT read as an absolute episode (the C47E → "47" trap).
    assert m("[RaX]Nana_-_33_-_[D383C47E].srt", a=47) == "unknown"
    assert m("[RaX]Nana_-_33_-_[D383C47E].srt", a=33) == "match"
    # Season pack / range names no single episode → never a mismatch.
    assert m("One Piece 1075-1100 [batch].srt", a=1080, e=8) == "unknown"
    assert m("Attack on Titan Season 2 Complete.srt", s=2, e=5) == "unknown"
    # On an absolute show we don't trust a stray S/E token → never bury it.
    assert m("Show S05E12 random.srt", a=1080, e=8, s=21) == "unknown"
    # No episode info at all, and no signal we can use.
    assert m("Random.Movie.2025.1080p.srt", s=None, e=None, a=None) == "unknown"


def test_episode_match_fansub_format():
    # The "[Group] Title - NN [tags]" anime release form — the common case the
    # old parser missed entirely (every Gachiakuta row scored an identical 49).
    m = lambda n, **k: pack.episode_match(n, season=k.get("s"), episode=k.get("e"), absolute=k.get("a"))
    assert m("[Erai-raws] Gachiakuta - 04 [1080p CR WEBRip HEVC AAC]", e=4, s=1) == "match"
    assert m("[Erai-raws] Gachiakuta - 06 [1080p CR WEBRip HEVC AAC]", e=4, s=1) == "mismatch"  # TV → buried
    # anime (absolute in play) → boost the right one, don't bury the rest
    assert m("[Erai-raws] Gachiakuta - 04 [1080p]", e=4, s=1, a=4) == "match"
    assert m("[Erai-raws] Gachiakuta - 06 [1080p]", e=4, s=1, a=4) == "unknown"


def test_is_likely_pack():
    # A single fansub episode (file-count > 1 from a bundled srt+ass) is NOT a
    # pack — the old `files > 1` rule flagged every one as a "SEASON PACK".
    assert pack.is_likely_pack("[Erai-raws] Gachiakuta - 04 [1080p CR WEBRip HEVC AAC]", 3) is False
    assert pack.is_likely_pack("Gachiakuta.S01E04.WEB-DL", 1) is False
    # Genuine packs — range, batch, complete, season.
    assert pack.is_likely_pack("[Erai-raws] Gachiakuta 01-12 [batch]", 12) is True
    assert pack.is_likely_pack("Attack on Titan Season 2 Complete", 25) is True
    assert pack.is_likely_pack("Nana (Complete Series)", 47) is True
    # Ambiguous name → fall back to the provider's file count.
    assert pack.is_likely_pack("Gachiakuta", 8) is True
    assert pack.is_likely_pack("Gachiakuta", 1) is False
