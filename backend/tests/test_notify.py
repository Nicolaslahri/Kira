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


# ── HTTP-failure handling (audit: 429/4xx were swallowed as "sent") ───────────
class _FakeResp:
    def __init__(self, status: int):
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self)  # type: ignore[arg-type]


class _FakeClient:
    def __init__(self, status: int):
        self._status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return _FakeResp(self._status)


async def test_discord_429_not_counted_as_sent(monkeypatch) -> None:
    _patch_settings(monkeypatch, {"notifications.discord_webhook": "https://discord/webhook"})
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda *a, **k: _FakeClient(429))
    # A rate-limited (429) push must NOT be reported as delivered.
    sent = await notify.fan_out("info", "burst")
    assert sent == []


async def test_discord_404_not_counted_as_sent(monkeypatch) -> None:
    _patch_settings(monkeypatch, {"notifications.discord_webhook": "https://discord/webhook"})
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda *a, **k: _FakeClient(404))
    sent = await notify.fan_out("info", "deleted webhook")
    assert sent == []


async def test_discord_200_counted_as_sent(monkeypatch) -> None:
    _patch_settings(monkeypatch, {"notifications.discord_webhook": "https://discord/webhook"})
    monkeypatch.setattr(notify.httpx, "AsyncClient", lambda *a, **k: _FakeClient(204))
    sent = await notify.fan_out("info", "ok")
    assert sent == ["discord"]


def test_truncate_markdown_never_cuts_mid_bold() -> None:
    # A title long enough to cut inside the **bold** run must not leave a
    # dangling `**`.
    long = notify._truncate_markdown("✅ **" + "x" * 3000 + "**", limit=100)
    assert long.count("**") % 2 == 0
