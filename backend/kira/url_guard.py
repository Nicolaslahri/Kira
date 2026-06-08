"""SSRF guard for user-configured outbound URLs (audit S5).

Tailored to a self-hosted LAN app: PRIVATE and loopback targets are ALLOWED on
purpose — Sonarr, Jellyfin, n8n, Apprise all live on the LAN (`192.168.x`,
`10.x`, `sonarr:8989`, `localhost`). What we block is the SSRF-relevant subset
that no legitimate integration uses: cloud-metadata endpoints, link-local /
multicast / unspecified addresses, and non-HTTP schemes. So a setting a hostile
caller managed to plant can't pivot Kira into reading instance metadata or
poking special-use addresses, while every real LAN/public webhook still works.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

# Cloud-provider metadata services (AWS/GCP/Azure share 169.254.169.254; GCP
# also resolves a hostname; Alibaba uses 100.100.100.200; the IMDSv2 IPv6 is
# fd00:ec2::254). 169.254.0.0/16 is also caught by the link-local rule below.
_METADATA_HOSTS = {"metadata.google.internal", "metadata.goog"}
_METADATA_IPS = {"169.254.169.254", "100.100.100.200", "fd00:ec2::254"}


def _blocked_ip_reason(ip: ipaddress._BaseAddress, host: str) -> str | None:
    """Return a human reason if `ip` is in the SSRF-blocked subset, else None.

    Mirrors the IP-literal checks: metadata endpoints and
    link-local/multicast/unspecified special-use addresses. Private/LAN/
    loopback are deliberately allowed (see module docstring)."""
    if str(ip) in _METADATA_IPS:
        return f"blocked cloud-metadata endpoint {host}"
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        return f"blocked special-use address {host}"
    return None


def is_safe_outbound_url(url: str) -> tuple[bool, str]:
    """Return (ok, reason). ok=False with a human reason when the URL is unsafe
    to fetch from. Allows private/loopback/public; blocks the SSRF subset."""
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return False, "malformed URL"
    if u.scheme not in ("http", "https"):
        return False, f"scheme {u.scheme or '(none)'!r} not allowed (http/https only)"
    host = (u.hostname or "").strip().lower()
    if not host:
        return False, "no host in URL"
    if host in _METADATA_HOSTS or host in _METADATA_IPS:
        return False, f"blocked cloud-metadata endpoint {host}"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None  # hostname (not a literal IP) — resolve it below
    if ip is not None:
        reason = _blocked_ip_reason(ip, host)
        if reason is not None:
            return False, reason
        return True, ""
    # Hostname: resolve and check EVERY address it maps to, so a name that
    # resolves to a metadata/special-use IP can't bypass the literal checks.
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        # DNS failure — fall through to ALLOW (non-crashing posture). This is a
        # deliberate tradeoff for a LAN-trust app: legitimate targets routinely
        # don't resolve at guard time but do at fetch time — a docker service
        # name (`sonarr:8989`) during a startup race, or a host behind a
        # split-horizon resolver. Failing closed here would break those real
        # integrations. The residual DNS-rebinding/TOCTOU vector (resolve-safe
        # at guard, resolve-blocked at fetch) can only reach the *blocked* set —
        # metadata/link-local/multicast/unspecified — because every private/LAN
        # address is allowed by design anyway; fully closing it needs
        # connect-to-validated-IP pinning across all egress, which is
        # disproportionate to that residual risk. See is_safe_outbound_url docs.
        return True, ""
    for info in infos:
        addr = info[4][0].split("%", 1)[0]  # strip any IPv6 scope id
        try:
            resolved = ipaddress.ip_address(addr)
        except ValueError:
            continue
        reason = _blocked_ip_reason(resolved, host)
        if reason is not None:
            return False, reason
    return True, ""


def validate_outbound_url(url: str) -> None:
    """Raise ValueError if `url` is unsafe to fetch from (see is_safe_outbound_url)."""
    ok, reason = is_safe_outbound_url(url)
    if not ok:
        raise ValueError(f"unsafe outbound URL ({reason})")
