"""List endpoints must bound `limit` (audit: API layer).

`?limit=-1` is the dangerous one — SQLite treats ``LIMIT -1`` as *unlimited*, so
a single request would stream the entire table; huge positive values are a
memory-pressure DoS. FastAPI now validates the bound (ge=1, le=N) and 422s
before the query ever runs. We mount each real router in a bare app and override
the DB dependency, so validation is exercised without touching a database.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kira.api.files import router as files_router
from kira.api.history import router as history_router
from kira.api.system import notif_router
from kira.database import get_session


async def _fake_session():
    yield None  # never used: rejected requests 422 before the body runs


def _client(router) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = _fake_session
    # Let 5xx from the (deliberately broken) fake session surface as a status
    # code rather than an exception, so a valid limit can be asserted "not 422".
    return TestClient(app, raise_server_exceptions=False)


def test_files_limit_bounds():
    c = _client(files_router)
    assert c.get("/files?limit=-1").status_code == 422       # SQLite "unlimited" footgun
    assert c.get("/files?limit=0").status_code == 422
    assert c.get("/files?limit=100001").status_code == 422   # over the cap
    assert c.get("/files?limit=500").status_code != 422      # valid → accepted by the bound


def test_history_limit_bounds():
    c = _client(history_router)
    assert c.get("/history?limit=-1").status_code == 422
    assert c.get("/history?limit=100001").status_code == 422
    assert c.get("/history?limit=500").status_code != 422


def test_notifications_limit_bounds():
    c = _client(notif_router)
    assert c.get("/notifications?limit=-1").status_code == 422
    assert c.get("/notifications?limit=501").status_code == 422   # tighter cap (UI list)
    assert c.get("/notifications?limit=50").status_code != 422
