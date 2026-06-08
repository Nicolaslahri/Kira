"""Media-type correction + key recompute (CR-09).

When a provider tells us a file's true media_type differs from the parser's
guess — most commonly an AniDB (anime-only source) match on a file the parser
typed ``tv`` because it lived outside an ``/anime/`` path — we must (a) flip
``media_file.media_type`` and (b) recompute the series/variant clustering keys
off the corrected type so the file re-clusters under its real identity.

This exact block was triplicated (once in ``kira.api.scans._match_phase``,
twice in ``kira.api.matches``). ``apply_media_type_and_recompute_keys`` is the
single source of truth. The signature is a CONTRACT — Wave 3 and the
``matches.py`` owner depend on it; keep it stable.
"""

from kira.matcher.keys import compute_series_key, compute_variant_key
from kira.parser import ParsedFile


def apply_media_type_and_recompute_keys(media_file, new_media_type: str) -> None:
    """Set ``media_file.media_type`` to ``new_media_type`` and recompute keys.

    Rebuilds a :class:`ParsedFile` from ``media_file.parsed_data`` (filtered to
    the dataclass's own fields so stale/extra keys can't break construction),
    stamps the new media_type onto BOTH the rebuilt ParsedFile and the
    ``media_file`` row, recomputes ``series_key``/``variant_key`` via
    ``kira.matcher.keys``, and writes the refreshed ``parsed_data`` back.

    Mutates ``media_file`` IN PLACE; the caller owns the surrounding session /
    commit and try/except. If ``parsed_data`` is empty/absent this is a no-op
    aside from setting ``media_type`` (there are no keys to recompute without
    parse data).

    :param media_file: a ``MediaFile`` ORM row (anything exposing
        ``parsed_data``, ``media_type``, ``series_key``, ``variant_key``).
    :param new_media_type: the corrected media type (e.g. ``"anime"``).
    """
    media_file.media_type = new_media_type
    if not media_file.parsed_data:
        return
    parsed = ParsedFile(**{
        k: v for k, v in media_file.parsed_data.items()
        if k in ParsedFile.__dataclass_fields__
    })
    parsed.media_type = new_media_type
    media_file.parsed_data = parsed.to_dict()
    media_file.series_key = compute_series_key(parsed)
    media_file.variant_key = compute_variant_key(parsed)
