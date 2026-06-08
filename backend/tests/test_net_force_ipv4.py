"""Force-IPv4 resolver — pins AF_UNSPEC lookups to IPv4 when enabled.

Guards the fix for intermittent dual-stack failures (TMDB connects picking a
dead IPv6 address). We assert the family the wrapped getaddrinfo passes through,
without doing real DNS."""

from __future__ import annotations

import socket

from kira import net


def test_default_is_force_ipv4_on() -> None:
    # Default ON (env unset in the test env).
    assert net.force_ipv4_enabled() is True


def test_install_is_idempotent() -> None:
    net.install()
    first = socket.getaddrinfo
    net.install()
    assert socket.getaddrinfo is first  # no double-wrap


def test_unspecified_family_pinned_to_ipv4_when_on(monkeypatch) -> None:
    captured = {}

    def _spy(host, port, family=0, type=0, proto=0, flags=0):
        captured["family"] = family
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", port))]

    monkeypatch.setattr(net, "_orig_getaddrinfo", _spy)
    net.set_force_ipv4(True)
    net.install()
    try:
        socket.getaddrinfo("api.themoviedb.org", 443)  # family omitted → AF_UNSPEC
        assert captured["family"] == socket.AF_INET
    finally:
        net.set_force_ipv4(True)  # restore default


def test_explicit_ipv6_is_respected(monkeypatch) -> None:
    captured = {}

    def _spy(host, port, family=0, type=0, proto=0, flags=0):
        captured["family"] = family
        return []

    monkeypatch.setattr(net, "_orig_getaddrinfo", _spy)
    net.set_force_ipv4(True)
    net.install()
    try:
        # A deliberate AF_INET6 request must NOT be downgraded.
        socket.getaddrinfo("example.com", 443, socket.AF_INET6)
        assert captured["family"] == socket.AF_INET6
    finally:
        net.set_force_ipv4(True)


def test_disabled_passes_family_through(monkeypatch) -> None:
    captured = {}

    def _spy(host, port, family=0, type=0, proto=0, flags=0):
        captured["family"] = family
        return []

    monkeypatch.setattr(net, "_orig_getaddrinfo", _spy)
    net.set_force_ipv4(False)
    net.install()
    try:
        socket.getaddrinfo("example.com", 443)  # AF_UNSPEC
        assert captured["family"] == 0  # left as AF_UNSPEC when force-IPv4 off
    finally:
        net.set_force_ipv4(True)  # restore the default for other tests
