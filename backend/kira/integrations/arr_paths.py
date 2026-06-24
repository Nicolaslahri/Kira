"""Shared *arr path translation — used by both the Sonarr and Radarr relink hooks.

When Kira renames/moves a managed folder, the *arr's stored path goes stale and
its next disk scan marks the files deleted (and may re-grab). The relink hooks
fix that by `PUT`ting the *arr its NEW path with `moveFiles=false`. But Kira and
the *arr usually mount the same volume at DIFFERENT roots (Kira `/media/...`,
the *arr `/data/media/...`), and Kira can't know that mapping a priori. This
module derives it from the one fact we DO have: the *arr's stored path and Kira's
OLD folder point at the SAME physical directory.

Pure + provider-agnostic (movies and series both): one implementation, one test
matrix. Sonarr re-exports this as `_translate_path` for back-compat.
"""
from __future__ import annotations


def translate_path(arr_old: str, kira_old: str, kira_new: str) -> str | None:
    """Map Kira's NEW folder into the *arr's own path namespace.

    The *arr's stored path (`arr_old`) and Kira's OLD folder (`kira_old`) point
    at the SAME physical directory, so their longest common path *suffix* is the
    shared library-relative tail. Strip that tail off each to learn the two mount
    prefixes, then re-root Kira's NEW folder (`kira_new`) under the *arr's prefix.

    Returns None when the two paths share no suffix (can't map safely) or when
    the new folder isn't under Kira's mount prefix (a cross-mount move we can't
    translate) — the caller then leaves the path alone and just rescans.
    Comparison is case-insensitive (Windows/macOS volumes); the result keeps the
    *arr prefix's casing + the new folder's on-disk casing.
    """
    def _parts(p: str) -> list[str]:
        return [seg for seg in p.replace("\\", "/").rstrip("/").split("/") if seg]

    a, ko, kn = _parts(arr_old), _parts(kira_old), _parts(kira_new)
    if not a or not ko or not kn:
        return None
    common = 0
    while (common < len(a) and common < len(ko)
           and a[-1 - common].lower() == ko[-1 - common].lower()):
        common += 1
    if common == 0:
        return None  # no shared tail → don't trust a mapping
    arr_prefix = a[: len(a) - common]      # the *arr's mount prefix
    kira_prefix = ko[: len(ko) - common]   # Kira's mount prefix
    if kira_prefix:
        # New folder must live under Kira's mount prefix for the re-root to hold.
        if [s.lower() for s in kn[: len(kira_prefix)]] != [s.lower() for s in kira_prefix]:
            return None
    elif kn[0].lower() != ko[0].lower():
        # Kira's mount is the volume root — require the same top-level dir so a
        # move to an unrelated root (a different volume) isn't silently re-rooted.
        return None
    tail = kn[len(kira_prefix):]           # new path relative to Kira's mount
    if not tail:
        return None
    lead = "/" if arr_old.replace("\\", "/").startswith("/") else ""
    return lead + "/".join(arr_prefix + tail)
