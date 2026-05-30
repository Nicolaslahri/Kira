"""Phase 20 — strict vs opportunistic matching gate.

the reference renamer's strict mode acts only on high-confidence matches and skips the
rest; non-strict takes best-effort guesses. That distinction matters for
UNATTENDED runs (auto-approve, watch-folder import) where acting on a shaky
match silently mis-files something.

This module is the pure decision: given a match's confidence and the
configured mode, should the system ACT automatically, or hold the file for
human review? The interactive Review page is unaffected — it always shows
every candidate. The gate is for the future auto-approve / watch-folder
paths (which don't exist yet); shipping it now means those land with the
safety rail already in place rather than bolted on after.
"""
from __future__ import annotations

from enum import Enum


class MatchMode(str, Enum):
    STRICT = "strict"               # only auto-act on confident matches
    OPPORTUNISTIC = "opportunistic"  # auto-act on any positive match


# Confidence floor for auto-acting in strict mode. 0.85 == the tier-1
# identity band floor in the cascade — i.e. "a structural identity hit",
# the same bar the UI calls a green/Strong match.
DEFAULT_STRICT_THRESHOLD = 0.85


def parse_mode(value: str | None) -> MatchMode:
    """Coerce a settings string to a MatchMode (defaults to STRICT — the
    safe choice for unattended operation)."""
    if isinstance(value, str) and value.strip().lower() == "opportunistic":
        return MatchMode.OPPORTUNISTIC
    return MatchMode.STRICT


def meets_threshold(
    score: float | None,
    mode: MatchMode = MatchMode.STRICT,
    threshold: float = DEFAULT_STRICT_THRESHOLD,
) -> bool:
    """True when a match is confident enough to ACT on automatically.

    - OPPORTUNISTIC: act on any positive match (best-effort).
    - STRICT: act only when ``score >= threshold``; otherwise hold for review.

    A None / non-positive score never auto-acts in either mode (no match).
    """
    if score is None or score <= 0:
        return False
    if mode is MatchMode.OPPORTUNISTIC:
        return True
    return score >= threshold
