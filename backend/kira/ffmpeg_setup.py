"""Managed ffmpeg — find it, or install a private copy with one click.

Embedded subtitle extraction (the highest-yield subtitle source for anime)
needs ffmpeg, but "go install ffmpeg" is exactly the kind of homework Kira
exists to remove. So:

  resolve_ffmpeg()   PATH ffmpeg, else Kira's own managed copy in ./tools/
  ffmpeg_status()    {available, path, source} for the settings/onboarding UI
  install_ffmpeg()   download a static build (BtbN GitHub releases — the same
                     builds most *arr stacks point users at), extract JUST the
                     ffmpeg binary into ./tools/, narrated via the activity
                     pill. No PATH edits, no system install, nothing global.

The download URL is a fixed constant (not user input), streamed to a temp
file under a hard size cap, and the archive member is matched by exact
basename — a hostile archive can't plant files outside ./tools/.

The Docker image ships ffmpeg already; this is for bare-metal installs.
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

logger = logging.getLogger("kira.ffmpeg_setup")

FFMPEG_INSTALL_JOB = "ffmpeg_install"

# Kira's private tool dir — beside kira.db / kira-id-index.json (CWD = the
# backend's working dir, the one writable place we already rely on).
def _tools_dir() -> Path:
    return Path.cwd() / "tools"


_EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

# Static builds used by most of the selfhosted ecosystem. Latest-release
# aliases so we never pin a stale version.
_BTBN = "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download"
_DOWNLOADS: dict[tuple[str, str], str] = {
    ("Windows", "AMD64"):  f"{_BTBN}/ffmpeg-master-latest-win64-gpl.zip",
    ("Windows", "ARM64"):  f"{_BTBN}/ffmpeg-master-latest-winarm64-gpl.zip",
    ("Linux", "x86_64"):   f"{_BTBN}/ffmpeg-master-latest-linux64-gpl.tar.xz",
    ("Linux", "aarch64"):  f"{_BTBN}/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
}

# Full archives are ~150-200 MB; anything past this is wrong/hostile.
_MAX_ARCHIVE_BYTES = 450 * 1024 * 1024


def managed_path() -> Path:
    return _tools_dir() / _EXE


def resolve_ffmpeg() -> str | None:
    """The ffmpeg binary Kira should use: system PATH first (user-managed wins),
    else the managed copy in ./tools/. None when neither exists."""
    system = shutil.which("ffmpeg")
    if system:
        return system
    managed = managed_path()
    if managed.is_file():
        return str(managed)
    return None


def ffmpeg_status() -> dict:
    """For the settings/onboarding UI: is ffmpeg usable, where from, and can
    this platform one-click install it?"""
    path = resolve_ffmpeg()
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
    }


def _install_running() -> bool:
    snap = activity.snapshot()
    return any(j["name"] == FFMPEG_INSTALL_JOB and j["active"] for j in snap["jobs"])


async def install_ffmpeg() -> dict:
    """Download + unpack ffmpeg into ./tools/, narrating to the activity pill.
    Returns ffmpeg_status() when done. Never raises — failures end the pill
    red with the reason and post a notification."""
    key = (platform.system(), platform.machine())
    url = _DOWNLOADS.get(key)
    if url is None:
        msg = f"No one-click build for {key[0]}/{key[1]} — install ffmpeg from ffmpeg.org instead."
        activity.begin(FFMPEG_INSTALL_JOB, "Installing ffmpeg")
        activity.end(FFMPEG_INSTALL_JOB, ok=False, detail=msg)
        return ffmpeg_status()
    if resolve_ffmpeg():
        return ffmpeg_status()  # already usable — nothing to do

    activity.begin(FFMPEG_INSTALL_JOB, "Installing ffmpeg · downloading")
    archive = _tools_dir() / ("_ffmpeg_download" + (".zip" if url.endswith(".zip") else ".tar.xz"))
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
                        mb = received // (1 << 20)
                        activity.set_label(
                            FFMPEG_INSTALL_JOB,
                            f"Installing ffmpeg · downloading {mb} / {total >> 20} MB",
                        )

        activity.set_label(FFMPEG_INSTALL_JOB, "Installing ffmpeg · unpacking")
        await asyncio.to_thread(_extract_binary, archive)

        path = resolve_ffmpeg()
        if not path:
            raise RuntimeError("archive didn't contain an ffmpeg binary")
        activity.end(FFMPEG_INSTALL_JOB, ok=True,
                     detail="ffmpeg installed — embedded subtitle extraction is now available")
        await _notify("success", "ffmpeg installed",
                      f"Kira set up its own ffmpeg at {path}. Embedded subtitle "
                      "extraction (the best source for anime) is now available.")
    except Exception as e:  # noqa: BLE001 — surface, never crash
        logger.warning(f"ffmpeg install failed: {e!r}")
        activity.end(FFMPEG_INSTALL_JOB, ok=False, detail=f"ffmpeg install failed — {e}")
        await _notify("warning", "ffmpeg install failed",
                      f"{e}. You can retry from Settings → Subtitles, or install "
                      "ffmpeg manually from ffmpeg.org.")
    finally:
        try:
            if archive.exists():
                archive.unlink()
        except OSError:
            pass
    return ffmpeg_status()


def _extract_binary(archive: Path) -> None:
    """Pull JUST the ffmpeg binary out of the archive into ./tools/ (atomic
    temp + replace). Basename-matched — archive paths are never trusted."""
    dest = managed_path()
    tmp = dest.with_name(dest.name + ".part")

    def _write(stream) -> None:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(stream, out, 1 << 20)

    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            member = next(
                (m for m in zf.namelist()
                 if Path(m).name.lower() == _EXE and "/bin/" in m.replace("\\", "/")),
                None,
            ) or next((m for m in zf.namelist() if Path(m).name.lower() == _EXE), None)
            if member is None:
                raise RuntimeError("no ffmpeg binary in archive")
            with zf.open(member) as src:
                _write(src)
    else:
        with tarfile.open(archive, mode="r:xz") as tf:
            member = next(
                (m for m in tf.getmembers()
                 if m.isfile() and Path(m.name).name == _EXE),
                None,
            )
            if member is None:
                raise RuntimeError("no ffmpeg binary in archive")
            src = tf.extractfile(member)
            if src is None:
                raise RuntimeError("could not read ffmpeg from archive")
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
        logger.warning(f"ffmpeg notify failed (non-fatal): {e!r}")
