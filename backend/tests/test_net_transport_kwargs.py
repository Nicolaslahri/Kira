"""IPv4-forcing client shim must honour the caller's transport kwargs (R5).

Injecting our own `transport` makes httpx ignore the client-level
limits/http2/verify/cert/trust_env/proxy kwargs (they only feed the default
transport). The shim must therefore CONSUME those kwargs and forward them into
the transport it builds — otherwise a caller's `verify=False`, custom pool
`limits`, or `http2` choice is silently dropped.
"""
from __future__ import annotations

import httpx
import pytest

import kira.net as net


@pytest.fixture
def capture(monkeypatch):
    """Capture transport-construction kwargs + what reaches the real client
    init, without building a real httpx client."""
    transport_kwargs: dict = {}
    client_kwargs: dict = {}

    class FakeTransport:
        def __init__(self, **kw):
            transport_kwargs.update(kw)

    def fake_orig(self, *a, **kw):
        client_kwargs.update(kw)

    monkeypatch.setattr(net.httpx, "AsyncHTTPTransport", FakeTransport)
    monkeypatch.setattr(net, "_orig_client_init", fake_orig)
    monkeypatch.setattr(net, "_force_ipv4", True)
    return transport_kwargs, client_kwargs


def test_caller_kwargs_forwarded_to_transport(capture):
    transport_kwargs, client_kwargs = capture
    custom_limits = httpx.Limits(max_connections=7)

    net._patched_client_init(object(), limits=custom_limits, http2=False,
                             verify=False, trust_env=False)

    assert transport_kwargs["limits"] is custom_limits   # caller's pool honored
    assert transport_kwargs["http2"] is False            # caller's http2 honored
    assert transport_kwargs["verify"] is False           # caller's verify honored
    assert transport_kwargs["trust_env"] is False        # caller's trust_env honored
    assert transport_kwargs["local_address"] == "0.0.0.0"  # IPv4 binding still forced
    # consumed (not also forwarded to the client, where they'd be ignored)
    for k in ("limits", "http2", "verify", "trust_env"):
        assert k not in client_kwargs
    assert client_kwargs["transport"] is not None        # transport injected


def test_defaults_when_caller_silent(capture):
    transport_kwargs, _ = capture
    net._patched_client_init(object())
    assert transport_kwargs["limits"] is net._LIMITS
    assert transport_kwargs["http2"] == net._H2_AVAILABLE
    # verify/cert/trust_env/proxy NOT forced when the caller didn't ask
    assert "verify" not in transport_kwargs
    assert "proxy" not in transport_kwargs


def test_explicit_transport_is_not_overridden(capture):
    transport_kwargs, client_kwargs = capture
    sentinel = object()
    net._patched_client_init(object(), transport=sentinel)
    assert client_kwargs["transport"] is sentinel        # caller's transport untouched
    assert transport_kwargs == {}                        # we never built one


def test_force_ipv4_off_skips_injection(capture, monkeypatch):
    transport_kwargs, client_kwargs = capture
    monkeypatch.setattr(net, "_force_ipv4", False)
    net._patched_client_init(object(), limits=httpx.Limits(max_connections=3))
    assert "transport" not in client_kwargs              # no injection when off
    assert client_kwargs["limits"].max_connections == 3  # caller kwargs pass straight through
