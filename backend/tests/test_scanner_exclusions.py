"""Directory-exclusion prefixes (audit fix).

A bare "@" prefix used to skip the entire subtree of any user folder beginning
with "@" (e.g. "@Animes" / "@Movies" — a common sort-to-top prefix). We now
list only the actual Synology/QNAP system dirs, so user "@" folders scan.
"""
from __future__ import annotations

from kira.scanner import _IGNORED_PREFIXES


def test_user_at_folders_are_not_excluded():
    assert "@Animes".startswith(_IGNORED_PREFIXES) is False
    assert "@Movies".startswith(_IGNORED_PREFIXES) is False
    assert "@4K".startswith(_IGNORED_PREFIXES) is False


def test_nas_system_dirs_still_excluded():
    assert "@eaDir".startswith(_IGNORED_PREFIXES) is True            # Synology
    assert "@__thumb".startswith(_IGNORED_PREFIXES) is True          # QNAP
    assert "@Recycle".startswith(_IGNORED_PREFIXES) is True          # QNAP trash
    assert ".hidden".startswith(_IGNORED_PREFIXES) is True           # dotfiles
    assert "$RECYCLE.BIN".startswith(_IGNORED_PREFIXES) is True      # Windows
