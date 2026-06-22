"""The pure pack-matching logic: does a pack *claim* this file, and as which
episode? No DB, no network, no side effects — trivially unit-testable.

Two steps:
  • ``gate(parsed, file_path, pack, binding)`` — is this file even in scope for
    the pack? (optional folder scope + the show signature). A pack must clear
    the gate before any episode is considered, so it can never wander onto a
    neighbouring title.
  • ``claim(parsed, file_path, pack)`` — which episode is it? Precedence ladder,
    strongest signal first: crc32 → regex → release substring → arc+number →
    bare episode/absolute number.
"""
from __future__ import annotations

import re

from kira.packs.schema import Pack, PackBinding, PackEpisode, compile_safe, safe_search

# The [ABCD1234] CRC token anime fansubs stamp into filenames.
_CRC_RE = re.compile(r"[\[(]([0-9A-Fa-f]{8})[\])]")


def _norm(s: str | None) -> str:
    """Lowercase + collapse separators/space so 'One.Pace' == 'one pace'."""
    if not s:
        return ""
    return re.sub(r"[\s._\-]+", " ", s).strip().lower()


def _norm_path(p: str | None) -> str:
    return (p or "").replace("\\", "/").lower()


def _filename(parsed, file_path: str | None) -> str:
    return getattr(parsed, "original_filename", None) or (file_path or "")


def _crc_of(filename: str) -> str | None:
    m = _CRC_RE.search(filename or "")
    return m.group(1).lower() if m else None


def in_scope(file_path: str | None, scope_paths: list[str]) -> bool:
    """True when ``scope_paths`` is empty (whole library) OR the file lives under
    one of the listed folders. Separator/case-normalized; the Z:↔UNC alias is
    out of scope here (the user enters paths that match their own library)."""
    paths = [p for p in (scope_paths or []) if p and p.strip()]
    if not paths:
        return True
    fp = _norm_path(file_path)
    if not fp:
        return False
    for root in paths:
        r = _norm_path(root).rstrip("/")
        if r and (fp == r or fp.startswith(r + "/")):
            return True
    return False


def gate(parsed, file_path: str | None, pack: Pack, binding: PackBinding | None = None) -> bool:
    """Is this file in scope for ``pack``? Folder scope (from the binding) AND at
    least one show-signature signal must match."""
    if binding is not None and not in_scope(file_path, binding.scope_paths):
        return False

    fname = _filename(parsed, file_path)
    nfile = _norm(fname)
    ntitle = _norm(getattr(parsed, "title", None))
    ngroup = _norm(getattr(parsed, "release_group", None))

    # Title / alias signal — a pack title/alias appears in the parsed title or
    # filename. Substring is intentionally one-directional (term ⊂ file/title):
    # the reverse would let an alias that embeds the real show's name
    # ("One Piece (One Pace)") falsely claim a genuine One Piece file. A 3-char
    # floor keeps a stray 1-2 char title from matching everything.
    title_terms = [_norm(t) for t in (pack.match.titles or [])]
    title_terms += [_norm(pack.show.title)] + [_norm(a) for a in (pack.show.aliases or [])]
    for term in (t for t in title_terms if t and len(t) >= 3):
        if term in ntitle or term in nfile:
            return True

    # Release-group signal.
    for g in (pack.match.release_groups or []):
        if ngroup and _norm(g) == ngroup:
            return True

    # Filename regex signal.
    if pack.match.filename_regex:
        try:
            if safe_search(compile_safe(pack.match.filename_regex), fname):
                return True
        except Exception:
            return False
    return False


def claim(parsed, file_path: str | None, pack: Pack) -> PackEpisode | None:
    """Which episode of ``pack`` is this file? Assumes ``gate`` already passed.
    Returns the matching ``PackEpisode`` or None. Precedence ladder."""
    fname = _filename(parsed, file_path)
    eps = pack.episodes
    if not eps:
        return None

    # 1 — crc32 (mathematically absolute).
    crc = _crc_of(fname)
    if crc:
        for ep in eps:
            if ep.match.crc32 and ep.match.crc32 == crc:
                return ep

    # 2 — per-episode regex.
    for ep in eps:
        if ep.match.regex:
            try:
                if safe_search(compile_safe(ep.match.regex), fname):
                    return ep
            except Exception:
                pass

    # 3 — release substring.
    nfile = _norm(fname)
    for ep in eps:
        if ep.match.release and _norm(ep.match.release) in nfile:
            return ep

    # 4 — arc name + arc episode number.
    for ep in eps:
        if ep.match.arc and ep.match.arc_episode is not None:
            narc = _norm(ep.match.arc)
            if narc and narc in nfile and _has_number(fname, ep.match.arc_episode):
                return ep

    # 5 — bare episode / absolute number (weakest; gate already fixed the show).
    p_abs = getattr(parsed, "absolute_episode", None)
    p_ep = getattr(parsed, "episode", None)
    p_season = getattr(parsed, "season", None)
    for ep in eps:
        if ep.absolute is not None and p_abs is not None and ep.absolute == p_abs:
            return ep
    for ep in eps:
        if p_ep is not None and ep.episode == p_ep and (
            p_season is None or ep.season == p_season
        ):
            return ep
    return None


def _has_number(text: str, n: int) -> bool:
    """A standalone occurrence of ``n`` (zero-padding tolerated) in ``text``."""
    return re.search(rf"(?<!\d)0*{n}(?!\d)", text or "") is not None
