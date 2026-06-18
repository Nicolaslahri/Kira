"""Background integration health monitor — snapshot shape + transition notifs.

The monitor probes each CONFIGURED integration on a loop and stores the latest
{ok, detail, checked_at} per integration. It must:
  * only notify on an ok↔failed TRANSITION (never every cycle → no spam),
  * never notify on the FIRST observation (a box that was already broken before
    Kira started shouldn't fire a "connection lost" alert at boot),
  * record a configured integration's result and omit unconfigured ones,
  * survive a probe raising (best-effort → ok=False, loop lives).

These tests drive `run_checks()` directly with the config + probe functions
patched, so no real network or DB I/O happens.
"""

from __future__ import annotations

import pytest

from kira.integrations import health_monitor as hm


@pytest.fixture
def fresh_monitor():
    """A clean HealthMonitor with an empty snapshot per test."""
    return hm.HealthMonitor()


def _patch_configs(monkeypatch, configs: dict):
    async def _fake_load(_session):
        return configs
    monkeypatch.setattr(hm, "_load_integration_configs", _fake_load)


def _patch_session(monkeypatch):
    """run_checks opens a session purely to read config; the loader is patched,
    so a trivial async-context stand-in is enough."""
    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    monkeypatch.setattr(hm, "SessionLocal", lambda: _S())


def _capture_notifications(monkeypatch) -> list[dict]:
    sent: list[dict] = []

    async def _fake_notify(*, kind, title, body):
        sent.append({"kind": kind, "title": title, "body": body})

    monkeypatch.setattr(hm, "_notify", _fake_notify)
    return sent


async def test_first_observation_records_but_does_not_notify(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {"sonarr": object()})
    sent = _capture_notifications(monkeypatch)

    async def _probe(_key, _cfg):
        return False, "Sonarr rejected the API key (401)."
    monkeypatch.setattr(hm, "_probe", _probe)

    await fresh_monitor.run_checks()

    snap = fresh_monitor.snapshot()
    assert "sonarr" in snap
    assert snap["sonarr"]["ok"] is False
    assert snap["sonarr"]["detail"] == "Sonarr rejected the API key (401)."
    assert "checked_at" in snap["sonarr"]
    # Crucially: NO notification on the very first observation, even though it's
    # a failure (it may have been broken before Kira booted).
    assert sent == []


async def test_ok_then_failed_fires_one_warning(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {"sonarr": object()})
    sent = _capture_notifications(monkeypatch)

    results = iter([(True, "Connected"), (False, "Cannot reach Sonarr: timeout")])

    async def _probe(_key, _cfg):
        return next(results)
    monkeypatch.setattr(hm, "_probe", _probe)

    await fresh_monitor.run_checks()   # ok (first obs, no notify)
    await fresh_monitor.run_checks()   # ok→failed transition → one warning

    assert len(sent) == 1
    assert sent[0]["kind"] == "warning"
    assert "Sonarr" in sent[0]["title"]
    assert fresh_monitor.snapshot()["sonarr"]["ok"] is False


async def test_failed_then_ok_fires_one_success(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {"jellyfin": {"url": "http://jf", "api_key": "k"}})
    sent = _capture_notifications(monkeypatch)

    results = iter([(False, "401"), (True, "Connected")])

    async def _probe(_key, _cfg):
        return next(results)
    monkeypatch.setattr(hm, "_probe", _probe)

    await fresh_monitor.run_checks()   # failed (first obs, no notify)
    await fresh_monitor.run_checks()   # failed→ok transition → one success

    assert len(sent) == 1
    assert sent[0]["kind"] == "success"
    assert "Jellyfin" in sent[0]["title"]


async def test_steady_state_does_not_spam(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {"plex": {"url": "http://plex", "token": "t"}})
    sent = _capture_notifications(monkeypatch)

    async def _probe(_key, _cfg):
        return True, "Connected"
    monkeypatch.setattr(hm, "_probe", _probe)

    for _ in range(5):
        await fresh_monitor.run_checks()

    # Five identical "ok" cycles → zero notifications (no transitions).
    assert sent == []
    assert fresh_monitor.snapshot()["plex"]["ok"] is True


async def test_unconfigured_integration_is_omitted(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {})  # nothing configured
    sent = _capture_notifications(monkeypatch)

    async def _probe(_key, _cfg):  # should never be called
        raise AssertionError("probe must not run for an unconfigured integration")
    monkeypatch.setattr(hm, "_probe", _probe)

    await fresh_monitor.run_checks()
    assert fresh_monitor.snapshot() == {}
    assert sent == []


async def test_probe_exception_is_isolated(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    _patch_configs(monkeypatch, {"sonarr": object()})
    sent = _capture_notifications(monkeypatch)

    async def _probe(_key, _cfg):
        raise RuntimeError("unexpected boom")
    monkeypatch.setattr(hm, "_probe", _probe)

    # Must not raise — a probe blowing up is recorded as a failure, loop lives.
    await fresh_monitor.run_checks()
    snap = fresh_monitor.snapshot()
    assert snap["sonarr"]["ok"] is False
    assert "boom" in snap["sonarr"]["detail"]


async def test_becoming_unconfigured_drops_snapshot_entry(monkeypatch, fresh_monitor):
    _patch_session(monkeypatch)
    sent = _capture_notifications(monkeypatch)

    async def _probe(_key, _cfg):
        return True, "Connected"
    monkeypatch.setattr(hm, "_probe", _probe)

    _patch_configs(monkeypatch, {"sonarr": object()})
    await fresh_monitor.run_checks()
    assert "sonarr" in fresh_monitor.snapshot()

    # User clears the Sonarr creds → next cycle sees it unconfigured → drop it.
    _patch_configs(monkeypatch, {})
    await fresh_monitor.run_checks()
    assert "sonarr" not in fresh_monitor.snapshot()


async def test_endpoint_returns_snapshot(monkeypatch):
    """GET /integrations/health serves the module singleton's snapshot."""
    from kira.api.integrations import integrations_health

    monkeypatch.setattr(hm.monitor, "_snapshot", {
        "sonarr": {"ok": True, "detail": "Connected", "checked_at": "2026-06-17T00:00:00+00:00"},
    })
    out = await integrations_health()
    assert out == {
        "sonarr": {"ok": True, "detail": "Connected", "checked_at": "2026-06-17T00:00:00+00:00"},
    }
    # Returned dict is a COPY — mutating it must not corrupt the live snapshot.
    out["sonarr"]["ok"] = False
    assert hm.monitor.snapshot()["sonarr"]["ok"] is True
