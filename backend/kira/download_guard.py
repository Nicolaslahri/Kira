"""Guard against HTTP-200 error bodies being saved as real media sidecars (R6).

CDNs and APIs (OpenSubtitles, the TMDB/TVDB image hosts) sometimes answer a
200 OK with an HTML rate-limit/notice page or a JSON error envelope instead of
the file we asked for. Writing that body verbatim leaves a permanent corrupt
`.srt` or `.jpg` beside the user's video — and because the download path is
write-if-absent, it's never retried. Validate the body before it touches disk.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:  # httpx only needed for the type hint; keep the module light
    import httpx

_log = logging.getLogger("kira.download_guard")


async def fetch_capped(
    client: "httpx.AsyncClient",
    url: str,
    *,
    max_bytes: int,
    timeout: float = 30.0,
    headers: dict | None = None,
    guard: bool = True,
    follow_redirects: bool = False,
    revalidate_redirects: bool = False,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> tuple[bytes, str] | None:
    """GET `url`, STREAMING the body and aborting the moment it exceeds
    `max_bytes`, and (by default) validating the URL through the SSRF guard
    first. Returns ``(content, content_type)`` on a clean 200 within the cap,
    else ``None``. Never raises.

    Used for the downloads that pull bytes from URLs found in *external* API
    responses (subtitle files, artwork images): those URLs are attacker-
    influenceable, so they must pass the outbound-URL guard, and their bodies
    must be bounded so a malicious/oversized payload can't exhaust memory or
    fill the disk.

    `follow_redirects` is opt-in for providers whose download endpoint legitimately
    302s to a file host (SubSource, some CDNs). The INITIAL host is still SSRF-
    validated; following the provider's own redirect chain is an accepted
    tradeoff for those (same fail-open posture as the rest of the outbound guard).

    `on_progress(received_bytes, total_bytes_or_None)` fires per chunk — used by
    large dataset downloads (anime-offline-database) to narrate MB progress to
    the activity pill. Exceptions from the callback are treated as a failed
    fetch (same never-raises contract)."""
    import time as _time
    from kira.url_guard import is_safe_outbound_url

    # Loop only matters when `revalidate_redirects` is set — otherwise this runs
    # exactly once (identical to the old straight-through behaviour).
    current = url
    hops = 0
    # Total wall-clock budget across ALL redirect hops — without it a
    # 30s per-hop timeout could stack to ~150s over 5 hops. Cap the
    # whole fetch at 2x the per-hop timeout and shrink each hop's
    # timeout to whatever budget remains.
    _deadline = _time.monotonic() + max(float(timeout), 1.0) * 2.0
    while True:
        _remaining = _deadline - _time.monotonic()
        if _remaining <= 0:
            return None
        _hop_timeout = min(float(timeout), _remaining)
        if guard:
            ok, _ = is_safe_outbound_url(current)
            if not ok:
                return None
        try:
            # By default do NOT follow redirects: we validated THIS host, and a
            # redirect could bounce to an internal/metadata target the guard
            # never saw (SSRF-via-redirect). Providers that redirect to a file
            # host opt in via `follow_redirects`.
            #
            # `revalidate_redirects` is the stricter mode for the auth-exempt,
            # fully caller-controlled /img proxy: we follow redirects MANUALLY,
            # one hop at a time, so the SSRF guard above re-runs against EVERY
            # hop (httpx's own follow_redirects would jump straight to the
            # redirect target without the guard ever seeing it).
            _follow = follow_redirects and not revalidate_redirects
            async with client.stream("GET", current, timeout=_hop_timeout,
                                     headers=headers or {}, follow_redirects=_follow) as resp:
                if revalidate_redirects and resp.is_redirect:
                    loc = resp.headers.get("location")
                    hops += 1
                    if not loc or hops > 5:
                        _log.info("fetch_capped: %s → redirect refused (loc=%r, hops=%d)",
                                  current, loc, hops)
                        return None
                    current = str(resp.url.join(loc))  # re-validated at loop top
                    continue
                if resp.status_code != 200:
                    _log.info("fetch_capped: %s → HTTP %s (not 200)", current, resp.status_code)
                    return None
                ct = resp.headers.get("content-type", "")
                total = int(resp.headers.get("content-length") or 0) or None
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        _log.info("fetch_capped: %s exceeded %d bytes — rejected", current, max_bytes)
                        return None  # over cap → reject (unbounded-download guard)
                    if on_progress is not None:
                        on_progress(len(buf), total)
                return bytes(buf), ct
        except Exception as e:
            _log.info("fetch_capped: %s failed: %r", current, e)
            return None

# Magic-byte signatures for the image formats artwork hosts actually serve.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
)


def sniff_image(content: bytes) -> str | None:
    """Return the image format name if `content` opens with a known image
    signature, else None (so the caller can reject an HTML/JSON error page that
    was served with a 200). WEBP is matched on its RIFF/WEBP container."""
    if not content:
        return None
    for magic, name in _IMAGE_MAGIC:
        if content.startswith(magic):
            return name
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def looks_like_error_page(content: bytes, content_type: str = "") -> bool:
    """True when a 200-OK body is an HTML page or JSON envelope rather than the
    text payload we asked for — used to reject error responses before saving a
    subtitle. Conservative on purpose: real subtitle formats are preserved
    (SRT starts with a digit/BOM, ASS with ``[Script Info]``, VTT with
    ``WEBVTT``, MicroDVD with ``{1}{1}`` which is *not* valid JSON)."""
    ct = (content_type or "").lower()
    if "text/html" in ct or "application/json" in ct or "+json" in ct:
        return True
    if not content:
        return False
    head = content[:512]
    if head[:3] == b"\xef\xbb\xbf":  # strip UTF-8 BOM
        head = head[3:]
    head = head.lstrip()
    low = head[:256].lower()
    if low.startswith(b"<!doctype html") or low.startswith(b"<html") or b"<html" in low:
        return True
    # A body that parses as a JSON object OR array is never a subtitle. Both
    # shapes occur in the wild: OpenSubtitles/CDN error envelopes are sometimes
    # a bare object (`{"error": ...}`) and sometimes an array of them
    # (`[{"error": ...}]`). We only attempt json.loads when the body opens with
    # "{" or "[", and only reject when it actually parses to a dict/list — so
    # the real subtitle formats that also start with those bytes survive:
    # ASS "[Script Info]" and MicroDVD "{1}{1}" are *not* valid JSON, so
    # json.loads raises and they fall through untouched.
    if head[:1] in (b"{", b"["):
        try:
            parsed = json.loads(content.decode("utf-8", "ignore"))
        except Exception:
            return False
        return isinstance(parsed, (dict, list))
    return False
