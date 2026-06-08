"""Subtitle sources for Kira.

Each module here pulls subtitles for a video from one source and writes
language-tagged sidecars (`<stem>.<lang>.srt` / `.ass`) beside it, returning
the saved paths. They share the contract:

    async def fetch(video_path, languages, *, ...) -> list[str]   # saved paths

so the rename flow can call several in order, each skipping languages a prior
source already satisfied. `embedded` is local/offline; the network sources
(OpenSubtitles, AnimeTosho, …) are key/credential-gated and best-effort.

Phase 1: `embedded` (this package) + the existing `providers/opensubtitles.py`.
Later phases fold OpenSubtitles in here behind a small registry and add
AnimeTosho + the HTML-scraper sources.
"""
