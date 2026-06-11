"""Cleanup helpers shared by the rename teardown + undo paths.

NOTE: the standalone library-wide artifact *sweep* (the on-demand button + the
per-scan auto-sweep) was removed. A global sweep is the wrong tool — it can't tell
a CURRENT file's Jellyfin/Plex artwork (which the server just regenerates) from
genuinely orphaned junk, so it churns endlessly and fights the media server, and
it even re-processed its own trash folder. Artifact cleanup now happens only at
"time of vacancy": when a rename moves the last media file OUT of a folder, the
move-time cleanup (`operations._cleanup_empty_source_parents`, on by default) and
the undo path remove the now-media-less folder + its leftover artifacts — exactly
when, and only when, a folder becomes useless. This module keeps just the shared
trash-target resolver those paths use.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession


async def _resolve_trash_root(session: AsyncSession, roots: list[str]) -> Path | None:
    """Recoverable-trash target, reusing the same settings the rename cleanup uses
    (`rename.cleanup_trash` + `rename.trash_dir`, default `<root>/.kira-trash`).
    None → hard delete."""
    from kira.api.rename import _resolve_bool_setting, _resolve_str_setting
    if not await _resolve_bool_setting(session, "rename.cleanup_trash", False):
        return None
    override = await _resolve_str_setting(session, "rename.trash_dir", "")
    if override:
        return Path(override)
    if roots:
        return Path(roots[0]) / ".kira-trash"
    return None
