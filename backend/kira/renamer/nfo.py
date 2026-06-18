"""Kodi / Emby / Jellyfin .nfo sidecar generation (Pass 7 #12).

Kodi-flavoured NFO files let Kodi/Emby (and Jellyfin in NFO mode) read Kira's
resolved metadata straight off disk — title, plot, genres, cast, IDs — instead
of re-scraping. We write them beside the renamed video from the data already on
`Match.metadata_blob`, so it's pure output: no API calls, no failure mode that
can affect the rename.

Three documents (Kodi conventions):
  • movie     → `<Movie (Year)>.nfo`     `<movie>`
  • episode   → `<… SxxExx …>.nfo`        `<episodedetails>`
  • series    → `tvshow.nfo` in the show root `<tvshow>` (write-if-absent)

The builders are pure string functions (unit-tested); `plan_nfo_writes` decides
the file set; the rename hook does the actual disk writes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

_HEADER = '<?xml version="1.0" encoding="UTF-8"?>'

# XML 1.0 (§2.2 Char) forbids most C0 control bytes — only TAB/LF/CR are legal —
# plus the surrogate block and U+FFFE/FFFF. saxutils.escape() only handles
# & < >, so a stray control char scraped into a title/plot/overview would sail
# straight through and make a strict reader (Kodi/Emby/Jellyfin) reject the
# WHOLE NFO as malformed. Strip them at the single escape chokepoint below.


def _xml_clean(s: str) -> str:
    """Drop characters illegal in XML 1.0 (§2.2 Char): keep TAB/LF/CR, the
    U+0020–U+D7FF and U+E000–U+FFFD ranges, and the astral planes; strip the
    rest (raw C0 controls, surrogates, U+FFFE/FFFF)."""
    return "".join(
        ch
        for ch in s
        if (o := ord(ch)) in (0x09, 0x0A, 0x0D)
        or 0x20 <= o <= 0xD7FF
        or 0xE000 <= o <= 0xFFFD
        or 0x10000 <= o <= 0x10FFFF
    )


def _esc(value: Any) -> str:
    """XML-escape a value after removing characters illegal in XML 1.0 text."""
    return escape(_xml_clean(str(value)))

# Optional NFO fields the user can toggle (Settings → Naming → Write .nfo files).
# Structural identity — title, year, season/episode, <uniqueid> — is ALWAYS
# written and intentionally not in this list. Keys must match the frontend's
# NFO_FIELDS and the `naming.nfo_fields` setting dict.
NFO_TOGGLEABLE = (
    "plot", "genres", "cast", "director", "studio", "runtime",
    "country", "originaltitle", "artwork", "seasonposters", "collection", "status", "showtitle",
    "streamdetails",
)


def _enabled(fields: set[str] | None, key: str) -> bool:
    """Whether an optional field should be written. ``fields=None`` means
    "everything on" (the default / unconfigured behaviour), so existing
    callers and back-compat are preserved."""
    return fields is None or key in fields


def _el(tag: str, value: Any) -> str:
    """One `<tag>value</tag>` line, or '' when value is empty/None."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    return f"  <{tag}>{_esc(s)}</{tag}>"


def _uniqueid(provider: str | None, provider_id: str | None) -> str:
    """Kodi `<uniqueid type=... default="true">` line for the match provider."""
    if not provider or not provider_id:
        return ""
    return f'  <uniqueid type="{_esc(provider)}" default="true">{_esc(provider_id)}</uniqueid>'


def _genre_lines(meta: dict[str, Any]) -> list[str]:
    return [f"  <genre>{_esc(g)}</genre>" for g in (meta.get("genres") or []) if g]


def _actor_lines(meta: dict[str, Any]) -> list[str]:
    return [f"  <actor><name>{_esc(a)}</name></actor>" for a in (meta.get("cast") or []) if a]


def _originaltitle(meta: dict[str, Any]) -> str:
    """`<originaltitle>` from the native/romaji title (mostly anime; absent for
    most live-action → omitted). Falls back to the first alt-title."""
    alts = meta.get("alt_titles") or []
    val = meta.get("title_native") or meta.get("title_romaji") or (alts[0] if alts else None)
    return _el("originaltitle", val)


def _status_line(meta: dict[str, Any]) -> str:
    """Kodi `<status>` (Continuing / Ended) from the provider's in_production flag."""
    ip = meta.get("in_production")
    if ip is None:
        return ""
    return _el("status", "Continuing" if ip else "Ended")


def _set_lines(meta: dict[str, Any]) -> list[str]:
    """Kodi movie `<set>` for franchise collections (#14 belongs_to_collection)."""
    name = meta.get("collection_name")
    if not name:
        return []
    return [f"  <set>\n    <name>{_esc(name)}</name>\n  </set>"]


def _art_lines(meta: dict[str, Any]) -> list[str]:
    """`<thumb>` poster + `<fanart><thumb>` backdrop. Kodi pulls these URLs when
    no local art file sits beside the video — complements the #13 artwork
    download (which writes poster.jpg/fanart.jpg) and covers the case it's off."""
    out: list[str] = []
    poster = meta.get("poster_url")
    if poster:
        out.append(f"  <thumb>{_esc(poster)}</thumb>")
    fanart = meta.get("fanart_url")
    if fanart:
        out.append(f"  <fanart>\n    <thumb>{_esc(fanart)}</thumb>\n  </fanart>")
    return out


def _season_thumb_lines(season_posters: dict[int, str] | None) -> list[str]:
    """Per-season poster `<thumb>`s for a `tvshow.nfo` — Kodi's mechanism for
    distinct season art (`<thumb aspect="poster" type="season" season="N">URL`).
    Each AniDB cour carries its OWN poster + ScudLee season, so a franchise
    unified into one show ships every season's real cover. Plex/Jellyfin/Emby
    read season art from the `Season NN/poster.jpg` FILE instead (also written);
    this covers the NFO-driven (Kodi) path. Gated under the `artwork` field with
    the rest of the NFO art."""
    if not season_posters:
        return []
    return [
        f'  <thumb aspect="poster" type="season" season="{s}">{_esc(url)}</thumb>'
        for s, url in sorted(season_posters.items())
        if url
    ]


# ── Kodi <fileinfo><streamdetails> from the file's tech data (MediaInfo /
#    filename-strip). Maps Kira's normalized tokens to Kodi's NFO conventions.
_QUALITY_WH = {
    "4320p": (7680, 4320), "2160p": (3840, 2160), "1440p": (2560, 1440),
    "1080p": (1920, 1080), "720p": (1280, 720), "576p": (720, 576), "480p": (854, 480),
}
_HDR_NFO = {"DV": "dolbyvision", "HDR10+": "hdr10plus", "HDR10": "hdr10", "HLG": "hlg"}
_CODEC_NFO = {"x265": "hevc", "x264": "h264", "AV1": "av1", "VP9": "vp9"}
_CHAN_COUNT = {"1.0": 1, "2.0": 2, "2.1": 3, "4.0": 4, "4.1": 5, "5.1": 6, "6.1": 7, "7.1": 8, "9.1": 10}


def _streamdetails_lines(tech: dict[str, Any] | None) -> list[str]:
    """Kodi `<fileinfo><streamdetails>` from the file's own container data
    (codec / resolution / HDR / audio codec + channels). Every field is
    optional — emits only what's known, and nothing at all when `tech` is empty
    (e.g. a row scanned before `parsing.read_mediainfo` was on and whose
    filename carried no quality tag). `tech` keys mirror ParsedFile:
    codec / quality / hdr / channels / audio (list) / duration (seconds)."""
    if not tech:
        return []

    def _line(indent: int, tag: str, value: Any) -> str:
        if value is None or str(value).strip() == "":
            return ""
        return f"{' ' * indent}<{tag}>{_esc(value)}</{tag}>"

    video: list[str] = []
    codec = _CODEC_NFO.get(tech.get("codec") or "")
    video.append(_line(6, "codec", codec))
    wh = _QUALITY_WH.get(tech.get("quality") or "")
    if wh:
        video.append(_line(6, "width", wh[0]))
        video.append(_line(6, "height", wh[1]))
    video.append(_line(6, "hdrtype", _HDR_NFO.get(tech.get("hdr") or "")))
    video.append(_line(6, "durationinseconds", tech.get("duration")))
    video = [v for v in video if v]

    # One <audio> per audio language (Kodi/Emby read these to flag a file's
    # audio tracks + languages). The FIRST track also carries the primary codec +
    # channels; the rest carry just their language. With no languages known we
    # still emit a single <audio> for the primary codec/channels (prior behavior).
    aud_list = tech.get("audio") or []
    primary_codec = aud_list[0] if isinstance(aud_list, list) and aud_list else None
    primary_channels = _CHAN_COUNT.get(tech.get("channels") or "")
    audio_langs = [str(x).strip() for x in (tech.get("audio_langs") or []) if str(x).strip()]
    sub_langs = [str(x).strip() for x in (tech.get("sub_langs") or []) if str(x).strip()]

    audio_blocks: list[list[str]] = []
    if audio_langs:
        for i, lang in enumerate(audio_langs):
            block = []
            if i == 0:
                block.append(_line(6, "codec", primary_codec))
                block.append(_line(6, "channels", primary_channels))
            block.append(_line(6, "language", lang))
            block = [b for b in block if b]
            if block:
                audio_blocks.append(block)
    else:
        block = [b for b in (_line(6, "codec", primary_codec),
                             _line(6, "channels", primary_channels)) if b]
        if block:
            audio_blocks.append(block)

    subtitle_lines = [s for s in (_line(6, "language", lang) for lang in sub_langs) if s]

    if not video and not audio_blocks and not subtitle_lines:
        return []
    out = ["  <fileinfo>", "    <streamdetails>"]
    if video:
        out += ["      <video>", *video, "      </video>"]
    for block in audio_blocks:
        out += ["      <audio>", *block, "      </audio>"]
    for sub in subtitle_lines:
        out += ["      <subtitle>", sub, "      </subtitle>"]
    out += ["    </streamdetails>", "  </fileinfo>"]
    return out


def _join(lines: list[str]) -> str:
    return "\n".join(line for line in lines if line)


def build_movie_nfo(title: str, year: int | None, meta: dict[str, Any],
                    provider: str | None = None, provider_id: str | None = None,
                    fields: set[str] | None = None, tech: dict[str, Any] | None = None) -> str:
    meta = meta or {}
    lines = [
        _HEADER, "<movie>",
        _el("title", title),
        _originaltitle(meta) if _enabled(fields, "originaltitle") else "",
        _el("year", year),
        _el("plot", meta.get("overview")) if _enabled(fields, "plot") else "",
        _el("runtime", meta.get("runtime")) if _enabled(fields, "runtime") else "",
        *(_genre_lines(meta) if _enabled(fields, "genres") else []),
        _el("director", meta.get("director")) if _enabled(fields, "director") else "",
        _el("studio", meta.get("studio")) if _enabled(fields, "studio") else "",
        _el("country", meta.get("original_country")) if _enabled(fields, "country") else "",
        *(_set_lines(meta) if _enabled(fields, "collection") else []),
        *(_actor_lines(meta) if _enabled(fields, "cast") else []),
        *(_art_lines(meta) if _enabled(fields, "artwork") else []),
        *(_streamdetails_lines(tech) if _enabled(fields, "streamdetails") else []),
        _uniqueid(provider, provider_id),
        "</movie>",
    ]
    return _join(lines) + "\n"


def build_episode_nfo(episode_title: str | None, season: int | None, episode: int | None,
                      meta: dict[str, Any], series_name: str | None = None,
                      fields: set[str] | None = None, tech: dict[str, Any] | None = None,
                      plot: str | None = None, aired: str | None = None) -> str:
    meta = meta or {}
    # `plot` / `aired` are the REAL per-episode synopsis + air date the rename
    # resolves from the provider's episode list (via the Fribb cross-ref for
    # AniDB anime, which carries no per-episode titles of its own). We used to
    # omit them deliberately because the only plot on hand was the SERIES
    # overview — stamping that on every episode was wrong and blocked the media
    # server from scraping the real one. With GENUINE per-episode data that
    # concern is gone, so we write them; both stay empty (→ the media server
    # scrapes) when the cross-ref can't resolve the episode. `plot` still honors
    # the field toggle; `<showtitle>` remains the unambiguous scraper anchor.
    lines = [
        _HEADER, "<episodedetails>",
        _el("title", episode_title),
        _el("showtitle", series_name) if _enabled(fields, "showtitle") else "",
        _el("season", season),
        _el("episode", episode),
        _el("plot", plot) if (plot and _enabled(fields, "plot")) else "",
        _el("aired", aired) if aired else "",
        _el("runtime", meta.get("runtime")) if _enabled(fields, "runtime") else "",
        *(_streamdetails_lines(tech) if _enabled(fields, "streamdetails") else []),
        "</episodedetails>",
    ]
    return _join(lines) + "\n"


def build_tvshow_nfo(series_title: str, year: int | None, meta: dict[str, Any],
                     provider: str | None = None, provider_id: str | None = None,
                     fields: set[str] | None = None,
                     season_posters: dict[int, str] | None = None) -> str:
    meta = meta or {}
    lines = [
        _HEADER, "<tvshow>",
        _el("title", series_title),
        _originaltitle(meta) if _enabled(fields, "originaltitle") else "",
        _el("year", year),
        _el("plot", meta.get("overview")) if _enabled(fields, "plot") else "",
        *(_genre_lines(meta) if _enabled(fields, "genres") else []),
        _el("studio", meta.get("studio") or meta.get("network")) if _enabled(fields, "studio") else "",
        _el("country", meta.get("original_country")) if _enabled(fields, "country") else "",
        _status_line(meta) if _enabled(fields, "status") else "",
        *(_actor_lines(meta) if _enabled(fields, "cast") else []),
        *(_art_lines(meta) if _enabled(fields, "artwork") else []),
        # Per-season posters (Kodi reads these from tvshow.nfo; file-based servers
        # use the Season NN/poster.jpg written by the artwork pass instead). Own
        # toggle so it's independent of the show poster/fanart `artwork` field.
        *(_season_thumb_lines(season_posters) if _enabled(fields, "seasonposters") else []),
        _uniqueid(provider, provider_id),
        "</tvshow>",
    ]
    return _join(lines) + "\n"


def series_root_for(target: Path) -> Path:
    """Best-effort show root for a `tvshow.nfo`. Episodes usually live at
    `…/Show (Year)/Season 01/file.mkv` — walk up past a `Season NN` folder.
    Falls back to the immediate parent when there's no season folder."""
    parent = target.parent
    if parent.name.lower().startswith("season") or parent.name.lower().startswith("specials"):
        return parent.parent
    return parent


def plan_nfo_writes(target: Path, media_type: str) -> dict[str, Path]:
    """Which NFO files to write for a renamed video, keyed by kind.

    movie → {'movie': <stem>.nfo}
    tv/anime episode → {'episode': <stem>.nfo, 'tvshow': <root>/tvshow.nfo}
    Anything else → {} (music etc. — no NFO).
    """
    if media_type == "movie":
        return {"movie": target.with_suffix(".nfo")}
    if media_type in ("tv", "anime"):
        return {
            "episode": target.with_suffix(".nfo"),
            "tvshow": series_root_for(target) / "tvshow.nfo",
        }
    return {}
