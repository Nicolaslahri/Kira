"""Kira command-line client.

A thin, scriptable CLI that drives a RUNNING Kira server over its HTTP API -
the natural shape for a daemon-style app (Docker container / always-on
service). It reuses the exact same endpoints the web UI does, so there's no
second matching/rename code path to drift out of sync.

    kira status                 # library summary + background activity
    kira scan [PATH]            # scan a path (or the configured root) and follow it
    kira ls --status no_match   # list files + their matches
    kira rename --all           # DRY-RUN by default; --apply to actually move
    kira identify 123           # content-hash identify one file (OpenSubtitles)

Point it at a remote/container instance with --api or the KIRA_API env var.
Exit codes: 0 ok - 1 runtime error - 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import Any

import httpx

DEFAULT_API = os.environ.get("KIRA_API", "http://127.0.0.1:8000/api/v1")

# Scan statuses that mean "stop polling".
_TERMINAL = ("completed", "completed_partial")

# MediaFile statuses the server recognises. Used to FAIL-CLOSED on a typo'd
# --status rather than silently matching zero files — a dangerous no-op for
# `rename`, where the user would think they'd renamed something and hadn't.
_KNOWN_STATUSES = frozenset({
    "discovered", "pending", "matching", "matched",
    "approved", "rejected", "no_match", "renamed",
})


class CliError(Exception):
    """A user-facing failure - printed to stderr, exit code 1."""


def _validate_status(status: str) -> None:
    if status not in _KNOWN_STATUSES:
        raise CliError(f"Unknown --status '{status}'. Valid: {', '.join(sorted(_KNOWN_STATUSES))}")


def _parse_ids(raw: str) -> list[int]:
    """Parse a comma/space-separated id list into POSITIVE ints, reporting ALL
    malformed tokens at once (not just the first one argparse/int would hit)."""
    tokens = [t for t in raw.replace(",", " ").split() if t]
    bad = [t for t in tokens if not (t.lstrip("+").isdigit() and int(t.lstrip("+")) > 0)]
    if bad:
        raise CliError(f"--ids must be positive integers; invalid: {', '.join(bad)}")
    return [int(t) for t in tokens]


# -- tiny ANSI helpers (no-op when not a TTY) --------------------------------
_TTY = sys.stdout.isatty()


def _c(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if _TTY else s


def dim(s: str) -> str: return _c("2", s)
def bold(s: str) -> str: return _c("1", s)
def green(s: str) -> str: return _c("32", s)
def red(s: str) -> str: return _c("31", s)
def yellow(s: str) -> str: return _c("33", s)
def cyan(s: str) -> str: return _c("36", s)


# -- HTTP --------------------------------------------------------------------
class Api:
    def __init__(self, base: str, timeout: float) -> None:
        self.base = base.rstrip("/")
        # X-Requested-With: the server's CSRF guard rejects state-changing
        # requests without a custom header (browser forms can't set one).
        self._client = httpx.Client(
            base_url=self.base, timeout=timeout,
            headers={"X-Requested-With": "Kira-CLI"},
        )

    def __enter__(self) -> "Api":
        return self

    def __exit__(self, *exc: object) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kw: Any) -> Any:
        try:
            resp = self._client.request(method, path, **kw)
        except httpx.ConnectError as e:
            raise CliError(
                f"Kira API not reachable at {self.base} - is the server running?\n  ({e})"
            ) from e
        except httpx.HTTPError as e:
            raise CliError(f"Request to {path} failed: {e}") from e
        if resp.status_code >= 400:
            detail = resp.text
            try:
                body = resp.json()
                detail = body.get("detail", detail) if isinstance(body, dict) else detail
            except ValueError:
                pass
            raise CliError(f"{method} {path} -> HTTP {resp.status_code}: {detail}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError as e:
            raise CliError(
                f"{method} {path} -> 200 OK but the body isn't JSON "
                f"({len(resp.content)} bytes). Is {self.base} really the Kira API?"
            ) from e

    def get(self, path: str, **kw: Any) -> Any:
        return self._request("GET", path, **kw)

    def post(self, path: str, **kw: Any) -> Any:
        return self._request("POST", path, **kw)


# -- helpers -----------------------------------------------------------------
def _selected_match(file: dict) -> dict | None:
    matches = file.get("matches") or []
    return next((m for m in matches if m.get("is_selected")), matches[0] if matches else None)


def _describe_match(m: dict | None) -> str:
    if not m:
        return dim("- no match")
    title = m.get("title") or "?"
    bits = [title]
    if m.get("year"):
        bits.append(f"({m['year']})")
    se = ""
    if m.get("season_number") is not None and m.get("episode_number") is not None:
        se = f" S{m['season_number']:02d}E{m['episode_number']:02d}"
    elif m.get("episode_number") is not None:
        se = f" E{m['episode_number']}"
    et = f" - {m['episode_title']}" if m.get("episode_title") else ""
    return f"{' '.join(bits)}{se}{et}"


def _basename(path: str) -> str:
    return path.replace("\\", "/").rsplit("/", 1)[-1]


def _setting_value(settings: dict, key: str, default: str) -> str:
    """Settings values come back either bare or wrapped as {"value": X}."""
    from kira.settings_store import unwrap_str  # pure helper, no server deps pulled in

    return unwrap_str(settings.get(key)) or default


def _fetch_all_files(api: Api, status: str | None = None, limit: int = 100_000) -> list[dict]:
    params: dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    return api.get("/files", params=params) or []


# -- commands ----------------------------------------------------------------
def cmd_status(api: Api, args: argparse.Namespace) -> int:
    files = _fetch_all_files(api)
    counts = Counter(f.get("status", "unknown") for f in files)
    activity = api.get("/activity") or {}

    if args.json:
        print(json.dumps({"total": len(files), "by_status": dict(counts), "activity": activity}, indent=2))
        return 0

    print(f"{bold('Kira')} {dim('- ' + api.base)}\n")
    print(bold("Library"))
    if not files:
        print("  " + dim("empty - run a scan"))
    else:
        for st, n in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {st:<12}{n:>7}")
        print("  " + dim("-" * 19))
        print(f"  {'total':<12}{len(files):>7}")

    print("\n" + bold("Activity"))
    jobs = [j for j in activity.get("jobs", []) if j.get("active")]
    if jobs:
        for j in jobs:
            done = j.get("done") or 0
            total = j.get("total")
            prog = f" - {done}/{total}" if total else (f" - {done}" if done else "")
            print(f"  {cyan('>')} {j.get('label', j.get('name'))}{prog}")
    else:
        print("  " + dim("idle"))

    boot = activity.get("boot")
    if boot and boot.get("files_reset"):
        n = boot["files_reset"]
        print("\n" + yellow(f"Recovered {n} file{'s' if n != 1 else ''} left mid-scan by a restart."))
    return 0


def cmd_scan(api: Api, args: argparse.Namespace) -> int:
    root = args.path
    if not root:
        settings = api.get("/settings") or {}
        root = _setting_value(settings, "paths.library_root", "")
        if not root:
            raise CliError("No PATH given and no library root configured (Settings -> Library).")
        print(dim(f"Using configured library root: {root}"))

    scan = api.post("/scans", json={"root_path": root})
    scan_id = scan["id"]
    print(f"Scan {bold('#' + str(scan_id))} started on {cyan(root)}")

    if args.no_follow:
        print(dim("Not following (--no-follow). Check `kira status`."))
        return 0

    last_line = ""
    while True:
        time.sleep(1.5)
        s = api.get(f"/scans/{scan_id}")
        status = s.get("status", "?")
        fc = s.get("file_count") or 0
        mc = s.get("matched_count") or 0
        total = s.get("estimated_total")
        if status == "matching" and fc:
            tail = f"matching {mc}/{fc}"
        elif total:
            tail = f"scanning {fc}/{total}"
        else:
            tail = f"scanning {fc} found"
        line = f"  {status:<18} {tail}"
        if line != last_line:
            # \r overwrite when a TTY, else one line per change.
            print(("\r" + line + " " * 8) if _TTY else line, end="" if _TTY else "\n", flush=True)
            last_line = line
        if status in _TERMINAL or status.startswith("failed"):
            if _TTY:
                print()
            break

    if status.startswith("failed"):
        print(red(f"Scan failed: {status}"))
        return 1
    print(green(f"Scan complete - {s.get('file_count', 0)} files, {s.get('matched_count', 0)} matched."))
    return 0


def cmd_ls(api: Api, args: argparse.Namespace) -> int:
    if args.status:
        _validate_status(args.status)
    files = _fetch_all_files(api, status=args.status, limit=args.limit)
    if args.json:
        print(json.dumps(files, indent=2))
        return 0
    if not files:
        print(dim("No files." if not args.status else f"No files with status '{args.status}'."))
        return 0
    for f in files:
        m = _selected_match(f)
        st = f.get("status", "?")
        st_col = green(st) if st in ("matched", "approved", "renamed") else (
            red(st) if st == "no_match" else yellow(st))
        print(f"{bold('#' + str(f['id'])):<7} {st_col:<22} {_basename(f.get('file_path', ''))}")
        print(f"        {dim('->')} {_describe_match(m)}")
    print(dim(f"\n{len(files)} file{'s' if len(files) != 1 else ''}."))
    return 0


def cmd_rename(api: Api, args: argparse.Namespace) -> int:
    # Gather target file ids from EXACTLY ONE selector — reject conflicting
    # combinations instead of silently honouring whichever the if/elif hits
    # first (e.g. `--ids 1 --all` would otherwise ignore --all without a word).
    if sum((bool(args.ids), bool(args.status), bool(args.all))) > 1:
        raise CliError("Pick ONE of --ids / --status / --all, not several.")
    file_ids: list[int] = []
    if args.ids:
        file_ids = _parse_ids(args.ids)
    elif args.status:
        _validate_status(args.status)
        file_ids = [f["id"] for f in _fetch_all_files(api, status=args.status)]
    elif args.all:
        file_ids = [f["id"] for f in _fetch_all_files(api) if _selected_match(f)]
    else:
        raise CliError("Specify what to rename: --ids 1,2,3 | --status matched | --all")

    if not file_ids:
        print(dim("Nothing to rename."))
        return 0

    # Resolve op + profile from the server's saved defaults unless overridden.
    settings = api.get("/settings") or {}
    op = args.op or _setting_value(settings, "rename.default_op", "hardlink")
    profile = args.profile or _setting_value(settings, "naming.profile", "Plex")
    dry_run = not args.apply

    if dry_run:
        print(bold(yellow("DRY RUN")) + dim(f" - previewing {len(file_ids)} file(s); pass --apply to execute.\n"))
    else:
        print(bold(f"Renaming {len(file_ids)} file(s)") + dim(f" - op={op} - profile={profile}\n"))

    result = api.post("/rename", json={
        "file_ids": file_ids, "op": op, "profile": profile, "dry_run": dry_run,
    })
    items = result.get("items", [])
    for it in items:
        if it.get("ok"):
            old = _basename(it.get("old_path") or "")
            new = it.get("new_path") or ""
            print(f"  {green('OK')} {old}\n      {dim('->')} {new}")
        else:
            print(f"  {red('x')} file #{it.get('file_id')}: {it.get('error') or 'failed'}")

    ok = result.get("succeeded", 0)
    bad = result.get("failed", 0)
    verb = "would rename" if dry_run else "renamed"
    print()
    summary = f"{ok} {verb}" + (f", {bad} blocked" if bad else "")
    print(green(summary) if not bad else yellow(summary))
    if dry_run and ok:
        print(dim("Re-run with --apply to perform these renames."))
    return 0 if bad == 0 else 1


def cmd_identify(api: Api, args: argparse.Namespace) -> int:
    print(dim(f"Hashing file #{args.file_id} and asking OpenSubtitles…"))
    updated = api.post(f"/files/{args.file_id}/identify-by-hash")
    m = _selected_match(updated)
    print(green("Identified by content:"))
    print(f"  {_basename(updated.get('file_path', ''))}")
    print(f"  {dim('->')} {_describe_match(m)}")
    return 0


# -- arg parsing -------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kira", description="Kira media-renamer CLI (drives a running Kira server).")
    try:
        from importlib.metadata import version as _v
        ver = _v("kira")
    except Exception:
        from kira import __version__ as ver  # type: ignore
    p.add_argument("--version", action="version", version=f"kira {ver}")
    p.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API}, or $KIRA_API)")
    p.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds (default: 30)")
    sub = p.add_subparsers(dest="command", metavar="<command>")

    s = sub.add_parser("status", help="Library summary + background activity")
    s.add_argument("--json", action="store_true", help="Machine-readable output")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("scan", help="Scan a path (or the configured root) and follow progress")
    s.add_argument("path", nargs="?", help="Folder to scan (default: configured library root)")
    s.add_argument("--no-follow", action="store_true", help="Start the scan and return immediately")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("ls", help="List files and their matches")
    s.add_argument("--status", help="Filter by status (matched, no_match, pending, …)")
    s.add_argument("--limit", type=int, default=500, help="Max files to list (default: 500)")
    s.add_argument("--json", action="store_true", help="Machine-readable output")
    s.set_defaults(func=cmd_ls)

    s = sub.add_parser("rename", help="Rename matched files (DRY-RUN unless --apply)")
    sel = s.add_argument_group("what to rename (pick one)")
    sel.add_argument("--ids", help="Comma/space-separated file ids")
    sel.add_argument("--status", help="All files with this status")
    sel.add_argument("--all", action="store_true", help="All files that have a match")
    s.add_argument("--op", choices=["move", "copy", "symlink", "hardlink"], help="Operation (default: server's saved default)")
    s.add_argument("--profile", help="Naming profile (default: server's saved profile)")
    s.add_argument("--apply", action="store_true", help="Actually perform the renames (otherwise dry-run)")
    s.set_defaults(func=cmd_rename)

    s = sub.add_parser("identify", help="Identify one file by its content hash (OpenSubtitles)")
    s.add_argument("file_id", type=int, help="MediaFile id (see `kira ls`)")
    s.set_defaults(func=cmd_identify)

    return p


def main(argv: list[str] | None = None) -> int:
    # Force UTF-8 output so nothing crashes on a Windows console whose default
    # code page is cp1252; degrade gracefully where reconfigure isn't available.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        with Api(args.api, args.timeout) as api:
            return args.func(api, args)
    except CliError as e:
        print(red("error: ") + str(e), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
