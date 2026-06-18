"""Typed subtitle errors that callers act on (vs. the best-effort failures the
sources swallow internally)."""

from __future__ import annotations


class AuthRejected(Exception):
    """OpenSubtitles refused the API key (401/403). Every file in a batch
    would fail identically, so callers stop and tell the user to fix the key
    instead of burning N requests against a dead credential."""


class PackEpisodeMissing(Exception):
    """A season-pack archive downloaded fine, but it held no entry matching the
    wanted episode — so we refused to guess (saving the wrong episode silently
    is worse than failing). Lets the manual-pick endpoint say exactly that
    instead of a generic "download failed"."""


class QuotaExceeded(Exception):
    """OpenSubtitles refused because the account's daily download quota (or
    rate limit) is spent. Raised so a batch backfill stops cleanly and reports
    'quota reached — resumes tomorrow' instead of failing every remaining file.

    `remaining` / `reset_hint` are populated when the API tells us; both may be
    None when only the status code was available."""

    def __init__(self, message: str = "OpenSubtitles quota exceeded",
                 *, remaining: int | None = None, reset_hint: str | None = None):
        super().__init__(message)
        self.remaining = remaining
        self.reset_hint = reset_hint
