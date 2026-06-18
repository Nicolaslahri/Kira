"""Undo cleanup is alias-aware (the mapped-drive Z:\\ ↔ UNC case).

Reported: undoing a One Piece rename moved the videos back but left every
`-poster.jpg`, `.en.srt`, `.nfo` AND the `Season 23` folder behind. Root cause:
`paths.library_root` is `Z:\\` but the rename engine persists RESOLVED paths, so
`created_assets` (and the vacated folder) are the UNC spelling
`\\\\192.168.0.63\\Data\\...`. `path_under_roots` is purely lexical → it rejected
every recorded asset ("outside a managed root"), and the folder walk found no
containing root, so nothing was cleaned.

`_managed_roots_aliased` resolves each root once and adds the differing spelling
so the containment checks match either form. (`Path("Z:\\").resolve()` actually
returning the UNC target is host behavior, verified separately; here we patch
that one call so the test is OS-independent — no symlink privilege needed.)
"""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from kira import database as db
from kira.api.files import _managed_roots, _managed_roots_aliased
from kira.api.history import _cleanup_entry_assets
from kira.models import RenameHistory, Setting


async def _fresh_db(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'undo_alias.db'}")
    sm = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(db, "engine", eng)
    monkeypatch.setattr(db, "SessionLocal", sm)
    await db.init_db()
    return sm


async def test_managed_roots_aliased_expands_via_resolve(tmp_path, monkeypatch):
    # Patch the resolve() that _managed_roots_aliased uses so a mapped-drive root
    # resolves to its UNC target — mirrors the host's Path("Z:\\").resolve() ->
    # "\\\\192.168.0.63\\Data". `_managed_roots` reads Settings (no Path); init_db
    # lives in another module — so this only steers the alias expansion.
    from kira.api import files as files_mod
    mapping = {"Z:\\library": "\\\\nas\\share\\library", "Z:\\media": "\\\\nas\\share\\media"}

    class _Resolved(str):
        pass

    class _FakePath:
        def __init__(self, p):
            self._p = str(p)

        def resolve(self):
            return _Resolved(mapping.get(self._p, self._p))

    monkeypatch.setattr(files_mod, "Path", _FakePath)
    sm = await _fresh_db(tmp_path, monkeypatch)
    async with sm() as s:
        s.add(Setting(key="paths.library_root", value="Z:\\library"))
        s.add(Setting(key="paths.watch_folders", value=["Z:\\media"]))
        await s.commit()
        raw = await _managed_roots(s)
        aliased = await _managed_roots_aliased(s)

    assert set(raw) == {"Z:\\library", "Z:\\media"}            # raw = drive-letter forms only
    # alias PRESERVES the drive-letter forms and APPENDS the resolved UNC forms
    assert "Z:\\library" in aliased and "Z:\\media" in aliased
    assert "\\\\nas\\share\\library" in aliased and "\\\\nas\\share\\media" in aliased


async def test_aliased_roots_flip_skip_to_delete(tmp_path):
    # A recorded asset persisted under the RESOLVED spelling. With only the
    # drive-letter root (pre-fix) the lexical guard rejects it → orphaned. Once
    # the resolved spelling is in the root set (what _managed_roots_aliased adds)
    # it is deleted. `real` stands in for the UNC path; `decoy` for the Z:\ root.
    real = tmp_path / "real"
    real.mkdir()
    decoy = str(tmp_path / "ZMAP")                 # configured root, NOT real's parent
    nfo = real / "One Piece - S23E01 - Episode 01.nfo"
    poster = real / "One Piece - S23E01 - Episode 01-poster.jpg"
    srt = real / "One Piece - S23E01 - Episode 01.en.srt"
    for f in (nfo, poster, srt):
        f.write_bytes(b"x")
    entry = RenameHistory(
        old_path="old", new_path=str(real / "One Piece - S23E01 - Episode 01.mkv"),
        operation="move", created_assets=[str(nfo), str(poster), str(srt)],
    )
    # pre-fix: only the drive-letter spelling → every asset skipped
    assert await _cleanup_entry_assets(entry, [decoy]) == 0
    assert nfo.exists() and poster.exists() and srt.exists()
    # post-fix: resolved spelling also present → NFO + poster + auto-fetched .srt deleted
    assert await _cleanup_entry_assets(entry, [decoy, str(real)]) == 3
    assert not nfo.exists() and not poster.exists() and not srt.exists()
