"""Outbound notification fan-out (Pass 6 #10).

Kira already writes in-app `Notification` rows (the bell popover). This adds an
OUTBOUND leg: when a noteworthy event fires (rename batch done, auto-scan found
new files), also push it to the user's configured external sinks so they hear
about it without opening the UI.

Sinks (all optional; read from the Setting table):
  notifications.discord_webhook   a Discord channel webhook URL
  notifications.webhook_url       a generic JSON POST endpoint (Apprise, n8n, …)

Everything here is best-effort and exception-isolated — a notification sink
being down must NEVER affect a rename/scan. Opens its own DB session so it can
be called from anywhere (request handler or background worker).
"""
from __future__ import annotations

import logging

import httpx

from kira.database import SessionLocal
from kira.models import Setting
from kira.settings_store import unwrap_str as _unwrap  # canonical settings-value unwrap

_log = logging.getLogger("kira.notify")

_TIMEOUT = 8.0


async def _post_discord(webhook_url: str, kind: str, title: str, body: str | None) -> None:
    # Discord webhooks take a `content` field (markdown). Prefix with an emoji
    # by severity so it's scannable in a busy channel.
    emoji = {"success": "✅", "error": "❌", "warning": "⚠️", "info": "ℹ️"}.get(kind, "ℹ️")
    content = f"{emoji} **{title}**"
    if body:
        content += f"\n{body}"
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        await client.post(webhook_url, json={"content": content[:1900]})


async def _post_generic(webhook_url: str, kind: str, title: str, body: str | None) -> None:
    # A plain JSON envelope any generic receiver (Apprise, n8n, a custom
    # script) can consume.
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        await client.post(webhook_url, json={
            "source": "kira",
            "kind": kind,
            "title": title,
            "body": body or "",
        })


async def fan_out(kind: str, title: str, body: str | None = None) -> list[str]:
    """Push an event to every configured external sink. Returns the sink names
    that accepted it (for logging). Best-effort — never raises."""
    try:
        async with SessionLocal() as session:
            d_row = await session.get(Setting, "notifications.discord_webhook")
            g_row = await session.get(Setting, "notifications.webhook_url")
        discord = _unwrap(d_row.value) if d_row is not None else None
        generic = _unwrap(g_row.value) if g_row is not None else None
    except Exception as e:  # noqa: BLE001
        _log.warning("notify: settings read failed: %r", e)
        return []

    from kira.url_guard import validate_outbound_url

    sent: list[str] = []
    if discord:
        try:
            validate_outbound_url(discord)  # SSRF guard before we POST event data
            await _post_discord(discord, kind, title, body)
            sent.append("discord")
        except ValueError as e:  # unsafe URL — distinct from a transport failure
            _log.warning("notify: discord webhook rejected: %s", e)
        except Exception as e:  # noqa: BLE001
            _log.warning("notify: discord push failed: %r", e)
    if generic:
        try:
            validate_outbound_url(generic)
            await _post_generic(generic, kind, title, body)
            sent.append("webhook")
        except ValueError as e:
            _log.warning("notify: generic webhook rejected: %s", e)
        except Exception as e:  # noqa: BLE001
            _log.warning("notify: generic push failed: %r", e)
    return sent
