"""Pass 7 #12 — NFO generation."""

from __future__ import annotations

from pathlib import Path

from kira.renamer import nfo


_META = {
    "overview": "A thief who steals corporate secrets.",
    "runtime": 148,
    "genres": ["Action", "Science Fiction"],
    "director": "Christopher Nolan",
    "studio": "Warner Bros.",
    "cast": ["Leonardo DiCaprio", "Joseph Gordon-Levitt"],
}


def test_movie_nfo_structure() -> None:
    out = nfo.build_movie_nfo("Inception", 2010, _META, "tmdb", "27205")
    assert out.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<movie>" in out and "</movie>" in out
    assert "<title>Inception</title>" in out
    assert "<year>2010</year>" in out
    assert "<runtime>148</runtime>" in out
    assert "<genre>Action</genre>" in out and "<genre>Science Fiction</genre>" in out
    assert "<director>Christopher Nolan</director>" in out
    assert "<actor><name>Leonardo DiCaprio</name></actor>" in out
    assert '<uniqueid type="tmdb" default="true">27205</uniqueid>' in out


def test_movie_nfo_omits_empty_fields() -> None:
    out = nfo.build_movie_nfo("Bare Movie", None, {}, None, None)
    assert "<title>Bare Movie</title>" in out
    assert "<year>" not in out          # year None → omitted
    assert "<runtime>" not in out
    assert "<uniqueid" not in out       # no provider id → omitted


def test_episode_nfo_structure() -> None:
    out = nfo.build_episode_nfo("The Rains of Castamere", 3, 9, {"overview": "Red wedding.", "runtime": 51})
    assert "<episodedetails>" in out
    assert "<title>The Rains of Castamere</title>" in out
    assert "<season>3</season>" in out
    assert "<episode>9</episode>" in out
    # The only plot Kira has is the SERIES overview - writing it as the episode
    # plot made every episode in Jellyfin show the identical blurb AND blocked
    # the server from scraping the real synopsis. Episode NFOs carry no <plot>.
    assert "<plot>" not in out
    assert "<runtime>51</runtime>" in out


def test_tvshow_nfo_uses_network_when_no_studio() -> None:
    out = nfo.build_tvshow_nfo("Game of Thrones", 2011, {"network": "HBO", "genres": ["Drama"]}, "tvdb", "121361")
    assert "<tvshow>" in out
    assert "<studio>HBO</studio>" in out
    assert '<uniqueid type="tvdb" default="true">121361</uniqueid>' in out


def test_xml_escaping() -> None:
    out = nfo.build_movie_nfo("Tom & Jerry <Special>", 2021, {"overview": "a < b & c > d"}, None, None)
    assert "Tom &amp; Jerry &lt;Special&gt;" in out
    assert "a &lt; b &amp; c &gt; d" in out
    assert "<Special>" not in out  # raw angle brackets never leak into markup


_RICH_MOVIE_META = {
    **_META,
    "original_country": "US",
    "collection_name": "The Dark Knight Collection",
    "poster_url": "https://img/p.jpg",
    "fanart_url": "https://img/f.jpg",
    "title_native": "インセプション",
}


def test_movie_nfo_enriched_fields() -> None:
    out = nfo.build_movie_nfo("Inception", 2010, _RICH_MOVIE_META, "tmdb", "27205")
    assert "<originaltitle>インセプション</originaltitle>" in out
    assert "<country>US</country>" in out
    assert "<set>" in out and "<name>The Dark Knight Collection</name>" in out
    assert "<thumb>https://img/p.jpg</thumb>" in out
    assert "<fanart>" in out and "<thumb>https://img/f.jpg</thumb>" in out


def test_movie_nfo_omits_new_fields_when_absent() -> None:
    out = nfo.build_movie_nfo("Bare", None, {}, None, None)
    for tag in ("<originaltitle>", "<country>", "<set>", "<thumb>", "<fanart>"):
        assert tag not in out


_TECH = {"codec": "x265", "quality": "1080p", "hdr": "HDR10",
         "channels": "5.1", "audio": ["TrueHD"], "duration": 1320}


def test_movie_nfo_streamdetails_full() -> None:
    out = nfo.build_movie_nfo("Inception", 2010, _META, "tmdb", "27205", tech=_TECH)
    assert "<fileinfo>" in out and "<streamdetails>" in out
    assert "<video>" in out and "<codec>hevc</codec>" in out        # x265 → hevc
    assert "<width>1920</width>" in out and "<height>1080</height>" in out  # 1080p → 1920×1080
    assert "<hdrtype>hdr10</hdrtype>" in out
    assert "<durationinseconds>1320</durationinseconds>" in out
    assert "<audio>" in out and "<channels>6</channels>" in out     # 5.1 → 6
    assert "<codec>TrueHD</codec>" in out                            # primary audio codec


def test_episode_nfo_streamdetails() -> None:
    out = nfo.build_episode_nfo("Ep", 1, 5, {}, tech=_TECH)
    assert "<streamdetails>" in out and "<height>1080</height>" in out


def test_streamdetails_absent_without_tech() -> None:
    out = nfo.build_movie_nfo("X", 2020, {}, None, None)            # no tech at all
    assert "<fileinfo>" not in out and "<streamdetails>" not in out


def test_streamdetails_partial_filename_only() -> None:
    # Only codec + quality (filename strip); no MediaInfo hdr/channels/audio.
    out = nfo.build_movie_nfo("X", 2020, {}, None, None, tech={"codec": "x264", "quality": "720p"})
    assert "<video>" in out and "<codec>h264</codec>" in out and "<height>720</height>" in out
    assert "<hdrtype>" not in out and "<audio>" not in out          # nothing HDR/audio known


def test_streamdetails_toggle_off() -> None:
    # 'streamdetails' not in the enabled set → block omitted even with tech.
    out = nfo.build_movie_nfo("X", 2020, {}, None, None, tech=_TECH, fields={"plot"})
    assert "<streamdetails>" not in out


def test_streamdetails_per_track_languages() -> None:
    # Dual-audio + multi-sub → one <audio>/<subtitle> per language. The first
    # audio track also carries the primary codec + channels.
    tech = {"codec": "x265", "quality": "1080p", "channels": "5.1", "audio": ["TrueHD"],
            "audio_langs": ["jpn", "eng"], "sub_langs": ["eng", "spa"]}
    out = nfo.build_movie_nfo("Anime Movie", 2020, {}, None, None, tech=tech)
    assert out.count("<audio>") == 2                  # two audio tracks
    assert "<language>jpn</language>" in out and "<language>eng</language>" in out
    assert out.count("<subtitle>") == 2               # two subtitle tracks
    assert "<language>spa</language>" in out
    assert "<channels>6</channels>" in out            # primary track's channels
    assert "<codec>TrueHD</codec>" in out             # primary track's audio codec (raw)


def test_streamdetails_languages_only_no_primary_codec() -> None:
    # Languages known but no primary audio codec/channels → still one <audio> per
    # language, just carrying <language>.
    out = nfo.build_movie_nfo("X", 2020, {}, None, None,
                              tech={"audio_langs": ["jpn"], "sub_langs": []})
    assert out.count("<audio>") == 1 and "<language>jpn</language>" in out
    assert "<subtitle>" not in out


def test_originaltitle_falls_back_to_alt_title() -> None:
    out = nfo.build_movie_nfo("X", 2020, {"alt_titles": ["Le X"]}, None, None)
    assert "<originaltitle>Le X</originaltitle>" in out


def test_episode_nfo_showtitle() -> None:
    out = nfo.build_episode_nfo("The Rains of Castamere", 3, 9,
                                {"overview": "x"}, series_name="Game of Thrones")
    assert "<showtitle>Game of Thrones</showtitle>" in out


def test_episode_nfo_omits_showtitle_when_absent() -> None:
    out = nfo.build_episode_nfo("E", 1, 1, {})
    assert "<showtitle>" not in out


def test_tvshow_nfo_enriched_fields() -> None:
    meta = {
        "network": "HBO", "genres": ["Drama"], "original_country": "US",
        "in_production": False, "poster_url": "https://img/p.jpg",
        "fanart_url": "https://img/f.jpg", "title_romaji": "Game of Thrones",
    }
    out = nfo.build_tvshow_nfo("Game of Thrones", 2011, meta, "tvdb", "121361")
    assert "<country>US</country>" in out
    assert "<status>Ended</status>" in out
    assert "<thumb>https://img/p.jpg</thumb>" in out
    assert "<fanart>" in out
    assert "<originaltitle>Game of Thrones</originaltitle>" in out


def test_tvshow_status_continuing() -> None:
    out = nfo.build_tvshow_nfo("X", 2020, {"in_production": True}, None, None)
    assert "<status>Continuing</status>" in out


def test_plan_movie() -> None:
    plan = nfo.plan_nfo_writes(Path("/lib/Movies/Inception (2010)/Inception (2010).mkv"), "movie")
    assert plan == {"movie": Path("/lib/Movies/Inception (2010)/Inception (2010).nfo")}


def test_plan_episode_includes_tvshow_at_series_root() -> None:
    target = Path("/lib/TV/Game of Thrones (2011)/Season 03/GoT - S03E09 - x.mkv")
    plan = nfo.plan_nfo_writes(target, "tv")
    assert plan["episode"] == target.with_suffix(".nfo")
    # tvshow.nfo walks up past the Season folder to the show root.
    assert plan["tvshow"] == Path("/lib/TV/Game of Thrones (2011)/tvshow.nfo")


def test_plan_music_is_empty() -> None:
    assert nfo.plan_nfo_writes(Path("/m/x.flac"), "music") == {}


def test_series_root_without_season_folder() -> None:
    # No Season folder → series root is the immediate parent.
    target = Path("/lib/Anime/One Piece/One Piece - 1000.mkv")
    assert nfo.series_root_for(target) == Path("/lib/Anime/One Piece")


# ── configurable fields ──────────────────────────────────────────────────────
def test_fields_none_equals_all_on() -> None:
    """Back-compat: fields=None must be identical to all fields enabled."""
    a = nfo.build_movie_nfo("X", 2020, _RICH_MOVIE_META, "tmdb", "1")
    b = nfo.build_movie_nfo("X", 2020, _RICH_MOVIE_META, "tmdb", "1",
                            fields=set(nfo.NFO_TOGGLEABLE))
    assert a == b


def test_fields_filter_keeps_only_enabled_plus_structural() -> None:
    out = nfo.build_movie_nfo("Inception", 2010, _RICH_MOVIE_META, "tmdb", "27205",
                              fields={"plot", "genres"})
    # enabled
    assert "<plot>" in out and "<genre>Action</genre>" in out
    # disabled
    for tag in ("<director>", "<actor>", "<country>", "<set>", "<thumb>",
                "<originaltitle>", "<runtime>", "<studio>"):
        assert tag not in out
    # structural identity is ALWAYS written
    assert "<title>Inception</title>" in out
    assert "<year>2010</year>" in out
    assert '<uniqueid type="tmdb" default="true">27205</uniqueid>' in out


def test_empty_fields_writes_only_structural() -> None:
    out = nfo.build_movie_nfo("X", 2020, _RICH_MOVIE_META, "tmdb", "1", fields=set())
    assert "<title>X</title>" in out and "<year>2020</year>" in out and "<uniqueid" in out
    for tag in ("<plot>", "<genre>", "<actor>", "<director>", "<country>",
                "<set>", "<thumb>", "<originaltitle>", "<runtime>"):
        assert tag not in out


def test_episode_fields_filter() -> None:
    out = nfo.build_episode_nfo("E", 1, 2, {"overview": "x", "runtime": 30},
                                series_name="Show", fields=set())
    assert "<title>E</title>" in out and "<season>1</season>" in out and "<episode>2</episode>" in out
    for tag in ("<showtitle>", "<plot>", "<runtime>"):
        assert tag not in out


def test_tvshow_fields_filter() -> None:
    meta = {"network": "HBO", "in_production": True, "original_country": "US", "genres": ["Drama"]}
    out = nfo.build_tvshow_nfo("GoT", 2011, meta, "tvdb", "1", fields={"genres"})
    assert "<genre>Drama</genre>" in out
    for tag in ("<studio>", "<country>", "<status>"):
        assert tag not in out


async def test_resolve_nfo_fields_reader() -> None:
    """The rename-time reader: unset → None (all on); a dict with `false` keys
    disables exactly those, absent keys default on."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import StaticPool

    from kira.api.rename import _resolve_nfo_fields
    from kira.models import Base, Setting

    engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                 connect_args={"check_same_thread": False}, poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as s:
        assert await _resolve_nfo_fields(s) is None  # unset → all on
        s.add(Setting(key="naming.nfo_fields", value={"cast": False, "artwork": False}))
        await s.commit()
    async with Session() as s:
        fields = await _resolve_nfo_fields(s)
        assert fields is not None
        assert "cast" not in fields and "artwork" not in fields  # disabled
        assert "plot" in fields and "genres" in fields            # absent → default on
    await engine.dispose()
