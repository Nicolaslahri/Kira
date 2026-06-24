"""Kira Packs API — install, preview, and manage community metadata packs.

A pack is a JSON document at a user-supplied URL describing a fan-edit show
(One Pace, etc.) + its episodes + optional subtitles. Bindings (the installed
list) live in the ``settings`` table under ``packs.bindings``; the pack content
itself is fetched + cached by ``kira.packs.loader``.

Endpoints:
  GET    /packs            — installed packs + summaries
  POST   /packs            — install a pack from a URL
  PUT    /packs/{key}      — toggle enabled / authority / scope / subtitles
  DELETE /packs/{key}      — uninstall (drops the binding + its cache)
  POST   /packs/validate   — dry-run a URL: preview + "would rescue N files"
  POST   /packs/{key}/refresh — force re-fetch
  POST   /packs/rescan     — apply enabled packs to current no_match files
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kira.database import get_session
from kira.models import MediaFile
from kira.packs import loader as _loader
from kira.packs import resolver as _resolver
from kira.packs.apply import _parsed_of, apply_packs_to_no_match
from kira.packs.schema import (
    Pack,
    PackBinding,
    PackValidationError,
    url_hash,
)

logger = logging.getLogger("kira.api.packs")

router = APIRouter(prefix="/packs", tags=["packs"])


# ── Request bodies ──────────────────────────────────────────────────────────
class PackAddBody(BaseModel):
    url: str
    authority: Literal["fallback", "override"] = "fallback"
    scope_paths: list[str] = Field(default_factory=list)
    subtitles: bool = True


class PackUpdateBody(BaseModel):
    enabled: bool | None = None
    authority: Literal["fallback", "override"] | None = None
    scope_paths: list[str] | None = None
    subtitles: bool | None = None


class PackValidateBody(BaseModel):
    url: str
    scope_paths: list[str] = Field(default_factory=list)


# ── Helpers ─────────────────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _first_error(e: Exception) -> str:
    """A clean one-line message from a pydantic ValidationError (or any error) —
    surfaces the validator's own text (e.g. the override⇒scope rule) to the UI."""
    if isinstance(e, ValidationError):
        errs = e.errors()
        if errs:
            return str(errs[0].get("msg", e))
    return str(e)


def _summary(binding: PackBinding, pack: Pack | None) -> dict[str, Any]:
    """JSON view of one installed pack for the settings UI."""
    out: dict[str, Any] = {
        "key": url_hash(binding.url),
        "url": binding.url,
        "id": binding.id,
        "name": binding.name or (pack.name if pack else binding.url),
        "enabled": binding.enabled,
        "authority": binding.authority,
        "subtitles": binding.subtitles,
        "scope_paths": binding.scope_paths,
        "last_fetched": binding.last_fetched,
        "last_error": binding.last_error,
        "resolved": pack is not None,
    }
    if pack is not None:
        seasons = sorted({e.season for e in pack.episodes})
        out.update({
            "title": pack.show.title,
            "media_type": pack.media_type,
            "poster_url": pack.show.poster_url,
            "year": pack.show.year,
            "episode_count": len(pack.episodes),
            "season_count": len(seasons),
            "sub_count": sum(len(e.subs) for e in pack.episodes),
        })
    return out


async def _no_match_files(session: AsyncSession) -> list[MediaFile]:
    return list((await session.scalars(
        select(MediaFile).where(MediaFile.status == "no_match")
    )).all())


async def _count_claims(
    session: AsyncSession, pack: Pack, binding: PackBinding,
) -> tuple[int, list[str]]:
    """How many current no_match files this pack would claim, + up to 5 sample
    filenames. The transparent "would rescue N files" preview."""
    import os

    n = 0
    samples: list[str] = []
    for mf in await _no_match_files(session):
        parsed = _parsed_of(mf)
        if parsed is None:
            continue
        if _resolver.gate(parsed, mf.file_path, pack, binding) and \
                _resolver.claim(parsed, mf.file_path, pack) is not None:
            n += 1
            if len(samples) < 5:
                samples.append(os.path.basename(mf.file_path))
    return n, samples


def _find_binding(bindings: list[PackBinding], key: str) -> PackBinding | None:
    for b in bindings:
        if url_hash(b.url) == key:
            return b
    return None


# ── Endpoints ───────────────────────────────────────────────────────────────
@router.get("", response_model=dict[str, Any])
async def list_packs(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    bindings = await _loader.load_bindings(session)
    summaries = []
    for b in bindings:
        pack = await _loader.get_pack(b)
        summaries.append(_summary(b, pack))
    return {"packs": summaries}


@router.post("/validate", response_model=dict[str, Any])
async def validate_pack(
    body: PackValidateBody, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Fetch + validate a URL WITHOUT installing it, and report how many of the
    user's current unmatched files it would rescue. Pure preview."""
    pack, err = await _loader.fetch_pack(body.url)
    if pack is None:
        return {"ok": False, "error": err}
    # Dry-run with a throwaway fallback binding (scope honoured, no override rule).
    preview_binding = PackBinding(
        url=body.url, id=pack.id, name=pack.name,
        authority="fallback", scope_paths=body.scope_paths,
    )
    claims, samples = await _count_claims(session, pack, preview_binding)
    summary = _summary(preview_binding, pack)
    summary.update({"ok": True, "would_rescue": claims, "sample_files": samples})
    return summary


@router.post("", response_model=dict[str, Any])
async def add_pack(
    body: PackAddBody, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    pack, err = await _loader.fetch_pack(body.url)
    if pack is None:
        raise HTTPException(status_code=422, detail=f"pack invalid: {err}")
    try:
        binding = PackBinding(
            url=body.url, id=pack.id, name=pack.name,
            authority=body.authority, subtitles=body.subtitles,
            scope_paths=body.scope_paths, last_fetched=_now_iso(),
        )
    except (PackValidationError, ValidationError) as e:
        # override-without-scope and the like (pydantic wraps the model validator).
        raise HTTPException(status_code=422, detail=_first_error(e)) from e

    bindings = await _loader.load_bindings(session)
    key = url_hash(body.url)
    bindings = [b for b in bindings if url_hash(b.url) != key]  # replace same-URL
    bindings.append(binding)
    await _loader.save_bindings(session, bindings)
    # Apply it to the existing no_match backlog right away, so the user doesn't
    # have to click "Re-run on unmatched files" after adding a pack.
    rescued = await apply_packs_to_no_match(session)
    if rescued:
        await session.commit()
    return {**_summary(binding, pack), "rescued": rescued}


@router.put("/{key}", response_model=dict[str, Any])
async def update_pack(
    key: str, body: PackUpdateBody, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    bindings = await _loader.load_bindings(session)
    current = _find_binding(bindings, key)
    if current is None:
        raise HTTPException(status_code=404, detail="pack not installed")
    data = current.model_dump()
    if body.enabled is not None:
        data["enabled"] = body.enabled
    if body.authority is not None:
        data["authority"] = body.authority
    if body.scope_paths is not None:
        data["scope_paths"] = body.scope_paths
    if body.subtitles is not None:
        data["subtitles"] = body.subtitles
    try:
        updated = PackBinding.model_validate(data)
    except (PackValidationError, ValidationError) as e:
        raise HTTPException(status_code=422, detail=_first_error(e)) from e
    bindings = [updated if url_hash(b.url) == key else b for b in bindings]
    await _loader.save_bindings(session, bindings)
    pack = await _loader.get_pack(updated)
    return _summary(updated, pack)


@router.delete("/{key}", response_model=dict[str, Any])
async def delete_pack(
    key: str, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    bindings = await _loader.load_bindings(session)
    current = _find_binding(bindings, key)
    if current is None:
        raise HTTPException(status_code=404, detail="pack not installed")
    remaining = [b for b in bindings if url_hash(b.url) != key]
    await _loader.save_bindings(session, remaining)
    _loader.evict(current.key)
    return {"ok": True, "removed": key}


@router.post("/{key}/refresh", response_model=dict[str, Any])
async def refresh_pack(
    key: str, session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    bindings = await _loader.load_bindings(session)
    current = _find_binding(bindings, key)
    if current is None:
        raise HTTPException(status_code=404, detail="pack not installed")
    pack = await _loader.get_pack(current, force=True)
    data = current.model_dump()
    data["last_fetched"] = _now_iso()
    data["last_error"] = None if pack is not None else "refresh failed (URL unreachable or invalid)"
    if pack is not None:
        data["name"] = pack.name
        data["id"] = pack.id
    updated = PackBinding.model_validate(data)
    bindings = [updated if url_hash(b.url) == key else b for b in bindings]
    await _loader.save_bindings(session, bindings)
    # A refresh may have pulled in new/changed episodes — re-apply to the backlog.
    rescued = await apply_packs_to_no_match(session)
    if rescued:
        await session.commit()
    return {**_summary(updated, pack), "rescued": rescued}


@router.post("/rescan", response_model=dict[str, Any])
async def rescan_packs(session: AsyncSession = Depends(get_session)) -> dict[str, Any]:
    """Apply the enabled packs to every file currently sitting in ``no_match`` —
    so installing a pack fixes the existing library without a full re-scan. A
    normal scan now does this automatically too (and adding/refreshing a pack
    applies it on the spot); this stays for an explicit on-demand re-run."""
    rescued = await apply_packs_to_no_match(session)
    if rescued:
        await session.commit()
    return {"ok": True, "rescued": rescued}
