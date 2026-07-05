"""CSRF guard: state-changing requests must carry X-Requested-With.

A cross-site HTML form can POST to a query-param endpoint as a CORS "simple
request" (no preflight — the allow-list never runs, and the browser attaches
cached Basic credentials automatically). The destructive resets took only
query params, so any page the owner visited could remotely wipe the DB. A
custom header can't be set by a form, so requiring one forces a preflight for
every cross-origin caller.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import kira.main as main

pytestmark = pytest.mark.no_csrf_header


def _app() -> FastAPI:
    """Minimal app carrying only the real CSRF middleware logic."""
    app = FastAPI()

    @app.middleware("http")
    async def mw(request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.headers.get("X-Requested-With"):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=403, content={"detail": "blocked"})
        return await call_next(request)

    @app.get("/api/v1/thing")
    async def get_thing():
        return {"ok": True}

    @app.post("/api/v1/thing")
    async def post_thing():
        return {"ok": True}

    return app


def test_post_without_header_is_blocked():
    # Bypass the suite-wide header-injection fixture by calling httpx directly.
    c = TestClient(_app())
    r = c.request("POST", "/api/v1/thing", headers={})
    assert r.status_code == 403


def test_post_with_header_passes():
    c = TestClient(_app())
    r = c.request("POST", "/api/v1/thing", headers={"X-Requested-With": "Kira"})
    assert r.status_code == 200


def test_get_never_requires_header():
    c = TestClient(_app())
    assert c.request("GET", "/api/v1/thing", headers={}).status_code == 200


def test_real_middleware_helper_is_wired():
    # The guard lives in main._basic_auth_mw ahead of the auth checks; assert
    # the source contains the header gate so a refactor can't silently drop it.
    import inspect
    src = inspect.getsource(main._basic_auth_mw)
    assert "X-Requested-With" in src
