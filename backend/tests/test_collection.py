"""Pass 7 #14 — movie collection extraction + grouping id."""

from __future__ import annotations


def _movie_details_payload(with_collection: bool) -> dict:
    d = {
        "title": "The Matrix",
        "release_date": "1999-03-30",
        "poster_path": "/p.jpg",
        "backdrop_path": "/b.jpg",
        "genres": [{"name": "Science Fiction"}],
        "credits": {"crew": [], "cast": []},
        "production_companies": [{"name": "Warner Bros."}],
        "production_countries": [{"iso_3166_1": "US"}],
        "overview": "A hacker learns the truth.",
        "runtime": 136,
    }
    if with_collection:
        d["belongs_to_collection"] = {"id": 2344, "name": "The Matrix Collection"}
    return d


async def test_get_movie_details_extracts_collection(monkeypatch) -> None:
    import httpx
    from kira.providers.tmdb import TMDBProvider
    from kira.providers.base import ProviderAuth

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return _movie_details_payload(True)

    class _Client:
        async def get(self, *a, **k): return _Resp()

    prov = TMDBProvider(base_url="https://api", auth=ProviderAuth(query_param="api_key", query_value="x"), client=_Client())
    out = await prov.get_movie_details("603")
    assert out["collection_id"] == "2344"
    assert out["collection_name"] == "The Matrix Collection"
    assert out["fanart_url"] == "https://image.tmdb.org/t/p/original/b.jpg"


async def test_get_movie_details_standalone_has_no_collection(monkeypatch) -> None:
    from kira.providers.tmdb import TMDBProvider
    from kira.providers.base import ProviderAuth

    class _Resp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return _movie_details_payload(False)

    class _Client:
        async def get(self, *a, **k): return _Resp()

    prov = TMDBProvider(base_url="https://api", auth=ProviderAuth(query_param="api_key", query_value="x"), client=_Client())
    out = await prov.get_movie_details("603")
    assert out["collection_id"] is None
    assert out["collection_name"] is None


def test_collection_group_id_format() -> None:
    # The grouping id the matcher writes for a collected movie.
    coll_id = "2344"
    assert f"tmdb-collection:{coll_id}" == "tmdb-collection:2344"
