"""Kira Packs — community metadata for fan-edit releases.

A "pack" is a small JSON document (hosted at any URL the user supplies) that
describes a show, its episodes, and optional per-episode subtitles for a
release the normal providers can't / won't match — One Pace, Muhn Pace, custom
re-cuts. Kira ingests it as authoritative metadata for exactly those files.

**The isolation guarantee.** Packs are consulted at ONE point in the pipeline:
the moment a file is about to be stamped ``no_match`` (every provider came up
empty). A pack therefore *structurally cannot* alter a title the providers
already matched — that is the entire safety model, and it's why Kira refusing
to auto-match fan-edits (One Pace scores ~0.73 vs "One Piece" → no_match) is the
feature, not a bug. An optional, scope-restricted ``override`` authority exists
for the rare wrong-provider-match case; it is off by default and may only ever
touch files under explicitly-listed folders.

The subsystem is self-contained:
  • ``schema``   — the pack JSON + binding models, safety caps, regex sanitizer.
  • ``loader``   — fetch + 24h cache + parse (mirrors providers/anime_lists.py).
  • ``resolver`` — the pure gate + episode-claim ladder.
  • ``apply``    — write the pack Match row (and fire subtitle fetch).
  • ``subs``     — drop the pack's subtitles via the existing subtitle pipeline.

Nothing here is imported by the core matcher except the two thin seams in
``api/scans.py`` (the no_match hook) and ``api/rename.py`` (use pack numbers
verbatim).
"""
from __future__ import annotations

PACK_PROVIDER = "pack"  # Match.provider value for a pack-sourced row
