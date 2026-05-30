"""Pure string-distance helpers for the multi-metric similarity cascade
(Phase 7). the reference renamer's EpisodeMetrics combines ~10 similarity measures;
Kira's tier-2 band had only character-trigram (Sørensen-Dice). These add
edit-distance, longest-common-subsequence, and numeric-token agreement so
the matcher disambiguates typos, word-order shuffles, and numeric-heavy
titles ("86" / "91 Days" / "3x3 Eyes") that trigram alone handles weakly.

All functions take RAW strings and are pure (no I/O) — the cascade metrics
normalize before calling. Titles are short (<60 chars) so the O(n·m) DP
table in Levenshtein / LCS is negligible.
"""

from __future__ import annotations

import re


def levenshtein_distance(a: str, b: str) -> int:
    """Classic edit distance (insert/delete/substitute = 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(
                prev[j] + 1,        # deletion
                cur[j - 1] + 1,     # insertion
                prev[j - 1] + (ca != cb),  # substitution
            ))
        prev = cur
    return prev[-1]


def levenshtein_ratio(a: str, b: str) -> float:
    """1.0 = identical, 0.0 = completely different. Normalized by the
    longer string's length so it's symmetric and bounded."""
    if not a and not b:
        return 1.0
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return 1.0 - (levenshtein_distance(a, b) / longest)


def lcs_length(a: str, b: str) -> int:
    """Length of the longest common subsequence (not contiguous)."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                cur.append(prev[j - 1] + 1)
            else:
                cur.append(max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def lcs_ratio(a: str, b: str) -> float:
    """LCS length normalized by the longer string. 1.0 = one is a
    subsequence-superset of the other at full length."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 1.0 if not a and not b else 0.0
    return lcs_length(a, b) / longest


_NUM_RE = re.compile(r"\d+")


def numeric_tokens(s: str) -> set[int]:
    """Set of integers appearing as runs of digits in `s`."""
    return {int(m) for m in _NUM_RE.findall(s)}


def numeric_similarity(a: str, b: str) -> float | None:
    """Agreement between the numbers in two strings, or None to abstain.

    Returns None when NEITHER side has a number (no numeric signal to add).
    When at least one side has numbers, returns the Jaccard overlap of the
    two number sets — so "86" vs "86" → 1.0, "86" vs "91 Days" → 0.0,
    "Mob Psycho 100" vs "Mob Psycho 100 II" → 1.0 (numbers {100} match;
    the roman II is folded elsewhere). Abstaining when both are number-free
    keeps this from contributing noise to ordinary text titles.
    """
    na, nb = numeric_tokens(a), numeric_tokens(b)
    if not na and not nb:
        return None
    union = na | nb
    if not union:
        return None
    return len(na & nb) / len(union)
