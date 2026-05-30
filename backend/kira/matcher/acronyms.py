"""Shared acronym data + helpers (Matching-completeness M2).

One source of truth for acronym reasoning, consumed by three call sites:

  - `AcronymMetric` (cascade)         — confirm an acronym→title match + score it.
  - `AniDBProvider.search_tv`         — surface the right AID for an acronym-only
                                        filename via the offline acronym index,
                                        before the cascade ever runs.
  - `MatchEngine._query_ladder`       — add an expansion query rung so TMDB/TVDB
                                        (which can't expand acronyms) resolve
                                        "LotR" → "lord of the rings".

Pure data + pure functions: no I/O, no provider imports → safe to import from
providers AND the matcher without circular-import risk. Callers pass titles that
are ALREADY normalized (via `similarity.normalize`); these helpers don't
re-normalize so the module stays dependency-free.
"""
from __future__ import annotations

# Curated fan / colloquial acronyms → canonical expansion (lowercase, the form
# `similarity.normalize` produces). These are the ones a generated initialism
# can't derive reliably — they fold in connector words ("on" in AoT) or are too
# entrenched to build from first letters ("lotr" drops both "the"s).
KNOWN_ACRONYMS: dict[str, str] = {
    "aot": "attack on titan",
    "snk": "shingeki no kyojin",
    "jjk": "jujutsu kaisen",
    "fma": "fullmetal alchemist",
    "fmab": "fullmetal alchemist brotherhood",
    "kny": "kimetsu no yaiba",
    "mha": "my hero academia",
    "bnha": "boku no hero academia",
    "dbz": "dragon ball z",
    "dbs": "dragon ball super",
    "lotr": "lord of the rings",
    "got": "game of thrones",
    "httyd": "how to train your dragon",
    "tng": "star trek the next generation",
    "ds9": "star trek deep space nine",
    "jojo": "jojos bizarre adventure",
}

# Connector words skipped when deriving the "without-stopwords" initialism.
STOP_WORDS = {
    "the", "a", "an", "of", "no", "and", "to",
    "wa", "ga", "wo", "ni", "de", "na", "o",
}


def is_acronym_shaped(token: str) -> bool:
    """True when an ALREADY-normalized `token` looks like an acronym: a single
    run of 2-6 chars with no spaces. Multi-word titles are left to the
    trigram / substring metrics."""
    return bool(token) and " " not in token and 2 <= len(token) <= 6


def acronym_forms(title_norm: str) -> set[str]:
    """Initialism forms of an ALREADY-normalized title.

    Returns both the all-words form ("attack on titan" → "aot") and the
    without-stopwords form ("the lord of the rings" → "lr"). Empty set for
    single-word titles (we never want "naruto" → "n")."""
    words = [w for w in title_norm.split() if w]
    if len(words) < 2:
        return set()
    out = {"".join(w[0] for w in words)}
    sig = [w for w in words if w not in STOP_WORDS]
    if len(sig) >= 2:
        out.add("".join(w[0] for w in sig))
    return out


def expand_known(token_norm: str) -> str | None:
    """Canonical expansion for a known acronym (already normalized), else None."""
    return KNOWN_ACRONYMS.get(token_norm)
