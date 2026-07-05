"""Neutral sidecar-naming helper shared by every subtitle source.

This is a pure path utility with no provider specifics. It lives here — at or
below the layer of all its consumers (embedded extraction, OpenSubtitles, YIFY)
— so a source module never has to reach into a *peer* provider just to name a
sidecar. `providers.opensubtitles` re-exports it for backward compatibility.
"""
from __future__ import annotations

import os
from pathlib import Path


def subtitle_sidecar_name(video_path: str | os.PathLike, language: str, ext: str = "srt",
                          *, forced: bool = False) -> str:
    """`<video stem>.<lang>[.forced].<ext>` — the Plex/Jellyfin language-tagged
    sidecar convention (default `.srt`; embedded extraction passes `ass`/`vtt`
    to keep a track's native format). `forced=True` inserts the standard
    `.forced` tag so a forced track and the full same-language sub can coexist
    (they used to collide on one name — the second variant was unreachable).
    The rename sidecar co-move carries both on every move. Pure."""
    stem = Path(video_path).stem
    tag = ".forced" if forced else ""
    return f"{stem}.{language.lower()}{tag}.{ext.lstrip('.').lower()}"
