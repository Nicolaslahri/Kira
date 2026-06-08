"""Pass 7 #11 — OpenSubtitles search + download + save."""

from __future__ import annotations

from pathlib import Path

from kira.providers import opensubtitles as osub


# ── pure parsers ─────────────────────────────────────────────────────────

def _subs_payload() -> dict:
    return {"data": [
        {"attributes": {"language": "en", "moviehash_match": False, "download_count": 50,
                        "files": [{"file_id": 111}], "release": "WEB"}},
        {"attributes": {"language": "en", "moviehash_match": True, "download_count": 10,
                        "files": [{"file_id": 222}], "release": "BluRay"}},
        {"attributes": {"language": "fr", "moviehash_match": False, "download_count": 99,
                        "files": [{"file_id": 333}], "release": "VOSTFR"}},
    ]}


def test_candidates_rank_moviehash_first() -> None:
    cands = osub.parse_subtitle_candidates(_subs_payload())
    # The hash-match (file 222) outranks the higher-download non-match (111).
    assert cands[0]["file_id"] == 222
    assert cands[0]["moviehash_match"] is True


def test_candidates_language_filter() -> None:
    cands = osub.parse_subtitle_candidates(_subs_payload(), ["fr"])
    assert [c["file_id"] for c in cands] == [333]


def test_candidates_skip_entries_without_files() -> None:
    payload = {"data": [{"attributes": {"language": "en", "files": []}}]}
    assert osub.parse_subtitle_candidates(payload) == []


def test_pick_best_per_language() -> None:
    cands = osub.parse_subtitle_candidates(_subs_payload(), ["en", "fr"])
    best = osub.pick_best_per_language(cands, ["en", "fr"])
    assert best["en"]["file_id"] == 222
    assert best["fr"]["file_id"] == 333


def test_parse_download_link() -> None:
    assert osub.parse_download_link({"link": "https://dl/x.srt"}) == "https://dl/x.srt"
    assert osub.parse_download_link({}) is None
    assert osub.parse_download_link({"link": ""}) is None


def test_parse_login_token() -> None:
    assert osub.parse_login_token({"token": "jwt123"}) == "jwt123"
    assert osub.parse_login_token({}) is None


def test_sidecar_name() -> None:
    assert osub.subtitle_sidecar_name("/m/Inception (2010).mkv", "EN") == "Inception (2010).en.srt"


# ── orchestrator with a routing fake client ──────────────────────────────

class _Resp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.headers = headers or {}
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
    def json(self):
        return self._payload


class _StreamResp:
    """Minimal async-context-manager response for client.stream(...)."""
    def __init__(self, resp: "_Resp"):
        self.status_code = resp.status_code
        self.headers = resp.headers
        self._content = resp.content
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False
    async def aiter_bytes(self):
        yield self._content


class _FakeClient:
    """Routes by URL: /subtitles (GET), /login (POST), /download (POST), and
    the download link (now fetched via stream) → srt bytes."""
    def __init__(self):
        self.calls = []
    def _dl_resp(self, url) -> _Resp:
        """Response for the actual subtitle-file download link (overridable)."""
        return _Resp(content=b"1\n00:00:01,000 --> 00:00:02,000\nHi\n")
    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        if "/subtitles" in url:
            return _Resp(payload=_subs_payload())
        if url.startswith("https://dl/"):
            return self._dl_resp(url)
        return _Resp(status=404)
    def stream(self, method, url, **kw):
        self.calls.append((method, url))
        if url.startswith("https://dl/"):
            return _StreamResp(self._dl_resp(url))
        return _StreamResp(_Resp(status=404))
    async def post(self, url, **kw):
        self.calls.append(("POST", url))
        if "/login" in url:
            return _Resp(payload={"token": "jwt123"})
        if "/download" in url:
            return _Resp(payload={"link": "https://dl/sub.srt"})
        return _Resp(status=404)


async def test_fetch_and_save_writes_sidecar(tmp_path) -> None:
    video = tmp_path / "Inception (2010).mkv"
    video.write_bytes(b"x" * 200_000)  # big enough for the hash, irrelevant to the fake
    client = _FakeClient()
    saved = await osub.fetch_and_save_subtitles(
        video, api_key="key", client=client, languages=["en"],
        username="u", password="p", tmdb_id=27205,
    )
    dest = tmp_path / "Inception (2010).en.srt"
    assert saved == [str(dest)]
    assert dest.exists() and dest.read_bytes().startswith(b"1\n")
    # Login happened (creds present) before the download.
    assert ("POST", "https://api.opensubtitles.com/api/v1/login") in client.calls


async def test_no_api_key_is_noop(tmp_path) -> None:
    video = tmp_path / "m.mkv"
    video.write_bytes(b"x" * 200_000)
    saved = await osub.fetch_and_save_subtitles(video, api_key=None, client=_FakeClient(), languages=["en"])
    assert saved == []


class _HtmlDlClient(_FakeClient):
    """Like _FakeClient but the download link 200s with an HTML error page —
    OpenSubtitles / its CDN does this under rate-limit instead of a 4xx."""
    def _dl_resp(self, url) -> _Resp:
        return _Resp(content=b"<!DOCTYPE html><html>rate limit</html>",
                     headers={"content-type": "text/html"})


async def test_html_error_200_not_saved_as_srt(tmp_path) -> None:
    video = tmp_path / "Inception (2010).mkv"
    video.write_bytes(b"x" * 200_000)
    saved = await osub.fetch_and_save_subtitles(
        video, api_key="key", client=_HtmlDlClient(), languages=["en"],
        username="u", password="p", tmdb_id=27205,
    )
    assert saved == []                                          # guard rejected it
    assert not (tmp_path / "Inception (2010).en.srt").exists()


async def test_existing_sidecar_skips_search_entirely(tmp_path) -> None:
    """All requested languages already on disk → return before spending any
    OpenSubtitles search/quota (exists-before-search)."""
    video = tmp_path / "Inception (2010).mkv"
    video.write_bytes(b"x" * 200_000)
    (tmp_path / "Inception (2010).en.srt").write_bytes(b"KEEP")
    client = _FakeClient()
    saved = await osub.fetch_and_save_subtitles(
        video, api_key="key", client=client, languages=["en"], tmdb_id=27205,
    )
    assert saved == []
    assert client.calls == []          # NO search, NO download — quota untouched


async def test_write_if_absent(tmp_path) -> None:
    video = tmp_path / "Inception (2010).mkv"
    video.write_bytes(b"x" * 200_000)
    existing = tmp_path / "Inception (2010).en.srt"
    existing.write_bytes(b"KEEP")
    saved = await osub.fetch_and_save_subtitles(
        video, api_key="key", client=_FakeClient(), languages=["en"], tmdb_id=27205,
    )
    assert saved == []                       # didn't overwrite
    assert existing.read_bytes() == b"KEEP"
