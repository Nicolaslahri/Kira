"""Regression: POST /rename must route to `rename`, not a sibling helper.

A helper (`_resolve_franchise_absolute`) was once defined BETWEEN the
`@router.post` decorator and `async def rename`, so the decorator bound the
route to the helper instead. FastAPI then treated the helper's
`(anidb, selected, parsed)` params as REQUIRED QUERY params and every rename
422'd with "query.anidb: Field required …". The perform_rename-direct tests
missed it (they bypass the HTTP route), so this guards the binding itself.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from kira.main import app


def test_post_rename_binds_to_rename_endpoint() -> None:
    routes = [
        r for r in app.routes
        if getattr(r, "path", "") == "/api/v1/rename"
        and "POST" in getattr(r, "methods", set())
    ]
    assert routes, "POST /api/v1/rename route is missing"
    assert routes[0].endpoint.__name__ == "rename"


def test_post_rename_accepts_body_no_required_query_params() -> None:
    # An empty batch is a VALID request → 200 with an empty result, never a 422
    # about required query params (the regression's signature).
    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/api/v1/rename", json={"file_ids": [], "profile": "Plex", "op": "copy"})
    assert r.status_code == 200, r.text
    assert r.json() == {"succeeded": 0, "failed": 0, "items": []}
