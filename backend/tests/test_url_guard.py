"""SSRF guard for user-configured outbound URLs (audit S5).

LAN-aware: private/loopback/public are allowed (real integrations live there);
cloud-metadata + special-use addresses + non-HTTP schemes are blocked.
"""
from __future__ import annotations

import socket

import pytest

from kira import url_guard
from kira.url_guard import is_safe_outbound_url, validate_outbound_url


@pytest.mark.parametrize("url", [
    "http://192.168.1.10:8989",                    # Sonarr on the LAN
    "http://10.0.0.5/api/v3",
    "http://172.16.0.9:8096",
    "http://sonarr:8989",                          # docker service name
    "http://localhost:8096",
    "http://127.0.0.1:8000",
    "https://discord.com/api/webhooks/123/abc",    # public webhook
    "https://hooks.example.com/path",
])
def test_allows_lan_and_public(url):
    ok, reason = is_safe_outbound_url(url)
    assert ok, f"{url} should be allowed, got: {reason}"


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data/",    # AWS/GCP/Azure metadata
    "http://metadata.google.internal/computeMetadata/",
    "http://100.100.100.200/",                      # Alibaba metadata
    "http://[fd00:ec2::254]/",                      # IMDSv2 IPv6
    "http://169.254.10.10/",                        # link-local
    "http://0.0.0.0/",                              # unspecified
    "http://224.0.0.1/",                            # multicast
])
def test_blocks_metadata_and_special_use(url):
    ok, _ = is_safe_outbound_url(url)
    assert not ok, f"{url} should be blocked"


@pytest.mark.parametrize("url", [
    "file:///etc/passwd",
    "gopher://127.0.0.1:6379/",
    "ftp://host/x",
    "://noscheme",
    "",
])
def test_blocks_non_http_schemes(url):
    assert not is_safe_outbound_url(url)[0]


def _fake_getaddrinfo(ip: str):
    """Build a getaddrinfo stub that resolves any host to `ip`."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    def _stub(host, port, *args, **kwargs):
        return [(family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))]
    return _stub


@pytest.mark.parametrize("ip", [
    "169.254.169.254",   # metadata
    "100.100.100.200",   # Alibaba metadata
    "fd00:ec2::254",     # IMDSv2 IPv6 metadata
    "169.254.10.10",     # link-local
    "0.0.0.0",           # unspecified
    "224.0.0.1",         # multicast
])
def test_blocks_hostname_resolving_to_blocked_ip(monkeypatch, ip):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _fake_getaddrinfo(ip))
    ok, _ = is_safe_outbound_url("http://metadata.attacker.example/")
    assert not ok, f"hostname resolving to {ip} should be blocked"


def test_allows_hostname_resolving_to_lan_ip(monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _fake_getaddrinfo("192.168.1.50"))
    ok, reason = is_safe_outbound_url("http://sonarr.lan/")
    assert ok, f"hostname resolving to LAN IP should be allowed, got: {reason}"


def test_dns_failure_falls_through_to_allow(monkeypatch):
    # Deliberate LAN-trust posture: a host that can't be resolved at guard time
    # (docker service name mid-startup, split-horizon DNS) is allowed rather
    # than breaking real integrations; see is_safe_outbound_url docstring.
    def _boom(*args, **kwargs):
        raise socket.gaierror("name or service not known")
    monkeypatch.setattr(url_guard.socket, "getaddrinfo", _boom)
    ok, _ = is_safe_outbound_url("http://does-not-resolve.invalid/")
    assert ok, "DNS failure should fall through to allow (non-crashing posture)"


def test_validate_raises_on_unsafe():
    with pytest.raises(ValueError):
        validate_outbound_url("http://169.254.169.254/")
    # A normal LAN URL must NOT raise.
    validate_outbound_url("http://192.168.1.5:8989")
