"""Kira CLI — arg parsing, command dispatch, and the rename safety default.

Commands take a duck-typed `api` (get/post), so we inject a FakeApi that
records calls and returns canned responses — no HTTP, no server needed.
"""

from __future__ import annotations

import json

import pytest

from kira import cli


class FakeApi:
    def __init__(self, responses: dict) -> None:
        self.base = "http://test/api/v1"
        self._responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def get(self, path, **kw):
        self.calls.append(("GET", path, kw))
        return self._resolve(("GET", path))

    def post(self, path, **kw):
        self.calls.append(("POST", path, kw))
        return self._resolve(("POST", path))

    def _resolve(self, key):
        if key not in self._responses:
            raise AssertionError(f"unexpected API call: {key}")
        return self._responses[key]

    def body_of(self, method: str, path: str) -> dict:
        for m, p, kw in self.calls:
            if m == method and p == path:
                return kw.get("json") or {}
        raise AssertionError(f"no {method} {path} call recorded")


def _args(argv: list[str]):
    return cli.build_parser().parse_args(argv)


# ── helpers ─────────────────────────────────────────────────────────────────
def test_setting_value_handles_bare_and_wrapped():
    assert cli._setting_value({"k": "v"}, "k", "d") == "v"
    assert cli._setting_value({"k": {"value": "v"}}, "k", "d") == "v"
    assert cli._setting_value({}, "k", "d") == "d"
    assert cli._setting_value({"k": {"other": 1}}, "k", "d") == "d"


def test_describe_match_formats_episode():
    m = {"title": "Breaking Bad", "year": 2008, "season_number": 1, "episode_number": 5, "episode_title": "Gray Matter"}
    out = cli._describe_match(m)
    assert "Breaking Bad" in out and "(2008)" in out and "S01E05" in out and "Gray Matter" in out


def test_describe_match_none():
    assert "no match" in cli._describe_match(None)


def test_basename_cross_platform():
    assert cli._basename("/a/b/c.mkv") == "c.mkv"
    assert cli._basename("C:\\a\\b\\c.mkv") == "c.mkv"


# ── parser ──────────────────────────────────────────────────────────────────
def test_parser_dispatches_each_command():
    assert _args(["status"]).func is cli.cmd_status
    assert _args(["scan", "/media"]).func is cli.cmd_scan
    assert _args(["ls"]).func is cli.cmd_ls
    assert _args(["rename", "--all"]).func is cli.cmd_rename
    assert _args(["identify", "7"]).func is cli.cmd_identify


def test_main_no_command_is_usage_error():
    assert cli.main([]) == 2


# ── status ──────────────────────────────────────────────────────────────────
def test_status_json_aggregates_counts(capsys):
    api = FakeApi({
        ("GET", "/files"): [
            {"status": "matched"}, {"status": "matched"}, {"status": "no_match"},
        ],
        ("GET", "/activity"): {"jobs": [], "active": False, "boot": None},
    })
    rc = cli.cmd_status(api, _args(["status", "--json"]))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["total"] == 3
    assert out["by_status"] == {"matched": 2, "no_match": 1}


# ── rename safety ─────────────────────────────────────────────────────────────
def _rename_api():
    return FakeApi({
        ("GET", "/settings"): {"rename.default_op": "move", "naming.profile": "Plex"},
        ("POST", "/rename"): {"succeeded": 1, "failed": 0,
                               "items": [{"file_id": 1, "ok": True, "old_path": "/x/a.mkv", "new_path": "TV/a.mkv"}]},
    })


def test_rename_defaults_to_dry_run():
    api = _rename_api()
    rc = cli.cmd_rename(api, _args(["rename", "--ids", "1"]))
    assert rc == 0
    body = api.body_of("POST", "/rename")
    assert body["dry_run"] is True          # NEVER moves files without --apply
    assert body["file_ids"] == [1]
    assert body["op"] == "move"             # pulled from server's saved default
    assert body["profile"] == "Plex"


def test_rename_apply_executes():
    api = _rename_api()
    cli.cmd_rename(api, _args(["rename", "--ids", "1", "--apply"]))
    assert api.body_of("POST", "/rename")["dry_run"] is False


def test_rename_op_override():
    api = _rename_api()
    cli.cmd_rename(api, _args(["rename", "--ids", "2,3", "--op", "copy"]))
    body = api.body_of("POST", "/rename")
    assert body["op"] == "copy"
    assert body["file_ids"] == [2, 3]


def test_rename_requires_a_selector():
    api = FakeApi({})
    with pytest.raises(cli.CliError):
        cli.cmd_rename(api, _args(["rename"]))


def test_rename_all_gathers_matched_files():
    api = FakeApi({
        ("GET", "/files"): [
            {"id": 1, "matches": [{"is_selected": True, "title": "X"}]},
            {"id": 2, "matches": []},  # no match → skipped
            {"id": 3, "matches": [{"is_selected": True, "title": "Y"}]},
        ],
        ("GET", "/settings"): {},
        ("POST", "/rename"): {"succeeded": 2, "failed": 0, "items": []},
    })
    cli.cmd_rename(api, _args(["rename", "--all"]))
    assert api.body_of("POST", "/rename")["file_ids"] == [1, 3]


# ── footgun guards (audit: CLI) ───────────────────────────────────────────────
def test_rename_rejects_multiple_selectors():
    """`--ids 1 --all` must error, not silently honour whichever wins the if."""
    api = FakeApi({})  # must fail before any API call
    with pytest.raises(cli.CliError, match="Pick ONE"):
        cli.cmd_rename(api, _args(["rename", "--ids", "1", "--all"]))


def test_rename_unknown_status_fails_closed():
    """A typo'd status must fail loudly, not match zero files and 'succeed'."""
    api = FakeApi({})
    with pytest.raises(cli.CliError, match="Unknown --status"):
        cli.cmd_rename(api, _args(["rename", "--status", "machted"]))


def test_ls_unknown_status_fails_closed():
    api = FakeApi({})
    with pytest.raises(cli.CliError, match="Unknown --status"):
        cli.cmd_ls(api, _args(["ls", "--status", "bogus"]))


def test_rename_ids_reports_all_bad_tokens():
    api = FakeApi({})
    with pytest.raises(cli.CliError) as ei:
        cli.cmd_rename(api, _args(["rename", "--ids", "1,x,3,-2,0"]))
    msg = str(ei.value)
    assert "x" in msg and "-2" in msg and "0" in msg   # ALL bad tokens, not just the first


def test_rename_ids_accepts_positive_with_plus_and_spaces():
    api = _rename_api()
    cli.cmd_rename(api, _args(["rename", "--ids", "1, 2 ,+3"]))
    assert api.body_of("POST", "/rename")["file_ids"] == [1, 2, 3]


class _NonJsonResp:
    status_code = 200
    content = b"<html>not json</html>"
    text = "<html>not json</html>"

    def json(self):
        raise ValueError("Expecting value")


def test_non_json_200_raises_clierror(monkeypatch):
    """A 200 whose body isn't JSON (wrong URL → some other server's HTML) must
    surface as a clean CliError, not a raw JSONDecodeError traceback."""
    api = cli.Api("http://test/api/v1", 5.0)
    monkeypatch.setattr(api._client, "request", lambda *a, **k: _NonJsonResp())
    with pytest.raises(cli.CliError, match="isn't JSON"):
        api.get("/files")
    api._client.close()
