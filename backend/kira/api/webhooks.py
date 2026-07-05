"""Inbound automation webhooks (Pass 6 #8) — Sonarr / Radarr post-import.

# (kind|event|path) -> monotonic ts of the last accepted webhook (30s window).
_RECENT_EVENTS: dict[str, float] = {}
A *arr server can POST here after it imports a release so Kira immediately
scans + matches (and, for an auto_rename watched folder, organizes) the new
file — no manual Scan click, no wait for the poll loop.

Security posture (deliberate — this is an unauthenticated LAN-reachable API):
  • TOKEN-GATED. Disabled unless `integrations.webhook.token` is set; the caller
    must echo it via `?token=` or the `X-Webhook-Token` header. No token
    configured → 404 (looks like the route doesn't exist). Wrong token → 403.
  • The payload is UNTRUSTED DATA. We never execute anything from it. The only
    action is "scan a path Kira already owns": any path in the body is honoured
    ONLY if it resolves under a configured library root / watch folder;
    otherwise it's ignored and we fall back to scanning the configured roots.
    An attacker-supplied path can never make Kira scan outside the library.

The scan is fired with source="auto", so it flows through the same
match → (optional) auto-rename → notify path as the watched-folders daemon.
"""
from __future__ import annotations

import posixpath
import secrets
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.models import Setting
from kira.settings_store import unwrap_str as _unwrap  # canonical settings-value unwrap

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ── pure helpers (unit-testable, no I/O) ─────────────────────────────────────

def extract_event_type(payload: dict[str, Any]) -> str:
    """Sonarr/Radarr both send an `eventType` (Test, Download, Rename, …)."""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("eventType") or "")


def extract_target_path(payload: dict[str, Any]) -> str | None:
    """Pull the most specific folder the event refers to.

    Sonarr → `series.path`; Radarr → `movie.folderPath`. Returns None when the
    payload carries no usable path (Test events, malformed bodies)."""
    if not isinstance(payload, dict):
        return None
    series = payload.get("series")
    if isinstance(series, dict):
        p = series.get("path")
        if isinstance(p, str) and p.strip():
            return p.strip()
    movie = payload.get("movie")
    if isinstance(movie, dict):
        p = movie.get("folderPath") or movie.get("path")
        if isinstance(p, str) and p.strip():
            return p.strip()
    return None


def _norm(p: str) -> str:
    # Forward-slash + collapse '.'/'..' lexically + strip trailing slash +
    # case-fold. The posixpath.normpath step is the traversal guard: without it
    # a payload like `/media/tv/../../etc/passwd` string-prefixes `/media/` and
    # would be accepted; collapsing it to `/etc/passwd` first makes the
    # containment check below reject it. (Lexical only — we don't follow
    # symlinks here; the scan reads, never writes, so that's an accepted scope.)
    return posixpath.normpath(p.replace("\\", "/")).rstrip("/").lower()


def path_under_roots(path: str, roots: list[str]) -> bool:
    """True when `path` IS one of `roots` or sits inside one — case/sep
    insensitive. The allowlist that keeps an untrusted webhook path from
    making Kira scan somewhere it shouldn't."""
    if not path:
        return False
    n = _norm(path)
    for r in roots:
        if not r:
            continue
        rn = _norm(r)
        if n == rn or n.startswith(rn + "/"):
            return True
    return False


def resolve_scan_paths(target_path: str | None, roots: list[str]) -> list[str]:
    """Decide what to scan: the event's path IF it's inside the library
    (faster, targeted), otherwise the full configured root set. Never returns
    an out-of-library path."""
    if target_path and path_under_roots(target_path, roots):
        return [target_path]
    return [r for r in roots if r]


# ── settings access ──────────────────────────────────────────────────────────

async def _configured_token(session: AsyncSession) -> str | None:
    row = await session.get(Setting, "integrations.webhook.token")
    return _unwrap(row.value) if row is not None else None


async def _configured_roots(session: AsyncSession) -> list[str]:
    roots: list[str] = []
    lib_row = await session.get(Setting, "paths.library_root")
    lib = _unwrap(lib_row.value) if lib_row is not None else None
    if lib:
        roots.append(lib)
    wf_row = await session.get(Setting, "paths.watch_folders")
    wf = wf_row.value if wf_row is not None else None
    if isinstance(wf, list):
        roots.extend(str(p) for p in wf if isinstance(p, str) and p.strip())
    return roots


def _check_token(provided: str | None, configured: str | None) -> None:
    """Raise the right HTTP error. No token configured → 404 (feature off);
    mismatch / missing → 403."""
    if not configured:
        raise HTTPException(status_code=404, detail="Webhooks are not enabled.")
    # Constant-time compare — a plain `!=` leaks the token via response timing.
    # Compared as BYTES: compare_digest raises TypeError on non-ASCII str, so a
    # non-ASCII header (or a user who saved a non-ASCII token) got an unhandled
    # 500 instead of a clean 403.
    if not provided or not secrets.compare_digest(
        provided.encode("utf-8"), configured.encode("utf-8")
    ):
        raise HTTPException(status_code=403, detail="Invalid or missing webhook token.")


async def _handle(kind: str, request: Request, token: str | None, session: AsyncSession) -> dict[str, Any]:
    configured = await _configured_token(session)
    # PREFER the `X-Webhook-Token` header over the `?token=` query param: a
    # query-string token leaks into proxy access logs / browser history, while
    # a header does not. The query param is still accepted for *arr clients that
    # can only configure a URL, but the header — when present — wins.
    _check_token(request.headers.get("X-Webhook-Token") or token, configured)

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    event = extract_event_type(payload)
    # Sonarr/Radarr fire a "Test" event from their UI's Test button.
    if event.lower() == "test":
        return {"ok": True, "test": True, "source": kind}

    # Only FILE-CHANGING events warrant a scan. Grab (queued, nothing on disk
    # yet), Health, ApplicationUpdate etc. used to each trigger a full scan.
    _ev = event.lower()
    if _ev and not any(k in _ev for k in ("download", "import", "rename", "delete", "upgrade")):
        return {"ok": True, "queued": False,
                "detail": f"Event {event!r} doesn't change files — no scan needed.",
                "source": kind}

    # Replay/duplicate suppression: *arr retries + double-fires (and a replayed
    # request) shouldn't stack scans. Same (kind, event, target) within 30s is
    # acked without starting another scan — the first one covers it.
    import time as _time
    _key = f"{kind}|{_ev}|{extract_target_path(payload) or ''}"
    _now = _time.monotonic()
    _stale = [k for k, t in _RECENT_EVENTS.items() if _now - t > 30.0]
    for k in _stale:
        _RECENT_EVENTS.pop(k, None)
    if _key in _RECENT_EVENTS:
        return {"ok": True, "queued": False, "detail": "Duplicate event (deduped).", "source": kind}
    _RECENT_EVENTS[_key] = _now

    roots = await _configured_roots(session)
    if not roots:
        return {"ok": False, "detail": "No library root / watch folders configured.", "source": kind}

    target = extract_target_path(payload)
    scan_paths = resolve_scan_paths(target, roots)

    from kira.api.scans import _start_scan
    scan_id = await _start_scan(scan_paths, source="auto")
    if scan_id is None:
        return {"ok": True, "queued": False, "detail": "A scan is already running.", "source": kind}
    return {"ok": True, "queued": True, "scan_id": scan_id, "scanned": scan_paths, "source": kind}


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/sonarr")
async def sonarr_webhook(
    request: Request,
    token: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Sonarr 'Connect → Webhook' target. Configure Sonarr to POST here with
    `?token=<your token>`."""
    return await _handle("sonarr", request, token, session)


@router.post("/radarr")
async def radarr_webhook(
    request: Request,
    token: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Radarr 'Connect → Webhook' target. Same token gate as Sonarr."""
    return await _handle("radarr", request, token, session)
