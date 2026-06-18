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
    # The login gate asks "is auth on?" BEFORE it has credentials.
    assert main._auth_exempt("GET", "/api/v1/auth/status") is True
    # Login-page poster rails render pre-auth too (cosmetic, art only).
    assert main._auth_exempt("GET", "/api/v1/auth/backdrop") is True
    # /auth/check verifies credentials, so it MUST go through the middleware.
    assert main._auth_exempt("GET", "/api/v1/auth/check") is False
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


# ── login-gate endpoints behind the real middleware ──────────────────────────

def _app_with_auth_routes(user: str | None, pw: str | None) -> TestClient:
    from kira.api.auth import router as auth_router
    app = FastAPI()

    @app.middleware("http")
    async def mw(request: Request, call_next):
        if user and pw and not main._auth_exempt(request.method, request.url.path):
            if not main._basic_auth_ok(request.headers.get("Authorization"), user, pw):
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Kira"'})
        return await call_next(request)

    app.include_router(auth_router, prefix="/api/v1")
    return TestClient(app)


def test_auth_check_verifies_credentials(monkeypatch):
    c = _app_with_auth_routes("admin", "s3cret")
    # No / wrong credentials → the middleware blocks /auth/check.
    assert c.get("/api/v1/auth/check").status_code == 401
    assert c.get("/api/v1/auth/check", headers={"Authorization": _basic("admin", "nope")}).status_code == 401
    # Correct credentials → ok. This is the login form's submit probe.
    r = c.get("/api/v1/auth/check", headers={"Authorization": _basic("admin", "s3cret")})
    assert r.status_code == 200 and r.json() == {"ok": True}
    # /auth/status answers WITHOUT credentials (exempt) and reports required.
    from kira.config import settings as cfg
    monkeypatch.setattr(cfg, "auth_user", "admin")
    monkeypatch.setattr(cfg, "auth_pass", "s3cret")
    r = c.get("/api/v1/auth/status")
    j = r.json()  # `onboarded` rides along but reflects DB state — ignore it here
    assert r.status_code == 200 and j["required"] is True and j["setup"] is False
    # Env off + no DB account → first-run setup window.
    from kira.api import auth as auth_mod
    monkeypatch.setattr(cfg, "auth_user", None)
    monkeypatch.setattr(cfg, "auth_pass", None)
    monkeypatch.setattr(auth_mod, "_account_cache", None)  # "no account"
    j2 = c.get("/api/v1/auth/status").json()
    assert j2["required"] is False and j2["setup"] is True


# ── First-run account (sign-up) ───────────────────────────────────────────────

def test_password_hash_roundtrip():
    from kira.api.auth import hash_password, verify_password
    stored = hash_password("hunter22")
    assert stored.startswith("pbkdf2$")
    assert verify_password("hunter22", stored) is True
    assert verify_password("hunter2", stored) is False
    assert verify_password("hunter22", "garbage") is False
    # Two hashes of the same password differ (fresh salt each time).
    assert hash_password("hunter22") != stored


def test_db_auth_ok_validates_header():
    from kira.api.auth import db_auth_ok, hash_password
    stored = hash_password("pw123456")
    assert db_auth_ok(_basic("nico", "pw123456"), "nico", stored) is True
    assert db_auth_ok(_basic("nico", "wrong"), "nico", stored) is False
    assert db_auth_ok(_basic("other", "pw123456"), "nico", stored) is False
    assert db_auth_ok(None, "nico", stored) is False
    assert db_auth_ok("Bearer x", "nico", stored) is False


def test_middleware_accepts_db_account(monkeypatch):
    """No env creds + a DB account → the real middleware logic must accept
    that account's Basic header and reject others."""
    from kira.api import auth as auth_mod
    from kira.api.auth import hash_password

    acct = ("nico", hash_password("pw123456"))
    app = FastAPI()

    @app.middleware("http")
    async def mw(request: Request, call_next):
        # Mirror main._basic_auth_mw's DB branch (env off).
        if not main._auth_exempt(request.method, request.url.path):
            if not auth_mod.db_auth_ok(request.headers.get("Authorization"), acct[0], acct[1]):
                return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Kira"'})
        return await call_next(request)

    @app.get("/api/v1/files")
    async def files():
        return {"ok": True}

    c = TestClient(app)
    assert c.get("/api/v1/files").status_code == 401
    assert c.get("/api/v1/files", headers={"Authorization": _basic("nico", "nope")}).status_code == 401
    assert c.get("/api/v1/files", headers={"Authorization": _basic("nico", "pw123456")}).status_code == 200


def test_setup_refused_when_env_managed(monkeypatch):
    from kira.api import auth as auth_mod
    from kira.config import settings as cfg
    monkeypatch.setattr(cfg, "auth_user", "admin")
    monkeypatch.setattr(cfg, "auth_pass", "s3cret")
    c = _app_with_auth_routes(None, None)  # middleware open; endpoint itself refuses
    r = c.post("/api/v1/auth/setup", json={"username": "x", "password": "y" * 8})
    assert r.status_code == 409
    monkeypatch.setattr(auth_mod, "_account_cache", ("nico", "hash"))
    monkeypatch.setattr(cfg, "auth_user", None)
    monkeypatch.setattr(cfg, "auth_pass", None)
    r = c.post("/api/v1/auth/setup", json={"username": "x", "password": "y" * 8})
    assert r.status_code == 409  # account already exists
    monkeypatch.setattr(auth_mod, "_account_cache", None)
    r = c.post("/api/v1/auth/setup", json={"username": "  ", "password": "y" * 8})
    assert r.status_code == 422  # empty username
    r = c.post("/api/v1/auth/setup", json={"username": "x", "password": "abc"})
    assert r.status_code == 422  # short password
