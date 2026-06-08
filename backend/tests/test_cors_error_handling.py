"""CORS headers on error (500) responses.

Regression for the "Failed to fetch" masking: an unhandled exception produced a
500 that bypassed CORSMiddleware — Starlette decorates responses on a normal
RETURN, not when the inner app raises — so a cross-origin browser saw a response
with no Access-Control-Allow-Origin and reported a generic "Failed to fetch",
hiding the real error (this is what masked the Sonarr-test crash). `_catch_errors_mw`
(registered INSIDE CORS) now turns an unhandled error into a normal 500 Response
that flows back out through CORS and gets the headers.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from kira.main import app


# A route that always raises — exercises the catch-all error middleware on the
# REAL app (so the test guards the actual middleware order, not a replica).
@app.get("/api/v1/__cors_error_probe__")
async def _cors_error_probe():
    raise RuntimeError("intentional test error")


def test_unhandled_error_carries_cors_headers():
    client = TestClient(app, raise_server_exceptions=False)
    r = client.get(
        "/api/v1/__cors_error_probe__",
        headers={"Origin": "http://localhost:5173"},  # one of settings.cors_origins
    )
    assert r.status_code == 500
    # The crux: the error response must carry the CORS header so the SPA can READ
    # the 500 (and show a real message) instead of an opaque "Failed to fetch".
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert r.json() == {"detail": "Internal server error"}
