"""Movie-collection completion — for each TMDB collection you PARTIALLY own,
surface the parts you're MISSING so the Review grid can render ghost covers with
a one-click "Get from Radarr".

Read-only + best-effort: if TMDB or its config is unavailable the endpoint just
returns no collections (the grid then shows movies ungrouped, as before). Movies-
only by nature — TMDB collections don't exist for TV.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.matcher.engine import registry_from_settings
from kira.models import Match
from kira.settings_store import get_raw, unwrap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collections", tags=["collections"])


def _as_bool(v: Any, default: bool = True) -> bool:
    """Coerce a DB setting value (bool / the wrapped string-toggle shape / absent)
    to a bool. Absent → default (the feature ships ON)."""
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "off", "")
    return bool(v)


@router.get("")
async def list_collections(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Missing parts per partially-owned TMDB movie collection.

    "Own" = a selected TMDB movie Match carrying that `collection_id`. Missing =
    the collection's parts minus the tmdb ids you own. Bounded by construction —
    only collections you already own ≥1 part of are ever queried, so a 5,000-movie
    library still only fetches the handful of collections it actually touches.
    """
    # Honor the Settings toggles. enabled=off → no bands/ghosts at all;
    # show_unreleased=off → hide upcoming films you can't grab yet.
    if not _as_bool(unwrap(await get_raw(session, "collections.enabled"))):
        return {"collections": []}
    show_unreleased = _as_bool(unwrap(await get_raw(session, "collections.show_unreleased")))

    rows = (await session.execute(
        select(Match.collection_id, Match.collection_name, Match.provider_id).where(
            Match.is_selected.is_(True),
            Match.match_type == "movie",
            Match.provider == "tmdb",
            Match.collection_id.is_not(None),
        )
    )).all()
    if not rows:
        return {"collections": []}

    # collection_id -> {name, owned tmdb ids}
    owned: dict[str, dict[str, Any]] = {}
    for coll_id, coll_name, prov_id in rows:
        if not coll_id:
            continue
        entry = owned.setdefault(coll_id, {"name": coll_name, "ids": set()})
        if prov_id:
            entry["ids"].add(str(prov_id))
        if coll_name and not entry["name"]:
            entry["name"] = coll_name

    today = date.today().isoformat()
    out: list[dict[str, Any]] = []
    async with httpx.AsyncClient() as client:
        registry = await registry_from_settings(client)
        if not registry.has("tmdb"):
            return {"collections": []}  # no TMDB key → no completion data
        tmdb = registry.build("tmdb")
        if not hasattr(tmdb, "get_collection"):
            return {"collections": []}
        for coll_id, info in owned.items():
            try:
                data = await tmdb.get_collection(coll_id)  # type: ignore[attr-defined]
            except Exception as e:  # noqa: BLE001 — one bad collection can't fail the rest
                logger.debug("collections: get_collection(%s) failed: %r", coll_id, e)
                continue
            parts = data.get("parts") or []
            if not parts:
                continue
            owned_ids = info["ids"]
            owned_in_coll = sum(1 for p in parts if p.get("tmdb_id") in owned_ids)
            missing: list[dict[str, Any]] = []
            for p in parts:
                tid = p.get("tmdb_id")
                if not tid or tid in owned_ids:
                    continue
                rel = p.get("release_date")
                # ISO date compares lexicographically — released = on/before today.
                released = bool(rel) and rel <= today
                if not released and not show_unreleased:
                    continue  # hide upcoming films per the setting
                missing.append({
                    "tmdb_id": tid,
                    "title": p.get("title"),
                    "year": p.get("year"),
                    "poster_url": p.get("poster_url"),
                    "released": released,
                })
            if not missing:
                continue  # you own the whole collection — nothing to surface
            out.append({
                "collection_id": coll_id,
                "name": data.get("name") or info["name"],
                "owned": owned_in_coll,
                "total": len(parts),
                "missing": missing,
            })
    return {"collections": out}
