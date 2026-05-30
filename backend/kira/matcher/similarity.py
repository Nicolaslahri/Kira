"""Title similarity — trigram (3-gram) Sørensen-Dice on normalized titles.

Same family as the reference renamer's QGrams. Two design choices worth knowing:

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

# Phase 15: number folding so equivalent-but-differently-written forms align.
# Applied per-token AFTER lowercasing/punctuation, on BOTH sides of every
# comparison — so identical titles still match (both fold the same way); only
# equivalent forms newly align ("Season II" ≡ "2nd Season" ≡ "Second Season"
# ≡ "Season 2").
#
# Roman numerals: MULTI-letter only. Single letters (i, v, x, l, c, d, m) are
# deliberately excluded — they collide with real title words far too often
# ("I, Robot", "X-Men", "V for Vendetta", "Malcolm X"). Two-letter-plus roman
# tokens have no English-word collisions worth worrying about.
_ROMAN_MAP = {
    "ii": "2", "iii": "3", "iv": "4", "vi": "6", "vii": "7", "viii": "8",
    "ix": "9", "xi": "11", "xii": "12", "xiii": "13", "xiv": "14", "xv": "15",
    "xvi": "16", "xvii": "17", "xviii": "18", "xix": "19", "xx": "20",
}
_ORDINAL_WORD_MAP = {
    "first": "1", "second": "2", "third": "3", "fourth": "4", "fifth": "5",
    "sixth": "6", "seventh": "7", "eighth": "8", "ninth": "9", "tenth": "10",
    "eleventh": "11", "twelfth": "12", "thirteenth": "13", "fourteenth": "14",
    "fifteenth": "15", "sixteenth": "16", "seventeenth": "17",
    "eighteenth": "18", "nineteenth": "19", "twentieth": "20",
}
# "2nd" → "2", "3rd" → "3", "21st" → "21", etc.
_NUM_ORDINAL_RE = re.compile(r"^(\d+)(?:st|nd|rd|th)$")


def _fold_number_token(tok: str) -> str:
    """Fold a single token: roman numeral / ordinal word / numeric ordinal
    → its arabic digits. Returns the token unchanged when it isn't one."""
    if tok in _ROMAN_MAP:
        return _ROMAN_MAP[tok]
    if tok in _ORDINAL_WORD_MAP:
        return _ORDINAL_WORD_MAP[tok]
    m = _NUM_ORDINAL_RE.match(tok)
    if m:
        return m.group(1)
    return tok
# Punctuation we DELETE entirely (joins letters together): apostrophes
# and periods. Without this, "S.W.A.T." would become "s w a t" and lose
# all trigram overlap with the API's "SWAT".
_DELETE_PUNCT_RE = re.compile(r"['\.]")
# Everything else punctuation-shaped becomes a space (so hyphens and
# colons still tokenize real word boundaries: "Spider-Man" → "spider man").
_SPACE_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE          = re.compile(r"\s+")


def normalize(s: str) -> str:
    """Lowercase, fold diacritics, strip leading articles + punctuation.

    Order matters:
      1. Unicode NFKD decomposition — splits "é" into "e" + combining
         acute, "Shōgun" into "Shogun" + combining macron, etc.
      2. Strip combining characters — keeps the base codepoints, removes
         the accent marks. Result: "Zürich" → "Zurich", "Shōgun" →
         "Shogun", and crucially KEEPS native scripts intact: Japanese
         kanji/kana (`葬送のフリーレン`), Chinese, Cyrillic (`Маша и
         Медведь`), Greek, Hebrew, Arabic, etc. all survive verbatim.
         Trigram similarity over CJK then works native-to-native: a
         file titled `葬送のフリーレン` matches the AniDB alias
         `葬送のフリーレン` at 100%, instead of both normalizing to
         empty strings (the prior behavior, which 100%-orphaned every
         CJK-only filename).
      3. Lowercase.
      4. Replace `&` with `and` BEFORE punctuation gets stripped, so
         "Rick & Morty" matches "Rick and Morty".
      5. Delete apostrophes + periods (acronyms / contractions stay
         joined: "S.W.A.T." → "swat", "Bug's" → "bugs").
      6. Other punctuation → spaces. `_SPACE_PUNCT_RE` uses `[^\\w\\s]+`
         with `re.UNICODE` — `\\w` matches CJK/Cyrillic as word chars,
         so they pass through; only true punctuation/symbols/emoji get
         collapsed to spaces.
      7. Article stripping — runs LAST so "A.I. Artificial Intelligence"
         (which becomes "ai artificial intelligence" after step 5) doesn't
         lose its leading "A" to the article rule.

    Autopsy 16 history: this function used to contain
    `"".join(ch if ord(ch) < 128 else " " for ch in s)` between steps
    2 and 3, collapsing every non-ASCII char to a space. That meant
    pure-CJK filenames normalized to `""` and the SubstringMetric /
    TrigramMetric `len < 4` guards kicked in → every CJK file silently
    orphaned. The comment claimed provider-alias matching would
    recover identity, but the same normalize is applied to aliases —
    so CJK aliases also became empty and matched nothing. Removed
    that line; `_SPACE_PUNCT_RE` already handles emoji / symbols
    correctly without lobotomizing entire writing systems.
    """
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    s = s.replace("&", " and ")
    s = _DELETE_PUNCT_RE.sub("", s)
    s = _SPACE_PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    # Phase 15: fold roman numerals + ordinals to arabic, per token.
    if s:
        s = " ".join(_fold_number_token(t) for t in s.split(" "))
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
