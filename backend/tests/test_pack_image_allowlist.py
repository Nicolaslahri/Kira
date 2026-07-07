"""Pack cover art through the /img proxy — exact-URL allow-list.

Pack posters live on whatever host the pack author uses (GitHub raw, a fan
CDN). The proxy's static provider allow-list rightly refuses those hosts, so
before this, every pack cover rendered as a blank initials card. The fix
allows the EXACT urls installed packs declare — never their whole hosts —
with the SSRF guard still applied downstream.
"""
from __future__ import annotations

from kira.api.images import _img_host_allowed
from kira.packs import loader
from kira.packs.schema import parse_pack

_PACK = parse_pack({
    "kira_pack": 1, "id": "one-pace", "name": "One Pace", "media_type": "anime",
    "show": {"title": "One Pace", "aliases": [], "year": 1999,
             "poster_url": "https://raw.githubusercontent.com/x/op/poster.jpg",
             "overview": "Fan re-edit.",
             "season_posters": {"2": "https://raw.githubusercontent.com/x/op/arc2.jpg"}},
    "match": {"titles": ["One Pace"]},
    "episodes": [
        {"season": 1, "episode": 1, "title": "Romance Dawn 01"},
    ],
})


def test_pack_urls_pass_the_proxy_gate(monkeypatch):
    monkeypatch.setattr(loader, "_packs", {"k": _PACK})
    monkeypatch.setattr(loader, "_image_urls_memo", None)

    # Exact declared urls (show + per-arc) are allowed…
    assert _img_host_allowed("https://raw.githubusercontent.com/x/op/poster.jpg")
    assert _img_host_allowed("https://raw.githubusercontent.com/x/op/arc2.jpg")
    # …but the HOST is not blanket-opened: a different path on it is refused.
    assert not _img_host_allowed("https://raw.githubusercontent.com/evil/other.jpg")
    # Provider hosts keep working as before.
    assert _img_host_allowed("https://image.tmdb.org/t/p/w500/x.jpg")


def test_memo_invalidates_on_evict(monkeypatch, tmp_path):
    monkeypatch.setattr(loader, "_packs", {"k": _PACK})
    monkeypatch.setattr(loader, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(loader, "_image_urls_memo", None)
    assert "https://raw.githubusercontent.com/x/op/poster.jpg" in loader.allowed_image_urls()
    loader._packs.pop("k")
    loader.evict("k")                      # invalidates the memo
    assert loader.allowed_image_urls() == set()
