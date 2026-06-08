"""Canonical accessors for the `settings` table value shape.

Settings rows store their value in one of two shapes — a bare scalar
(``"abc"``, ``true``, ``["en"]``) or a wrapped dict (``{"value": "abc"}``).
Historically every consumer re-implemented the same peel-the-wrapper logic
(``_unwrap`` / ``_coerce_str`` / ``_setting_value`` / ``_unwrap_path`` …) in at
least seven modules, so any change to the storage convention had to be made in
lockstep across all of them and a single missed copy became a silent data-read
bug. This module is the single source of truth: callers import from here.
"""
from __future__ import annotations

from typing import Any


def unwrap(v: Any) -> Any:
    """Peel the optional ``{"value": ...}`` wrapper and return the inner value
    unchanged (str, bool, list, …). A bare value passes through as-is."""
    if isinstance(v, dict) and "value" in v:
        return v.get("value")
    return v


def unwrap_str(v: Any) -> str | None:
    """The inner value as a clean, non-empty ``str`` — else ``None``.

    Trailing/leading whitespace is stripped; an empty/whitespace-only string or
    a non-string inner value yields ``None``."""
    inner = unwrap(v)
    if isinstance(inner, str):
        return inner.strip() or None
    return None


async def get_str(session, key: str) -> str | None:
    """Load setting ``key`` and return it as a clean ``str`` (or ``None`` when
    the row is absent, empty, or non-string). Centralises the
    ``session.get(Setting, key)`` + unwrap pattern."""
    from kira.models import Setting  # lazy: keep the pure unwrap helpers ORM-free

    row = await session.get(Setting, key)
    return unwrap_str(row.value) if row is not None else None


async def get_raw(session, key: str) -> Any:
    """Load setting ``key`` and return its inner value unwrapped (any type), or
    ``None`` when the row is absent."""
    from kira.models import Setting  # lazy: keep the pure unwrap helpers ORM-free

    row = await session.get(Setting, key)
    return unwrap(row.value) if row is not None else None
