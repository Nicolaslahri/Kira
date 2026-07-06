"""Managed fpcalc (Chromaprint) — find it, or install a private copy with one click.

AcoustID acoustic fingerprinting (the last-resort matcher for UNTAGGED / badly
named music) needs Chromaprint's `fpcalc` binary. As with ffmpeg, "go install
chromaprint" is exactly the homework Kira removes, so this mirrors
`ffmpeg_setup.py` exactly:

  resolve_fpcalc()   PATH fpcalc, else Kira's own managed copy in ./tools/
  fpcalc_status()    {available, path, source, installable, installing} for the UI
  install_fpcalc()   download the official Chromaprint release (acoustid/chromaprint
                     GitHub), extract JUST the fpcalc binary into ./tools/, narrated
                     via the activity pill. No PATH edits, nothing global.

The download URL is a fixed constant (not user input), streamed to a temp file
under a hard size cap, and the archive member is matched by exact basename — a
hostile archive can't plant files outside ./tools/.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import tarfile
import zipfile
from pathlib import Path

from kira import activity

logger = logging.getLogger("kira.fpcalc_setup")

FPCALC_INSTALL_JOB = "fpcalc_install"


def _tools_dir() -> Path:
    return Path.cwd() / "tools"


_EXE = "fpcalc.exe" if os.name == "nt" else "fpcalc"

# Chromaprint has no "latest" download alias, so we pin a known-good release.
# fpcalc is a stable, rarely-changing tool — bump deliberately.
_VER = "1.5.1"
_REL = f"https://github.com/acoustid/chromaprint/releases/download/v{_VER}"
_DOWNLOADS: dict[tuple[str, str], str] = {
    ("Windows", "AMD64"):  f"{_REL}/chromaprint-fpcalc-{_VER}-windows-x86_64.zip",
    ("Linux", "x86_64"):   f"{_REL}/chromaprint-fpcalc-{_VER}-linux-x86_64.tar.gz",
    ("Darwin", "x86_64"):  f"{_REL}/chromaprint-fpcalc-{_VER}-macos-x86_64.tar.gz",
    ("Darwin", "arm64"):   f"{_REL}/chromaprint-fpcalc-{_VER}-macos-x86_64.tar.gz",  # via Rosetta
}

# The fpcalc archive is a single ~2-5 MB binary; anything past this is wrong.
_MAX_ARCHIVE_BYTES = 60 * 1024 * 1024


def managed_path() -> Path:
    return _tools_dir() / _EXE


def resolve_fpcalc() -> str | None:
    """The fpcalc binary Kira should use: system PATH first (user-managed wins),
    else the managed copy in ./tools/. None when neither exists."""
    system = shutil.which("fpcalc")
    if system:
        return system
    managed = managed_path()
    if managed.is_file():
        return str(managed)
    return None


def fpcalc_status() -> dict:
    """For the settings/onboarding UI: is fpcalc usable, where from, and can this
    platform one-click install it?"""
    path = resolve_fpcalc()
    source = None
    if path:
        source = "managed" if Path(path) == managed_path() else "system"
    key = (platform.system(), platform.machine())
    return {
        "available": path is not None,
        "path": path,
        "source": source,
        "installable": key in _DOWNLOADS,
        "installing": _install_running(),
        # Live install-job label for inline progress on the status row (same
        # contract as ffmpeg_status — the activity pill isn't always visible).
        "progress": _install_label(),
    }


def _install_running() -> bool:
    snap = activity.snapshot()
    return any(j["name"] == FPCALC_INSTALL_JOB and j["active"] for j in snap["jobs"])


def _install_label() -> str | None:
    snap = activity.snapshot()
    return next((j["label"] for j in snap["jobs"]
                 if j["name"] == FPCALC_INSTALL_JOB and j["active"]), None)


async def install_fpcalc() -> dict:
    """Download + unpack fpcalc into ./tools/, narrating to the activity pill.
    Returns fpcalc_status() when done. Never raises — failures end the pill red."""
    key = (platform.system(), platform.machine())
    url = _DOWNLOADS.get(key)
    if url is None:
        msg = f"No one-click build for {key[0]}/{key[1]} — install chromaprint/fpcalc from your package manager."
        activity.begin(FPCALC_INSTALL_JOB, "Installing fpcalc")
        activity.end(FPCALC_INSTALL_JOB, ok=False, detail=msg)
        return fpcalc_status()
    if resolve_fpcalc():
        return fpcalc_status()  # already usable — nothing to do

    activity.begin(FPCALC_INSTALL_JOB, "Installing fpcalc · downloading")
    archive = _tools_dir() / ("_fpcalc_download" + (".zip" if url.endswith(".zip") else ".tar.gz"))
    try:
        from kira import net
        client = net.shared_client()
        _tools_dir().mkdir(parents=True, exist_ok=True)

        received = 0
        with open(archive, "wb") as out:
            async with client.stream("GET", url, follow_redirects=True, timeout=60.0) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length") or 0) or None
                async for chunk in r.aiter_bytes(1 << 16):
                    received += len(chunk)
                    if received > _MAX_ARCHIVE_BYTES:
                        raise RuntimeError("download exceeded the size cap")
                    out.write(chunk)
                    if total:
                        activity.set_label(
                            FPCALC_INSTALL_JOB,
                            f"Installing fpcalc · downloading {received >> 20} / {total >> 20} MB",
                        )

        activity.set_label(FPCALC_INSTALL_JOB, "Installing fpcalc · unpacking")
        await asyncio.to_thread(_extract_binary, archive)

        path = resolve_fpcalc()
        if not path:
            raise RuntimeError("archive didn't contain an fpcalc binary")
        activity.end(FPCALC_INSTALL_JOB, ok=True,
                     detail="fpcalc installed — AcoustID fingerprint matching is now available")
        await _notify("success", "fpcalc installed",
                      f"Kira set up its own fpcalc at {path}. AcoustID fingerprint "
                      "matching for untagged music is now available.")
    except Exception as e:  # noqa: BLE001 — surface, never crash
        logger.warning(f"fpcalc install failed: {e!r}")
        activity.end(FPCALC_INSTALL_JOB, ok=False, detail=f"fpcalc install failed — {e}")
        await _notify("warning", "fpcalc install failed",
                      f"{e}. You can retry from Settings, or install chromaprint/fpcalc manually.")
    finally:
        try:
            if archive.exists():
                archive.unlink()
        except OSError:
            pass
    return fpcalc_status()


def _extract_binary(archive: Path) -> None:
    """Pull JUST the fpcalc binary out of the archive into ./tools/ (atomic temp +
    replace). Basename-matched — archive paths are never trusted."""
    dest = managed_path()
    tmp = dest.with_name(dest.name + ".part")

    def _write(stream) -> None:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(stream, out, 1 << 20)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            member = next((m for m in zf.namelist() if Path(m).name.lower() == _EXE), None)
            if member is None:
                raise RuntimeError("no fpcalc binary in archive")
            with zf.open(member) as src:
                _write(src)
    else:
        with tarfile.open(archive, mode="r:gz") as tf:
            member = next(
                (m for m in tf.getmembers() if m.isfile() and Path(m.name).name == _EXE),
                None,
            )
            if member is None:
                raise RuntimeError("no fpcalc binary in archive")
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError("could not read fpcalc from archive")
            with src:
                _write(src)

    os.replace(tmp, dest)
    if os.name != "nt":
        os.chmod(dest, 0o755)


async def _notify(kind: str, title: str, body: str) -> None:
    try:
        from kira.database import SessionLocal
        from kira.models import Notification
        async with SessionLocal() as session:
            session.add(Notification(kind=kind, title=title, body=body))
            await session.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"fpcalc notify failed (non-fatal): {e!r}")
