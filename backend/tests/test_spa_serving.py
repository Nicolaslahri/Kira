"""Single-image SPA serving (the Docker deploy).

When a built `frontend/dist` exists, `kira.main` serves the React shell + its
static files SAME-ORIGIN, with a 404-aware handler that:
  • serves index.html for client-side routes (deep-link / hard refresh), and
  • serves real built files (favicon.svg, …), but
  • leaves /api 404s as JSON (never masks an API error with the HTML shell).

Skipped when the frontend isn't built (CI without `vite build`), since the
handler only registers when `dist/index.html` is present.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kira.main import app, _SPA_INDEX, _FRONTEND_DIST

pytestmark = pytest.mark.skipif(
    not _SPA_INDEX.is_file(),
    reason="frontend not built (no dist/index.html) — SPA serving inactive",
)


def _client() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def test_root_serves_spa_shell() -> None:
    r = _client().get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_client_side_route_falls_back_to_shell() -> None:
    # A path with no backend route is a client-side route → serve the shell so a
    # hard refresh on /review doesn't 404.
    r = _client().get("/review")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_real_static_file_is_served() -> None:
    r = _client().get("/favicon.svg")
    assert r.status_code == 200
    assert "svg" in r.headers.get("content-type", "")


def test_api_404_stays_json_not_shell() -> None:
    # The SPA fallback must NOT swallow API 404s — they stay JSON so a missing
    # endpoint reads as an error, not a 200 HTML page.
    r = _client().get("/api/v1/this-endpoint-does-not-exist")
    assert r.status_code == 404
    assert "application/json" in r.headers.get("content-type", "")


# ── CR-15: path-confinement guard on the static-file fast path ───────────────
# The fallback's "serve a real built file" branch is gated by
# `_FRONTEND_DIST.resolve() in target.parents and target.is_file()`. A
# traversal path that resolves OUTSIDE dist must therefore FALL THROUGH to the
# index shell — never leak an out-of-tree file (e.g. backend source).
def _index_body() -> bytes:
    return _SPA_INDEX.read_bytes()


@pytest.mark.parametrize("traversal", [
    "/../../main.py",                 # backend/kira/main.py sits above dist
    "/..%2f..%2fmain.py",             # percent-encoded variant
    "/../../../backend/kira/main.py",
    "/%2e%2e/%2e%2e/main.py",         # encoded dot-dot
])
def test_traversal_path_serves_shell_not_out_of_tree_file(traversal: str) -> None:
    r = _client().get(traversal)
    assert r.status_code == 200
    # It's the SPA index shell, byte-for-byte — NOT the contents of main.py.
    assert r.content == _index_body()
    assert b"def fetch_subtitles" not in r.content
    assert b"FastAPI(" not in r.content


def test_traversal_target_resolves_outside_dist() -> None:
    # Sanity-anchor the guard's premise: the traversal target really does land
    # outside _FRONTEND_DIST (so the `in target.parents` check is what rejects
    # it), and the out-of-tree file we're guarding against actually exists.
    target = (_FRONTEND_DIST / "../../main.py").resolve()
    assert _FRONTEND_DIST.resolve() not in target.parents
