"""Cluster signal extractor — longest contiguous shared word sequence
across a list of parsed titles.

This is the cluster-level identity signal that fixes the One Pace failure
class. When N files in a cluster all share a leading word sequence in
their normalized titles, that's a much stronger identity signal than any
individual filename. Score the *cluster signal* against candidates, not
each file independently.

Algorithm (inspired by FileBot's CommonSequenceMatcher.firstCommonSequence,
re-implemented from the algorithmic description — no Java code copy):

  1. Normalize every input title (lowercase, strip punctuation, collapse
     whitespace) via the existing `normalize()` function.
  2. Tokenize each into words.
  3. Find the longest contiguous word subsequence that appears in EVERY
     input's token list, with a minimum length of 1 word.
  4. Return that subsequence joined by spaces, or None if no shared
     sequence exists.

Cluster isolation guarantee (user-locked): the caller MUST ensure the
input titles all belong to the same series. We enforce this upstream by
keying clusters on `series_key` which already separates seasons.
Defensive: if input list has wildly divergent titles (only stop-words
shared), return None so the cascade falls back to per-file scoring.
"""
from __future__ import annotations

from kira.matcher.similarity import normalize


_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "of", "in", "on", "to", "for", "by",
    "with", "from", "at", "as", "is", "be",
})


def compute_cluster_signal(titles: list[str]) -> str | None:
    """Return the longest contiguous shared word sequence across all titles.

    None when the input list is < 2 elements, or no shared non-stopword
    sequence exists, or every shared word is a stopword.
    """
    if len(titles) < 2:
        return None

    tokenized: list[list[str]] = []
    for t in titles:
        n = normalize(t)
        if not n:
            return None
        words = [w for w in n.split() if w]
        if not words:
            return None
        tokenized.append(words)

    # Find the longest contiguous word sequence present in EVERY list.
    # Greedy from the first list's substrings, longest first; check each
    # against every other list using a contiguous substring search.
    first = tokenized[0]
    best: list[str] = []
    for length in range(len(first), 0, -1):
        if length <= len(best):
            break  # can't beat the current best
        for start in range(len(first) - length + 1):
            candidate = first[start:start + length]
            if _all_contain(tokenized[1:], candidate):
                if length > len(best):
                    best = candidate
                break  # first hit at this length is good enough
        if best and len(best) == length:
            break  # found at this length; nothing longer possible

    if not best:
        return None
    # Reject pure-stopword sequences (`["the"]` alone is no signal).
    if all(w in _STOP_WORDS for w in best):
        return None
    return " ".join(best)


def _all_contain(token_lists: list[list[str]], needle: list[str]) -> bool:
    """True iff `needle` appears as a contiguous subsequence in every token list."""
    n = len(needle)
    for tl in token_lists:
        if not _contains_subsequence(tl, needle, n):
            return False
    return True


def _contains_subsequence(haystack: list[str], needle: list[str], n: int) -> bool:
    if n == 0 or n > len(haystack):
        return False
    for i in range(len(haystack) - n + 1):
        if haystack[i:i + n] == needle:
            return True
    return False
