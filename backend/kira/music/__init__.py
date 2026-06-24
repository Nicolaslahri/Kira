"""Music subsystem — an ISOLATED, plugin-style matcher for `media_type="music"`.

Deliberately self-contained (the Kira Packs isolation model applied to a whole
media type): the movie/TV/anime matching cascade NEVER processes a music file
(`matcher/engine.py::match` short-circuits `media_type=="music"` with `return []`),
and nothing in here reaches into that cascade. The pipeline consults this
subsystem at exactly ONE seam — the `media_type=="music"` branch in
`api/scans.py` — gated behind the `music.enabled` setting (default OFF), so
nothing here can affect a non-music user until they explicitly opt in.

Matcher quality ladder (best signal first):
  1. embedded MusicBrainz IDs in the file's tags → direct release/recording lookup
  2. tags + folder → MusicBrainz album search    → score candidates, assign tracks
  3. AcoustID acoustic fingerprint               → recording → MusicBrainz
Embedded tags (read via mutagen in `tags.py`) are the gold signal — far more
reliable than filename parsing, and they often already carry the MusicBrainz IDs.
"""
