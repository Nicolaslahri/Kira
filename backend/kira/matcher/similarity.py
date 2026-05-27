"""Title similarity — trigram (3-gram) Sørensen-Dice on normalized titles.

Same family as FileBot's QGrams. Two design choices worth knowing:

1. **Dice over Jaccard.** Jaccard's `|A ∩ B| / |A ∪ B|` is brutal on
   asymmetric lengths — "Naruto" vs "Naruto Shippuden" scores 0.44,
   below most usable thresholds. Dice's `2·|A ∩ B| / (|A| + |B|)` scores
   the same pair at 0.61 because the overlap is weighted higher. Way
   more forgiving of dropped subtitles, season suffixes, etc.

2. **Two-stage punctuation handling.** Apostrophes and periods are
   DELETED (so `S.W.A.T.` → `swat` and `A Bug's Life` → `a bugs life`).
   Everything else (hyphens, colons, commas) becomes spaces. This keeps
   acronyms intact while still tokenizing real word boundaries.
"""

from __future__ import annotations

import re
import unicodedata

_ARTICLES = {"the", "a", "an"}
# Punctuation we DELETE entirely (joins letters together): apostrophes
# and periods. Without this, "S.W.A.T." would become "s w a t" and lose
# all trigram overlap with the API's "SWAT".
_DELETE_PUNCT_RE = re.compile(r"['\.]")
# Everything else punctuation-shaped becomes a space (so hyphens and
# colons still tokenize real word boundaries: "Spider-Man" → "spider man").
_SPACE_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE          = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, transliterate Unicode→ASCII, strip leading articles + punctuation.

    Order matters:
      1. Unicode fold ("Zürich" → "Zurich", "Shōgun" → "Shogun").
      2. M5: ASCII fallback for non-Latin scripts (Cyrillic, CJK, Greek,
         Hebrew, Arabic). NFKD only strips combining diacritics — it
         leaves base codepoints intact, so `Маша и Медведь` (Cyrillic)
         survives unchanged and trigrams 0% against its English alias.
         We collapse runs of non-ASCII chars to single spaces so they
         don't poison the trigram set. The matcher's alias scoring
         already handles "different script but same identity" via
         provider-native alias tables (AniDB romaji, TVDB language
         variants), so the normalized form just needs to not actively
         interfere.
      3. Lowercase.
      4. Replace `&` with `and` BEFORE punctuation gets stripped, so
         "Rick & Morty" matches "Rick and Morty".
      5. Delete apostrophes + periods (acronyms / contractions stay
         joined: "S.W.A.T." → "swat", "Bug's" → "bugs").
      6. Other punctuation → spaces.
      7. Article stripping — runs LAST so "A.I. Artificial Intelligence"
         (which becomes "ai artificial intelligence" after step 5) doesn't
         lose its leading "A" to the article rule.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    # M5: collapse non-ASCII runs to a single space. Latin titles untouched
    # because all chars are ASCII; Cyrillic / CJK / etc. now produce a
    # short normalized form that doesn't fake-match unrelated content.
    # Numbers stay (so "2049" survives). The non-ASCII fallback isn't a
    # romanization — proper transliteration needs `unidecode`/`pykakasi`
    # which we're avoiding as a dependency. The matcher catches CJK/Cyrillic
    # identity via provider alias matching (AniDB's romaji entry, TVDB's
    # language variants) where the actual transliteration lives.
    s = "".join(ch if ord(ch) < 128 else " " for ch in s)
    s = s.lower()
    s = s.replace("&", " and ")
    s = _DELETE_PUNCT_RE.sub("", s)
    s = _SPACE_PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    parts = s.split(" ", 1)
    if parts and parts[0] in _ARTICLES and len(parts) > 1:
        s = parts[1]
    return s


def trigram_similarity(a: str, b: str) -> float:
    """Sørensen-Dice similarity of character trigram sets (0-1) after normalize.

    Pure Dice. The M7 short-title penalty + token-set tiebreak that
    used to live here has been deleted — `ClusterSignalMetric` in the
    cascade now handles the "One Pace vs One Piece" case structurally
    via the cluster-wide common-sequence signal, which is a cleaner
    fix than a single-string penalty heuristic.
    """
    a_n = normalize(a)
    b_n = normalize(b)
    if not a_n or not b_n:
        return 0.0
    if a_n == b_n:
        return 1.0
    tg_a = _trigrams(a_n)
    tg_b = _trigrams(b_n)
    if not tg_a or not tg_b:
        return 0.0
    intersection = len(tg_a & tg_b)
    dice = (2.0 * intersection) / (len(tg_a) + len(tg_b))
    return dice


def _trigrams(s: str) -> set[str]:
    # Pad short strings so 1-2 char titles still produce trigrams.
    padded = f"  {s}  "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}
