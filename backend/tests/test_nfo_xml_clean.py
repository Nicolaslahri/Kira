"""NFO XML-1.0 sanitization (audit finding C6).

`saxutils.escape()` only handles & < > — a raw control byte scraped into a
title/plot/overview sails straight through and makes a strict reader
(Kodi/Emby/Jellyfin) reject the WHOLE NFO as malformed. Every value now routes
through `_esc`, which strips characters illegal in XML 1.0 before escaping.

Control chars are built with chr()/hex so the test source stays plain ASCII.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from kira.renamer.nfo import _xml_clean, build_episode_nfo, build_movie_nfo


def test_xml_clean_strips_illegal_keeps_legal():
    nul, ff, us = chr(0x00), chr(0x0C), chr(0x1F)
    assert _xml_clean("a" + nul + "b" + ff + "c" + us + "d") == "abcd"

    # TAB/LF/CR are the only C0 controls XML 1.0 permits — keep them.
    tab, lf, cr = chr(0x09), chr(0x0A), chr(0x0D)
    assert _xml_clean("keep" + tab + lf + cr + "end") == "keep" + tab + lf + cr + "end"

    # U+FFFE / U+FFFF noncharacters are illegal — strip.
    assert _xml_clean("x" + chr(0xFFFE) + "y" + chr(0xFFFF) + "z") == "xyz"

    # Valid BMP + astral characters (CJK, accents, emoji) must survive intact.
    astral = chr(0x1F3AC)  # 🎬
    assert _xml_clean("Frieren " + chr(0x846C) + " caf" + chr(0xE9) + " " + astral) == \
        "Frieren " + chr(0x846C) + " caf" + chr(0xE9) + " " + astral


def test_movie_nfo_with_control_chars_is_valid_xml():
    title = "Bad" + chr(0x07) + "Title" + chr(0x1F)        # BEL + US
    meta = {"overview": "Plot with " + chr(0x0C) + " form-feed and " + chr(0x00) + " NUL."}
    xml = build_movie_nfo(title, 2021, meta, provider="tmdb", provider_id="603")

    # A raw control char would make this raise ParseError. Encode to bytes so
    # ElementTree accepts the encoding="UTF-8" declaration in the header.
    root = ET.fromstring(xml.encode("utf-8"))
    assert root.tag == "movie"
    assert root.findtext("title") == "BadTitle"
    assert chr(0x0C) not in xml and chr(0x00) not in xml


def test_episode_nfo_with_control_chars_is_valid_xml():
    xml = build_episode_nfo(
        "Ep" + chr(0x00) + "Title", 1, 5,
        {"overview": "ok" + chr(0x1F)},
        series_name="Show" + chr(0x0C),
    )
    root = ET.fromstring(xml.encode("utf-8"))
    assert root.tag == "episodedetails"
    assert root.findtext("title") == "EpTitle"
    assert root.findtext("showtitle") == "Show"
