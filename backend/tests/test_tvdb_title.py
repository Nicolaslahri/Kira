"""TVDB title selection: prefer English over a Japan-origin anime's Japanese
master name. The real case: a Sonarr `{tvdb-442084}` folder → embedded-id match →
TVDB master record name is Japanese (悶えてよ、アダムくん), the English title lives
in translations/aliases. Kira used to ship the raw Japanese; now it prefers
English, exactly like it already did for the overview."""
from __future__ import annotations

from kira.providers.tvdb import _has_cjk, _pick_title


def test_has_cjk():
    assert _has_cjk("悶えてよ、アダムくん") is True   # kanji + hiragana + katakana
    assert _has_cjk("ヴァイオレット") is True          # katakana only
    assert _has_cjk("掙扎吧，亞當") is True            # han (chinese)
    assert _has_cjk("Modaete yo, Adam-kun") is False  # romaji
    assert _has_cjk("Adam's Sweet Agony (2024)") is False


def test_pick_title_prefers_english_name_translation():
    payload = {
        "name": "悶えてよ、アダムくん",
        "translations": {"nameTranslations": [
            {"language": "jpn", "name": "悶えてよ、アダムくん"},
            {"language": "eng", "name": "Modaete yo, Adam-kun"},
        ]},
    }
    assert _pick_title(payload, ["悶えてよ、アダムくん"]) == "Modaete yo, Adam-kun"


def test_pick_title_falls_back_to_latin_alias_when_no_eng_translation():
    # The exact Adam-kun shape: Japanese master, no eng NAME translation, English
    # only in aliases → use the first Latin-script alias, NOT the raw Japanese.
    payload = {"name": "悶えてよ、アダムくん", "translations": {}}
    aliases = ["悶えてよ、アダムくん", "Modaete yo, Adam-kun", "Writhe in Pain, Adam", "掙扎吧，亞當"]
    assert _pick_title(payload, aliases) == "Modaete yo, Adam-kun"


def test_pick_title_keeps_english_master_unchanged():
    # An English-origin show is untouched — no regression for the common case.
    payload = {"name": "Reacher", "translations": {}}
    assert _pick_title(payload, ["リーチャー"]) == "Reacher"


def test_pick_title_keeps_japanese_when_no_latin_anywhere():
    # No Latin option anywhere → keep the master name (never blank it out).
    payload = {"name": "悶えてよ、アダムくん", "translations": {}}
    assert _pick_title(payload, ["掙扎吧，亞當"]) == "悶えてよ、アダムくん"
