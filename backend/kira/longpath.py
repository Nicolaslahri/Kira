r"""Windows long-path (MAX_PATH) helper.

Windows caps a full path at 260 chars UNLESS it's an extended-length path —
prefixed `\\?\` (drive) or `\\?\UNC\` (network share) — which lifts the cap to
~32,767. Kira already clamps each path *component* to 255 bytes
(`renamer/templates`), but a deep folder tree + long titles (anime / light novels
are the worst offenders) can still blow past 260 in TOTAL, OSError-ing the
move/mkdir.

Deployment scope (deliberate):
  • **Windows .exe** — local-drive, mapped-drive, or UNC media: THIS is what the
    helper fixes (`\\?\C:\…`, `\\?\Z:\…`, `\\?\UNC\server\share\…`).
  • **Docker-on-NAS (Linux)** — no such limit (PATH_MAX is 4096, per-component
    already clamped), so this is a hard NO-OP (`os.name != "nt"`). The primary
    deployment is therefore completely unaffected.

Only paths actually in the danger zone (>= THRESHOLD) are rewritten, so normal
short paths are returned byte-for-byte unchanged — zero behavior change on the
overwhelmingly common case, and existing tests stay green untouched.
"""
from __future__ import annotations

import os

# Rewrite at 240, not 260: leaves headroom for the short sibling suffixes Kira
# appends mid-operation (".kira-copy-tmp" / ".kira-casefix-tmp") without tipping a
# just-under path over the real limit between render and write.
_THRESHOLD = 240


def long_path(p: "os.PathLike[str] | str") -> str:
    r"""Return `p` as a Windows extended-length path string when it's long enough
    to risk the 260-char MAX_PATH limit; otherwise return it unchanged.

    No-op on non-Windows, on comfortably-short paths, and on already-prefixed
    input. A `\\?\` path MUST be fully-qualified + backslash-separated and the OS
    does NO normalization on it (`/`, `.`, `..` are taken literally) — so we run
    it through `os.path.abspath` first to guarantee both.
    """
    s = os.fspath(p)
    if os.name != "nt":
        return s
    if s.startswith("\\\\?\\"):
        return s                       # already extended-length
    if len(s) < _THRESHOLD:
        return s                       # comfortably under MAX_PATH — leave it alone
    ap = os.path.abspath(s)            # absolute + backslash-normalized + collapsed
    if ap.startswith("\\\\"):
        # UNC: \\server\share\... -> \\?\UNC\server\share\...
        return "\\\\?\\UNC\\" + ap[2:]
    return "\\\\?\\" + ap
