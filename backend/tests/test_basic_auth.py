"""Opt-in HTTP Basic auth on the API (audit: no-auth finding).

OFF by default (creds unset → open). When KIRA_AUTH_USER/PASS are set, every
API request needs valid Basic credentials; CORS preflight, the health probe,
and token-gated webhooks are exempt. Credentials compare in constant time.
"""
from __future__ import annotations

import base64

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

import kira.main as main


def _basic(user: str, pw: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


# ── the constant-time credential check (the security core) ───────────────────

def test_basic_auth_ok_accepts_correct():
    assert main._basic_auth_ok(_basic("admin", "s3cret"), "admin", "s3cret") is True


def test_basic_auth_ok_rejects_bad():
    assert main._basic_auth_ok(_basic("admin", "wrong"), "admin", "s3cret") is False
    assert main._basic_auth_ok(_basic("nope", "s3cret"), "admin", "s3cret") is False
    assert main._basic_auth_ok(None, "admin", "s3cret") is False
    assert main._basic_auth_ok("Bearer xyz", "admin", "s3cret") is False
    assert main._basic_auth_ok("Basic !!!notbase64!!!", "admin", "s3cret") is False
    assert main._basic_auth_ok("Basic " + base64.b64encode(b"noseparator").decode(),
                               "admin", "s3cret") is False


def test_auth_exempt():
    assert main._auth_exempt("OPTIONS", "/api/v1/files") is True       # CORS preflight
    assert main._auth_exempt("GET", "/api/v1/health") is True          # container probe
    assert main._auth_exempt("POST", "/api/v1/webhooks/sonarr") is True  # token-gated
    assert main._auth_exempt("GET", "/api/v1/files") is False


# ── middleware behavior (real helpers, tiny app, no DB/lifespan) ─────────────

def _app(user: str | None, pw: str | None) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def mw(request: Request, call_next):
        if user and pw and not main._auth_exempt(request.method, request.url.path):
            if not main._basic_auth_ok(request.headers.get("Authorization"), user, pw):
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Kira"'})
        return await call_next(request)

    @app.get("/api/v1/files")
    async def files():
        return {"ok": True}

    @app.get("/api/v1/health")
    async def health():
        return {"ok": True}

    return TestClient(app)


def test_off_by_default_is_open():
    c = _app(None, None)
    assert c.get("/api/v1/files").status_code == 200


def test_enabled_requires_credentials():
    c = _app("admin", "s3cret")
    r = c.get("/api/v1/files")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").startswith("Basic")
    assert c.get("/api/v1/files", headers={"Authorization": _basic("admin", "nope")}).status_code == 401
    assert c.get("/api/v1/files", headers={"Authorization": _basic("admin", "s3cret")}).status_code == 200


def test_health_and_preflight_exempt_when_enabled():
    c = _app("admin", "s3cret")
    assert c.get("/api/v1/health").status_code == 200                  # exempt, no creds
    assert c.options("/api/v1/files").status_code != 401               # preflight exempt
