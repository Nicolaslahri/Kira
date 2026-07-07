"""Central logging configuration.

Every module logs through `logging.getLogger(__name__)` (the old `print()`
diagnostics were converted wholesale), so this one call decides format and
level for the whole backend. Uvicorn keeps its own access/error handlers —
we only configure the ROOT handler that kira.* loggers propagate to.

Level comes from the `KIRA_LOG_LEVEL` env var (DEBUG/INFO/WARNING/ERROR),
default INFO. An env var rather than a Settings row on purpose: logging must
be configured before the database is reachable, and "turn on debug logging"
is exactly the thing you do when the app won't boot.
"""
from __future__ import annotations

import logging
import os
import re

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s — %(message)s"
_DATEFMT = "%H:%M:%S"


# Mask secrets before they hit disk. TMDB puts `?api_key=…` in the request URL,
# so an httpx error repr (or any provider log line) can carry the user's key —
# and users paste logs into GitHub issues. Matches the secret PARAM NAMES only
# (not a bare "key", which would clobber cascade-trace `_ep_key=` noise),
# followed by `=` / `:` / URL-encoded `%3D`, and replaces the value with ***.
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|x-api-key|client[_-]?key|access[_-]?token|token|password|passwd|pwd)"
    r"(\s*[=:]\s*|%3D)([^\s&'\"]+)"
)


def scrub_secrets(text: str) -> str:
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}=***", text)


class _SecretScrubFilter(logging.Filter):
    """Rewrites each record's rendered message with secrets masked. Attached to
    the root handler so EVERY propagated record (kira.*, httpx, etc.) is scrubbed."""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
            scrubbed = scrub_secrets(msg)
            if scrubbed != msg:
                record.msg = scrubbed
                record.args = ()
        except Exception:
            pass
        return True


class _RingHandler(logging.Handler):
    """Bounded in-memory tail of every log record — backs GET /logs so the UI
    can show recent logs without a docker exec. 500 records ≈ a few minutes
    of busy scanning; the deque drops the oldest automatically."""

    def __init__(self, maxlen: int = 500):
        super().__init__()
        from collections import deque
        self.records: deque[dict] = deque(maxlen=maxlen)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.append({
                "ts": record.created,
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            })
        except Exception:  # noqa: BLE001 — logging must never raise
            pass


ring_handler: _RingHandler | None = None


def ring_tail(limit: int = 200, level: str | None = None) -> list[dict]:
    """Newest-last slice of the in-memory log ring, optionally min-level
    filtered ('WARNING' → warnings + errors only)."""
    if ring_handler is None:
        return []
    rows = list(ring_handler.records)
    if level:
        floor = getattr(logging, level.upper(), None)
        if isinstance(floor, int):
            rows = [r for r in rows
                    if getattr(logging, str(r["level"]), 0) >= floor]
    return rows[-max(1, min(limit, 500)):]


def setup_logging() -> None:
    global ring_handler
    level_name = os.environ.get("KIRA_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    # force=False: respect handlers an embedding context (tests, uvicorn
    # config files) may already have installed; basicConfig is then a no-op
    # and only the level still applies below.
    logging.basicConfig(level=level, format=_FORMAT, datefmt=_DATEFMT)
    logging.getLogger("kira").setLevel(level)
    # Third-party chatter that drowns the signal at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("alembic.runtime.migration").setLevel(logging.WARNING)
    # Credential scrubber on every root handler (handler-level filters see every
    # propagated record from every logger).
    _scrub = _SecretScrubFilter()
    for _h in logging.getLogger().handlers:
        _h.addFilter(_scrub)
    # In-memory ring for the UI log viewer — attached at ROOT so it sees every
    # propagated record, WITH the scrubber so secrets never reach the API.
    if ring_handler is None:
        ring_handler = _RingHandler()
        ring_handler.addFilter(_scrub)
        logging.getLogger().addHandler(ring_handler)
