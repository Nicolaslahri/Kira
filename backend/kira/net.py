"""Force IPv4 for all outbound connections (default on).

Why: dual-stack hosts (TMDB, GitHub, Discord, OpenSubtitles, …) publish both
IPv4 and IPv6 addresses. On a network with broken / half-configured IPv6 —
extremely common on home routers and Windows — connects that pick the IPv6
address hang until they time out, producing intermittent `ConnectError('')` /
connect timeouts. The classic symptom: "TMDB test failed" → "verified 389 ms" →
failed again, depending on which address family that attempt happened to pick.
For one user this measured 0/8 IPv6 connects vs 8/8 IPv4, and it dragged
matching to ~5% of normal because every IPv6 attempt ate the retry budget.

Public metadata APIs all have solid IPv4 and there's no benefit to attempting
IPv6 here. Rather than thread an IPv4-bound transport through ~two dozen
`httpx.AsyncClient(...)` call sites (and miss provider-internal ones), we patch
ONE thing: `socket.getaddrinfo`. When force-IPv4 is on, an unspecified-family
(`AF_UNSPEC`) lookup is pinned to `AF_INET`, so resolution returns only IPv4
addresses and no connect — from any httpx client or asyncio socket — ever tries
IPv6. Explicit `AF_INET6` lookups are left alone.

Toggle off for the rare IPv6-only deployment via `KIRA_FORCE_IPV4=0` or the
`network.force_ipv4` setting.
"""
from __future__ import annotations

import importlib.util
import os
import socket

import httpx

_FALSY = {"0", "false", "no", "off"}

# HTTP/2 multiplexes every request over ONE connection → ONE TLS handshake total,
# instead of one per request. Only usable if the `h2` package is installed
# (`pip install httpx[http2]`); detected once.
_H2_AVAILABLE = importlib.util.find_spec("h2") is not None

# Keep connections WARM. The fix for the TLS-reset problem (security software /
# middleboxes RST'ing handshakes to dual-stack CDNs) is to re-handshake as
# rarely as possible: establish once, then reuse. A long keepalive_expiry means
# a whole scan rides a single established connection instead of re-handshaking
# per file. `retries=1` lets the transport itself shrug off a single connect
# blip before our higher-level retry even sees it.
_LIMITS = httpx.Limits(max_keepalive_connections=20, max_connections=40, keepalive_expiry=300.0)

# Default ON — IPv4 is universal for these APIs and this sidesteps the common
# broken-IPv6 trap. Opt out explicitly via env or the settings toggle.
_force_ipv4: bool = os.environ.get("KIRA_FORCE_IPV4", "").strip().lower() not in _FALSY

_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv4(host, port, family=0, type=0, proto=0, flags=0):  # noqa: A002 - stdlib signature
    # AF_UNSPEC (0) = "caller didn't care" → PREFER IPv4 when forcing. An
    # explicit AF_INET6 request is honoured (we never block deliberate IPv6).
    #
    # Prefer-not-pin: we try IPv4 first, but if the host has no A record we
    # fall back to the unrestricted lookup rather than making it unresolvable.
    # This keeps the common broken-dual-stack-IPv6 trap closed while not
    # silently denying IPv6 resolution to IPv6-only LAN hosts (Plex/Jellyfin/
    # Sonarr on an IPv6-only segment) or to any *other* library in the process
    # that relies on dual-stack getaddrinfo — the global patch must not break
    # them just because Kira prefers IPv4 for its own provider calls.
    if _force_ipv4 and family == 0:
        try:
            return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
        except socket.gaierror:
            pass  # no IPv4 address — fall through to the dual-stack lookup
    return _orig_getaddrinfo(host, port, family, type, proto, flags)


def _ipv4_transport_kwargs(kwargs: dict) -> dict:
    """Pull the client-level transport knobs out of `kwargs` and build the
    arg dict for an IPv4-bound AsyncHTTPTransport. Shared by the AsyncClient
    monkeypatch and the explicit `async_client()` factory so both produce an
    identical transport (single source of truth — DRY)."""
    t_kwargs: dict = {
        "local_address": "0.0.0.0",                  # bind IPv4 → can't dial dead IPv6
        "limits": kwargs.pop("limits", _LIMITS),     # warm pool unless caller overrides
        "http2": kwargs.pop("http2", _H2_AVAILABLE), # multiplex when h2 present
        "retries": kwargs.pop("retries", 1),         # transport-level connect retry
    }
    for key in ("verify", "cert", "trust_env", "proxy"):
        if key in kwargs:
            t_kwargs[key] = kwargs.pop(key)
    return t_kwargs


_orig_client_init = httpx.AsyncClient.__init__


def _patched_client_init(self, *args, **kwargs):
    # Bind every client's sockets to IPv4 (`local_address="0.0.0.0"`) when
    # forcing IPv4. This is the BULLETPROOF layer: an AF_INET socket physically
    # cannot connect to an IPv6 address, so it works regardless of how the
    # resolver behaves under uvicorn / httpcore / anyio (where the getaddrinfo
    # monkeypatch proved unreliable). A caller that passes its own `transport`
    # is respected (we don't override). One place → covers all ~24 client sites
    # plus provider-internal clients, with zero call-site changes.
    if _force_ipv4 and "transport" not in kwargs:
        try:
            # Build the transport from the CALLER's settings, not blind module
            # defaults. Passing an explicit `transport` makes httpx ignore the
            # client-level `limits`/`http2`/`verify`/`cert`/`trust_env`/`proxy`
            # kwargs (they only ever feed the *default* transport), so unless we
            # consume them here a client that asked for, say, `verify=False` or
            # custom `limits` would silently have it dropped. Pop → forward.
            kwargs["transport"] = httpx.AsyncHTTPTransport(**_ipv4_transport_kwargs(kwargs))
        except Exception:
            pass  # never let the IPv4 shim break client construction
    _orig_client_init(self, *args, **kwargs)


def async_client(**kwargs) -> httpx.AsyncClient:
    """Explicit, testable factory for an IPv4-preferring AsyncClient.

    Equivalent to ``httpx.AsyncClient(**kwargs)`` once `install()` has run, but
    the IPv4 binding is applied *here* in plain sight rather than via the global
    `__init__` monkeypatch — preferred for new code and for tests that want a
    client without depending on import-time global state. When force-IPv4 is
    off it returns a vanilla client."""
    if _force_ipv4 and "transport" not in kwargs:
        kwargs["transport"] = httpx.AsyncHTTPTransport(**_ipv4_transport_kwargs(kwargs))
    return httpx.AsyncClient(**kwargs)


_shared_client: httpx.AsyncClient | None = None


def shared_client() -> httpx.AsyncClient:
    """A process-lifetime AsyncClient whose connection pool stays warm across
    *separate* outbound operations (a scan, a rematch, a rename) instead of
    each `async with httpx.AsyncClient()` tearing its pool down on block exit.
    Lazily created; closed once at shutdown via `aclose_shared()` (wired into
    the app lifespan). Do NOT use it inside an `async with` — it is shared and
    must outlive any single caller."""
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = async_client()
    return _shared_client


async def aclose_shared() -> None:
    """Close the shared client at app shutdown. Safe if never created."""
    global _shared_client
    if _shared_client is not None and not _shared_client.is_closed:
        await _shared_client.aclose()
    _shared_client = None


def install() -> None:
    """Idempotently install the IPv4 forcing process-wide (resolver + socket
    binding). Safe to call repeatedly (no double-wrapping)."""
    if socket.getaddrinfo is not _getaddrinfo_ipv4:
        socket.getaddrinfo = _getaddrinfo_ipv4
    if httpx.AsyncClient.__init__ is not _patched_client_init:
        httpx.AsyncClient.__init__ = _patched_client_init


def set_force_ipv4(enabled: bool) -> None:
    """Flip the runtime flag — read once on boot from settings, and again on
    every settings change. The installed resolver consults this live, so no
    re-install is needed."""
    global _force_ipv4
    _force_ipv4 = bool(enabled)


def force_ipv4_enabled() -> bool:
    return _force_ipv4


# Install on import so the preference is active for every outbound connection,
# including provider clients created before the app's lifespan runs.
install()
