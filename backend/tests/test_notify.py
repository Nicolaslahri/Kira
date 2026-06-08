"""Pass 6 #10 — outbound notification fan-out."""

from __future__ import annotations

from kira import notify


class _FakeRow:
    def __init__(self, value):
        self.value = value


class _FakeSession:
    def __init__(self, data: dict):
        self._data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, _model, key):
        v = self._data.get(key)
        return _FakeRow(v) if v is not None else None


def _patch_settings(monkeypatch, data: dict) -> None:
    monkeypatch.setattr(notify, "SessionLocal", lambda: _FakeSession(data))


def test_unwrap() -> None:
    assert notify._unwrap("x") == "x"
    assert notify._unwrap({"value": "y"}) == "y"
    assert notify._unwrap("") is None
    assert notify._unwrap(None) is None


async def test_no_sinks_configured(monkeypatch) -> None:
    _patch_settings(monkeypatch, {})
    assert await notify.fan_out("info", "Hi") == []


async def test_fires_both_sinks(monkeypatch) -> None:
    _patch_settings(monkeypatch, {
        "notifications.discord_webhook": "https://discord/webhook",
        "notifications.webhook_url": "https://example/hook",
    })
    calls: list[str] = []

    async def fake_discord(url, kind, title, body):
        calls.append(f"discord:{url}")

    async def fake_generic(url, kind, title, body):
        calls.append(f"generic:{url}")

    monkeypatch.setattr(notify, "_post_discord", fake_discord)
    monkeypatch.setattr(notify, "_post_generic", fake_generic)
    sent = await notify.fan_out("success", "Renamed 3 files", "move · Plex")
    assert sent == ["discord", "webhook"]
    assert calls == ["discord:https://discord/webhook", "generic:https://example/hook"]


async def test_one_sink_failure_does_not_block_other(monkeypatch) -> None:
    _patch_settings(monkeypatch, {
        "notifications.discord_webhook": "https://discord/webhook",
        "notifications.webhook_url": "https://example/hook",
    })

    async def boom(url, kind, title, body):
        raise RuntimeError("discord down")

    ok: list[str] = []

    async def fake_generic(url, kind, title, body):
        ok.append("generic")

    monkeypatch.setattr(notify, "_post_discord", boom)
    monkeypatch.setattr(notify, "_post_generic", fake_generic)
    # Discord raises, but generic still fires and fan_out never raises.
    sent = await notify.fan_out("error", "Something failed")
    assert sent == ["webhook"]
    assert ok == ["generic"]


async def test_settings_read_failure_is_isolated(monkeypatch) -> None:
    def boom():
        raise RuntimeError("db gone")

    monkeypatch.setattr(notify, "SessionLocal", boom)
    # Must not raise — returns [] when it can't even read settings.
    assert await notify.fan_out("info", "x") == []
