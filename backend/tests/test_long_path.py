r"""Windows long-path helper — the `\\?\` / `\\?\UNC\` extended-length rewrite.

The real >260-char move can't run in cross-platform CI, so we unit-test the pure
rewrite logic: NO-OP on POSIX + short + already-prefixed input, and the correct
prefix form for long drive vs UNC paths (os.name + abspath mocked so the Windows
branches run on Linux too)."""
from __future__ import annotations

import kira.longpath as lp
from kira.longpath import long_path


def test_noop_on_posix(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "posix")
    p = "/data/media/anime/" + "L" * 300 + "/file.mkv"
    assert long_path(p) == p


def test_short_windows_path_unchanged(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    p = "Z:\\media\\Show\\file.mkv"
    assert long_path(p) == p


def test_long_drive_path_gets_prefix(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    monkeypatch.setattr(lp.os.path, "abspath", lambda s: s)  # input already absolute
    p = "Z:\\media\\anime\\" + "L" * 250 + "\\file.mkv"
    out = long_path(p)
    assert out == "\\\\?\\" + p
    assert out.startswith("\\\\?\\Z:\\")


def test_long_unc_path_gets_unc_prefix(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    monkeypatch.setattr(lp.os.path, "abspath", lambda s: s)
    tail = "media\\anime\\" + "L" * 230 + "\\file.mkv"
    p = "\\\\192.168.0.63\\Data\\" + tail
    out = long_path(p)
    assert out == "\\\\?\\UNC\\192.168.0.63\\Data\\" + tail
    assert out.startswith("\\\\?\\UNC\\")


def test_already_prefixed_unchanged(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    p = "\\\\?\\Z:\\media\\" + "x" * 300
    assert long_path(p) == p


def test_idempotent(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    monkeypatch.setattr(lp.os.path, "abspath", lambda s: s)
    p = "Z:\\media\\anime\\" + "L" * 250 + "\\file.mkv"
    once = long_path(p)
    assert long_path(once) == once


def test_threshold_boundary(monkeypatch):
    monkeypatch.setattr(lp.os, "name", "nt")
    monkeypatch.setattr(lp.os.path, "abspath", lambda s: s)
    # 239 chars → untouched; 240 → rewritten.
    short = "C:\\" + "a" * 236        # len 239
    assert len(short) == 239 and long_path(short) == short
    longp = "C:\\" + "a" * 237        # len 240
    assert len(longp) == 240 and long_path(longp) == "\\\\?\\" + longp


def test_accepts_pathlike(monkeypatch):
    from pathlib import PurePosixPath
    monkeypatch.setattr(lp.os, "name", "posix")
    # os.fspath on a PathLike → no-op on posix, returns the string form.
    assert long_path(PurePosixPath("/x/y")) == "/x/y"
