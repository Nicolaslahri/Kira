"""Auth endpoints + the server account backing the login page.

Kira requires an account by default: the FIRST boot has no account, so the
SPA shows a sign-up screen that calls /auth/setup; every boot after that the
login page checks credentials against the stored account. Transport stays
HTTP Basic (the middleware in main.py), so there are no sessions or tokens —
the SPA holds the header per-tab.

Credential resolution order (mirrored by the middleware):
  1. Env override (KIRA_AUTH_USER + KIRA_AUTH_PASS) — ops-managed, wins
     outright; the DB account is ignored and /auth/setup refuses.
  2. DB account (settings rows `auth.user` + `auth.password_hash`) — created
     once via /auth/setup, password stored as salted PBKDF2-SHA256. The
     `password` substring in the key name puts the hash behind GET /settings'
     secret masking automatically.
  3. Neither → open (pre-setup window; the SPA forces sign-up before use).

Endpoints:
  /auth/status — auth-EXEMPT. {required, setup}: `setup` means "no account
                 and no env creds — show the sign-up screen".
  /auth/setup  — creates the account. Only valid in the pre-setup window
                 (which is also when the middleware is open), so it needs no
                 exemption; afterwards it 409s.
  /auth/check  — NOT exempt. Reaching the handler means the middleware
                 accepted the Authorization header — the login form's probe.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kira.config import settings
from kira.database import get_session
from kira.models import Setting

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Password hashing (stdlib only — no new dependency) ───────────────

_PBKDF2_ITERATIONS = 240_000


def hash_password(password: str) -> str:
    """Salted PBKDF2-SHA256, self-describing format:
    ``pbkdf2$<iterations>$<salt_b64>$<hash_b64>``."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return "pbkdf2${}${}${}".format(
        _PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification against a `hash_password` string. False on
    any malformed input — never raises."""
    try:
        scheme, iters_s, salt_b64, hash_b64 = stored.split("$")
        if scheme != "pbkdf2":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters_s))
        return secrets.compare_digest(dk, expected)
    except Exception:
        return False


# ── DB account, cached for the per-request middleware path ───────────

_UNSET = object()
_account_cache: object = _UNSET  # _UNSET | None | (username, password_hash)


def _setting_str(row: Setting | None) -> str | None:
    v = row.value if row else None
    if isinstance(v, dict) and "value" in v:
        v = v["value"]
    return v if isinstance(v, str) and v.strip() else None


async def get_db_account() -> tuple[str, str] | None:
    """(username, password_hash) or None. Cached after the first read so the
    auth middleware costs a tuple check per request, not a DB hit. Refreshed
    by `set_account_cache` when /auth/setup creates the account."""
    global _account_cache
    if _account_cache is _UNSET:
        try:
            from kira.database import SessionLocal
            async with SessionLocal() as s:
                u = _setting_str(await s.get(Setting, "auth.user"))
                h = _setting_str(await s.get(Setting, "auth.password_hash"))
            _account_cache = (u, h) if u and h else None
        except Exception as e:
            # Don't cache a transient failure (e.g. DB mid-migration at boot).
            logger.warning("auth: account lookup failed (treating as unset): %r", e)
            return None
    return _account_cache  # type: ignore[return-value]


def set_account_cache(value: tuple[str, str] | None) -> None:
    global _account_cache
    _account_cache = value


def db_auth_ok(authorization: str | None, username: str, password_hash: str) -> bool:
    """Validate an HTTP Basic header against the stored account. Username is
    compared constant-time; the password runs through PBKDF2 verification."""
    if not authorization or not authorization.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(authorization[6:], validate=True).decode("utf-8")
    except Exception:
        return False
    u, sep, p = decoded.partition(":")
    if not sep:
        return False
    ok_user = secrets.compare_digest(u, username)
    ok_pass = verify_password(p, password_hash)
    return ok_user and ok_pass


# ── Endpoints ─────────────────────────────────────────────────────────

def _env_auth_on() -> bool:
    return bool(settings.auth_user and settings.auth_pass)


async def _server_onboarded(session: AsyncSession) -> bool:
    """Onboarding is a SERVER fact, not a per-browser one: completed when the
    flow set `onboarding.completed`, or — legacy adoption — when the library
    already has files (instances configured before the flag existed must not
    be re-onboarded)."""
    row = await session.get(Setting, "onboarding.completed")
    v = row.value if row else None
    if isinstance(v, dict) and "value" in v:
        v = v["value"]
    if v is True:
        return True
    from sqlalchemy import select as sa_select

    from kira.models import MediaFile
    first = await session.execute(sa_select(MediaFile.id).limit(1))
    return first.first() is not None


@router.get("/status")
async def auth_status(session: AsyncSession = Depends(get_session)) -> dict:
    onboarded = await _server_onboarded(session)
    if _env_auth_on():
        return {"required": True, "setup": False, "onboarded": onboarded}
    acct = await get_db_account()
    return {"required": acct is not None, "setup": acct is None, "onboarded": onboarded}


class SetupBody(BaseModel):
    username: str
    password: str


@router.post("/setup")
async def auth_setup(body: SetupBody, session: AsyncSession = Depends(get_session)) -> dict:
    if _env_auth_on():
        raise HTTPException(
            status_code=409,
            detail="Credentials are managed via environment variables on this server.",
        )
    if await get_db_account() is not None:
        raise HTTPException(status_code=409, detail="An account already exists.")
    username = body.username.strip()
    if not username:
        raise HTTPException(status_code=422, detail="Username can't be empty.")
    if len(body.password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters.")

    pw_hash = hash_password(body.password)
    for key, value in (("auth.user", username), ("auth.password_hash", pw_hash)):
        existing = await session.get(Setting, key)
        if existing is None:
            session.add(Setting(key=key, value=value))
        else:
            existing.value = value
    await session.commit()
    set_account_cache((username, pw_hash))
    logger.info("auth: account created for %r — sign-in now required", username)
    return {"ok": True}


@router.get("/check")
async def auth_check() -> dict:
    return {"ok": True}


# ── Popular-poster padding for the login rails ───────────────────────
# A small library yields too few distinct covers for five marquee rows, so
# the backdrop tops up with TMDB "popular" art (movies, TV, and JP-animation
# for the anime flavor). Fetched lazily, cached 6h in-process — one TMDB
# round per cache window, not per login.
import time as _time

_POPULAR_TTL = 6 * 3600
_popular_cache: tuple[float, dict[str, list[str]]] | None = None


async def _tmdb_key(session: AsyncSession) -> str | None:
    key = _setting_str(await session.get(Setting, "providers.tmdb.api_key"))
    return key or (settings.tmdb_api_key or None)


async def _fetch_popular(session: AsyncSession) -> dict[str, list[str]]:
    global _popular_cache
    now = _time.monotonic()
    if _popular_cache is not None and now - _popular_cache[0] < _POPULAR_TTL:
        return _popular_cache[1]
    key = await _tmdb_key(session)
    out: dict[str, list[str]] = {"movies": [], "anime": [], "tv": []}
    if not key:
        return out
    from kira import net
    base = "https://api.themoviedb.org/3"
    # Classic v3 keys go in the query string; v4 read tokens (JWTs) go in a
    # Bearer header — support both so whichever the user pasted works.
    params: dict[str, str] = {}
    headers: dict[str, str] = {"Accept": "application/json"}
    if key.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {key}"
    else:
        params["api_key"] = key
    targets = {
        "movies": ("/movie/popular", {}),
        "tv": ("/tv/popular", {}),
        "anime": ("/discover/tv", {"with_genres": "16", "with_origin_country": "JP",
                                   "sort_by": "popularity.desc"}),
    }
    try:
        client = net.shared_client()
        for kind, (path, extra) in targets.items():
            urls: list[str] = []
            for page in ("1", "2"):
                r = await client.get(f"{base}{path}",
                                     params={**params, **extra, "page": page},
                                     headers=headers, timeout=10.0)
                r.raise_for_status()
                for item in (r.json().get("results") or []):
                    pp = item.get("poster_path")
                    if pp:
                        urls.append(f"https://image.tmdb.org/t/p/w500{pp}")
            out[kind] = urls
        _popular_cache = (now, out)
    except Exception as e:
        logger.warning("auth: popular-poster fetch failed (non-fatal): %r", e)
    return out


@router.get("/backdrop")
async def auth_backdrop(session: AsyncSession = Depends(get_session)) -> dict:
    """Poster URLs for the login page's animated 3D rails, grouped by media
    type: the user's OWN library art first, topped up with TMDB popular
    titles for variety, randomly sampled per request so every visit looks
    different.

    Auth-EXEMPT by design (the login page renders before credentials exist),
    which means poster artwork is visible pre-auth. Accepted trade-off for a
    self-hosted app: cosmetic, no filenames or paths."""
    import random

    from sqlalchemy import func, select as sa_select

    from kira.models import Match, MediaFile

    out: dict[str, list[str]] = {"movies": [], "anime": [], "tv": []}
    type_key = {"movie": "movies", "anime": "anime", "tv": "tv"}
    try:
        for mt, key in type_key.items():
            rows = await session.execute(
                sa_select(Match.poster_url)
                .join(MediaFile, Match.media_file_id == MediaFile.id)
                .where(
                    Match.is_selected.is_(True),
                    Match.poster_url.is_not(None),
                    MediaFile.media_type == mt,
                )
                .distinct()
                .order_by(func.random())
                .limit(40)
            )
            out[key] = [r[0] for r in rows if r[0]]
    except Exception as e:
        logger.warning("auth: backdrop sample failed (non-fatal): %r", e)
    # Top up each flavor to ~40 distinct posters with popular art.
    try:
        popular = await _fetch_popular(session)
        for key in out:
            have = set(out[key])
            extras = [u for u in popular.get(key, []) if u not in have]
            random.shuffle(extras)
            out[key].extend(extras[: max(0, 40 - len(out[key]))])
    except Exception as e:
        logger.warning("auth: popular top-up failed (non-fatal): %r", e)
    return out
