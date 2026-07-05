"""Suite-wide fixtures."""
import pytest


@pytest.fixture(autouse=True)
def _no_db_account(monkeypatch):
    """Auth's DB-account cache must never leak the developer's REAL account
    into tests: most tests hit the live `kira.db` through the unpatched
    SessionLocal, and once a real account exists every API TestClient call
    would 401. Default every test to "no account" (middleware open, exactly
    the pre-auth behavior); tests that exercise the account path set the
    cache themselves on top of this."""
    from kira.api import auth as auth_mod
    monkeypatch.setattr(auth_mod, "_account_cache", None)


@pytest.fixture(autouse=True)
def _csrf_header(request, monkeypatch):
    """The CSRF guard rejects state-changing requests without X-Requested-With.
    Real clients (the SPA, the CLI) always send it; tests use bare TestClient
    calls, so inject the header suite-wide rather than editing every call site.
    Tests marked `no_csrf_header` (those exercising the guard itself) opt out."""
    if request.node.get_closest_marker("no_csrf_header"):
        return
    from starlette.testclient import TestClient

    orig_request = TestClient.request

    def _request(self, method, url, *args, headers=None, **kwargs):
        merged = {"X-Requested-With": "pytest"}
        if headers:
            merged.update(headers)
        return orig_request(self, method, url, *args, headers=merged, **kwargs)

    monkeypatch.setattr(TestClient, "request", _request)


def pytest_configure(config):
    config.addinivalue_line("markers", "no_csrf_header: skip suite-wide CSRF header injection")
