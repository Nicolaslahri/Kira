"""Background health checker for external integrations (Sonarr / Radarr / Plex / Jellyfin).

Tells the user when a configured integration's connection breaks WITHOUT them
having to click "Test connection" in Settings. A periodic loop probes each
CONFIGURED integration with its existing connection test (short timeout), stores
the latest result in a module-level snapshot, and fires a single Notification on
each ok→failed (and failed→ok) TRANSITION — never every cycle, so no spam.

Design (mirrors `kira.watcher`):
- One asyncio task, armed in the FastAPI lifespan, cancelled cleanly on shutdown.
- Best-effort to a fault: a check failure records ok=False with the error
  detail; ANY unexpected error is swallowed so the loop (and the app) survives.
- Cheap + non-intrusive: configured-only, SHORT per-request timeout, and the DB
  is touched only briefly to read config — the network probe runs OUTSIDE any
  held session, so this never contends with the scan/rename write path.

Import discipline: only stdlib + `kira.database` at module top. Everything under
`kira.integrations.*` / `kira.models` is imported lazily inside methods to keep
import cycles impossible (integrations.py imports this module's helpers in turn).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from kira.database import SessionLocal

_log = logging.getLogger("kira.health_monitor")

# How often to probe. Not critical — 5 min is frequent enough to catch a broken
# connection well before the user next opens Settings, cheap enough to be
# invisible (a handful of sub-second HTTP calls against the user's own LAN).
CHECK_INTERVAL_SECONDS = 300

# Per-probe network timeout. Deliberately short: a health check is a background
# nicety, not a request the user is waiting on, so we'd rather call a slow box
# "failed (timeout)" than let a hung socket stall the loop. Sonarr/Plex/Jellyfin
# on a healthy LAN answer their status endpoints in well under a second.
PROBE_TIMEOUT_SECONDS = 5.0

# Consecutive failures required before firing a "connection lost" notification.
# With a 5-min interval, 3 means ~10-15 min of sustained failure — long enough
# that a single slow/dropped probe (a NAS-hosted Sonarr answering in >5s under
# load) doesn't generate a lost/restored notification pair every few minutes.
_FAIL_THRESHOLD = 3

# The integrations we monitor. Keys are the snapshot keys the endpoint returns
# and the frontend polls on.
INTEGRATION_KEYS = ("sonarr", "radarr", "plex", "jellyfin")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class HealthMonitor:
    """Singleton daemon: one periodic loop + a per-integration result snapshot.

    Snapshot shape, per configured integration key:
        {"ok": bool, "detail": str, "checked_at": <iso8601>}
    Unconfigured integrations are absent from the snapshot (the endpoint reports
    them as status "unconfigured").
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None
        # key -> {"ok", "detail", "checked_at"}. Module-level state, read by the
        # endpoint, written only by the loop (single writer → no lock needed).
        self._snapshot: dict[str, dict[str, Any]] = {}
        # key -> {"fails": int, "alerted_down": bool} — debounce state so a
        # transient slow probe doesn't spam lost/restored notifications.
        self._alert_state: dict[str, dict[str, Any]] = {}

    # ── lifecycle ────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Arm the periodic checker. Idempotent and never raises — a startup
        problem here must not block boot (mirrors watcher.start())."""
        if self._task is not None and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        # Strong reference held on self so the GC can't collect the bare task
        # mid-flight (same trap the warmup task in main.py guards against).
        self._task = asyncio.create_task(self._loop(), name="kira-health-monitor")
        _log.info("health monitor: armed (interval=%ds, timeout=%.0fs)",
                  CHECK_INTERVAL_SECONDS, PROBE_TIMEOUT_SECONDS)

    async def stop(self) -> None:
        """Cancel the loop cleanly. Safe to call when never started."""
        if self._stop_event is not None:
            self._stop_event.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:  # noqa: BLE001
                _log.debug("health monitor: task ended with %r", e)
        self._stop_event = None

    # ── loop ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        try:
            # A short initial delay lets boot settle (init_db, watcher arm,
            # warmups) before we add background HTTP — and avoids racing the
            # very first request. The first real check runs after this.
            await self._sleep_or_stop(10)
            while self._stop_event is not None and not self._stop_event.is_set():
                try:
                    await self.run_checks()
                except Exception as e:  # noqa: BLE001 — a cycle failure must never kill the loop
                    _log.warning("health monitor: check cycle failed (non-fatal): %r", e)
                await self._sleep_or_stop(CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise

    async def _sleep_or_stop(self, seconds: float) -> None:
        """Sleep up to `seconds`, returning early if asked to stop — so shutdown
        doesn't wait out a full 5-minute interval."""
        if self._stop_event is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    # ── checks ───────────────────────────────────────────────────────────

    async def run_checks(self) -> None:
        """Run one probe cycle over every CONFIGURED integration.

        Reads all integration settings in ONE short-lived session, then releases
        it BEFORE doing any network I/O — so a slow probe can't pin a DB
        connection while a scan/rename is trying to commit. Notifications for
        state transitions open their own brief sessions.
        """
        # 1. Read config quickly (no network while the session is open).
        try:
            async with SessionLocal() as session:
                configs = await _load_integration_configs(session)
        except Exception as e:  # noqa: BLE001
            _log.warning("health monitor: failed to read integration config: %r", e)
            return

        # 2. Probe each configured integration OUTSIDE the session. Each probe is
        #    independently exception-isolated so one bad box can't skip the rest.
        for key in INTEGRATION_KEYS:
            cfg = configs.get(key)
            if cfg is None:
                # Not configured → drop any stale snapshot entry so the endpoint
                # reports "unconfigured" again after the user clears creds.
                self._snapshot.pop(key, None)
                continue
            try:
                ok, detail = await _probe(key, cfg)
            except Exception as e:  # noqa: BLE001 — never let a probe crash the cycle
                ok, detail = False, f"Health check error: {e}"
            await self._record(key, ok, detail)

    async def _record(self, key: str, ok: bool, detail: str) -> None:
        """Store the latest result and fire a Notification only on a DEBOUNCED
        state change. A single slow probe (>5s timeout under load) would
        otherwise flip ok→failed and back, spamming lost/restored pairs — so we
        require `_FAIL_THRESHOLD` consecutive failures before alerting 'down',
        and only alert 'restored' if we actually alerted 'down' first."""
        self._snapshot[key] = {"ok": ok, "detail": detail, "checked_at": _now_iso()}
        st = self._alert_state.setdefault(key, {"fails": 0, "alerted_down": False})

        try:
            if ok:
                # Recovered. Only announce if we'd previously announced the
                # outage — otherwise a transient blip stays silent.
                was_down = st["alerted_down"]
                st["fails"] = 0
                st["alerted_down"] = False
                if was_down:
                    await _notify(
                        kind="success",
                        title=f"{_label(key)} connection restored",
                        body=f"{_label(key)} is reachable again.",
                    )
            else:
                st["fails"] += 1
                # Alert once, only after sustained failure (not the first blip).
                if st["fails"] >= _FAIL_THRESHOLD and not st["alerted_down"]:
                    st["alerted_down"] = True
                    await _notify(
                        kind="warning",
                        title=f"{_label(key)} connection lost",
                        body=(
                            f"Kira's background health check can no longer reach "
                            f"{_label(key)} ({st['fails']} checks in a row): {detail} "
                            f"Check the integration in Settings → Integrations."
                        ),
                    )
        except Exception as e:  # noqa: BLE001 — a notify failure must not break the loop
            _log.warning("health monitor: notify failed (non-fatal): %r", e)

    # ── snapshot accessors ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """A shallow copy of the latest per-integration results (configured
        integrations only). The endpoint serialises this directly."""
        return {k: dict(v) for k, v in self._snapshot.items()}


# Module-level singleton — imported by main.py (lifespan) + integrations.py (endpoint).
monitor = HealthMonitor()


# ─────────────────────────────────────────────────────────────────────
# Config loading + probes — module functions so tests can patch them and the
# transition logic can be exercised without real network I/O.
# ─────────────────────────────────────────────────────────────────────


def _label(key: str) -> str:
    return {"sonarr": "Sonarr", "radarr": "Radarr", "plex": "Plex", "jellyfin": "Jellyfin"}.get(key, key.title())


async def _load_integration_configs(session) -> dict[str, Any]:
    """Build a {key: config} dict for every CONFIGURED integration. Keys absent
    from the result are unconfigured (URL and/or credential missing) and are not
    probed.

    Reuses the SAME loaders the API + rename hooks use, so "configured" means
    exactly what the rest of Kira means by it:
      * Sonarr → `_load_sonarr_config` (raises HTTPException when URL/key unset)
      * Plex / Jellyfin → URL + token/key both present (same gate as refresh_all)
    """
    configs: dict[str, Any] = {}

    # Sonarr: the test only needs URL + API key (quality profile / root folder
    # are irrelevant to /system/status), so the loader's HTTPException on a
    # missing URL or key cleanly means "unconfigured".
    try:
        from fastapi import HTTPException

        from kira.api.integrations import _load_sonarr_config
        try:
            configs["sonarr"] = await _load_sonarr_config(session)
        except HTTPException:
            pass  # not configured
    except Exception as e:  # noqa: BLE001
        _log.debug("health monitor: sonarr config load skipped: %r", e)

    # Radarr: same shape — `_load_radarr_config` raises HTTPException when the
    # URL or key is unset, which cleanly means "unconfigured".
    try:
        from fastapi import HTTPException

        from kira.api.integrations import _load_radarr_config
        try:
            configs["radarr"] = await _load_radarr_config(session)
        except HTTPException:
            pass  # not configured
    except Exception as e:  # noqa: BLE001
        _log.debug("health monitor: radarr config load skipped: %r", e)

    # Plex / Jellyfin: same "both fields present" gate refresh_all uses.
    try:
        from kira.settings_store import get_str
        plex_url = await get_str(session, "integrations.plex.url")
        plex_token = await get_str(session, "integrations.plex.token")
        if plex_url and plex_token:
            configs["plex"] = {"url": plex_url, "token": plex_token}
        jf_url = await get_str(session, "integrations.jellyfin.url")
        jf_key = await get_str(session, "integrations.jellyfin.api_key")
        if jf_url and jf_key:
            configs["jellyfin"] = {"url": jf_url, "api_key": jf_key}
    except Exception as e:  # noqa: BLE001
        _log.debug("health monitor: media-server config load skipped: %r", e)

    return configs


async def _probe(key: str, cfg: Any) -> tuple[bool, str]:
    """Probe ONE configured integration. Returns (ok, detail).

    Reuses each integration's existing connection test with a SHORT timeout
    (`PROBE_TIMEOUT_SECONDS`). Never raises for an expected failure — a bad
    URL / refused connection / non-2xx becomes (False, "<reason>")."""
    if key == "sonarr":
        return await _probe_sonarr(cfg)
    if key == "radarr":
        return await _probe_radarr(cfg)
    if key == "plex":
        return await _probe_plex(cfg["url"], cfg["token"])
    if key == "jellyfin":
        return await _probe_jellyfin(cfg["url"], cfg["api_key"])
    return False, f"Unknown integration: {key}"


async def _probe_sonarr(cfg: Any) -> tuple[bool, str]:
    """Reuse Sonarr's `test_connection`, but with the short health-probe timeout
    (the shared client default is 10s — too long for a background nicety). We
    shorten via a hard `asyncio.wait_for` wrapper so we don't have to thread a
    timeout through the existing helper."""
    from kira.integrations.sonarr import SonarrError, test_connection
    try:
        status = await asyncio.wait_for(test_connection(cfg), timeout=PROBE_TIMEOUT_SECONDS)
        version = status.get("version") if isinstance(status, dict) else None
        return True, f"Connected (v{version})" if version else "Connected"
    except asyncio.TimeoutError:
        return False, f"Timed out after {PROBE_TIMEOUT_SECONDS:.0f}s"
    except SonarrError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001 — non-encodable key etc. (same trap as the test endpoint)
        return False, f"{e}"


async def _probe_radarr(cfg: Any) -> tuple[bool, str]:
    """Reuse Radarr's `test_connection` with the short health-probe timeout —
    the movie mirror of `_probe_sonarr`."""
    from kira.integrations.radarr import RadarrError, test_connection
    try:
        status = await asyncio.wait_for(test_connection(cfg), timeout=PROBE_TIMEOUT_SECONDS)
        version = status.get("version") if isinstance(status, dict) else None
        return True, f"Connected (v{version})" if version else "Connected"
    except asyncio.TimeoutError:
        return False, f"Timed out after {PROBE_TIMEOUT_SECONDS:.0f}s"
    except RadarrError as e:
        return False, str(e)
    except Exception as e:  # noqa: BLE001
        return False, f"{e}"


async def _probe_plex(url: str, token: str) -> tuple[bool, str]:
    """Plex liveness via the documented `/identity` endpoint — unauthenticated-
    safe, returns the server's machine identifier. We still send the token so a
    locked-down Plex (which 401s anonymous /identity) reports a real connection
    rather than a false failure."""
    import httpx

    from kira.url_guard import is_safe_outbound_url
    ok_url, reason = is_safe_outbound_url(url)
    if not ok_url:
        return False, f"URL rejected: {reason}"
    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            r = await client.get(
                f"{base}/identity",
                headers={"X-Plex-Token": token, "Accept": "application/json"},
            )
    except httpx.TimeoutException:
        return False, f"Timed out after {PROBE_TIMEOUT_SECONDS:.0f}s"
    except Exception as e:  # noqa: BLE001
        return False, f"Cannot reach Plex: {e}"
    if r.status_code == 401:
        return False, "Plex rejected the token (401)."
    if r.status_code >= 400:
        return False, f"Plex returned HTTP {r.status_code}."
    return True, "Connected"


async def _probe_jellyfin(url: str, api_key: str) -> tuple[bool, str]:
    """Jellyfin liveness via `/System/Info` (auth-gated), with the key in the
    `X-Emby-Token` header (Jellyfin inherits Emby's auth header — same as
    refresh_jellyfin). A 401 means the key is wrong; a 2xx proves both
    reachability AND a valid key."""
    import httpx

    from kira.url_guard import is_safe_outbound_url
    ok_url, reason = is_safe_outbound_url(url)
    if not ok_url:
        return False, f"URL rejected: {reason}"
    base = url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
            r = await client.get(
                f"{base}/System/Info",
                headers={"X-Emby-Token": api_key, "Accept": "application/json"},
            )
    except httpx.TimeoutException:
        return False, f"Timed out after {PROBE_TIMEOUT_SECONDS:.0f}s"
    except Exception as e:  # noqa: BLE001
        return False, f"Cannot reach Jellyfin: {e}"
    if r.status_code == 401:
        return False, "Jellyfin rejected the API key (401)."
    if r.status_code >= 400:
        return False, f"Jellyfin returned HTTP {r.status_code}."
    return True, "Connected"


async def _notify(*, kind: str, title: str, body: str) -> None:
    """Add ONE Notification in its own brief session (the bell surfaces it).
    Mirrors the `session.add(Notification(...))` pattern used across the app."""
    from kira.models import Notification
    async with SessionLocal() as session:
        session.add(Notification(kind=kind, title=title, body=body))
        await session.commit()
