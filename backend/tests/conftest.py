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
