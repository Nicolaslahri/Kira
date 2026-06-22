# Kira Packs — authoring guide

A **pack** is a small JSON file you host anywhere (a GitHub raw URL, a gist, a
static site) that teaches Kira about a release its metadata providers can't
match — fan re-edits like **One Pace** / **Muhn Pace**, custom cuts, or any
re-numbered version of a show. You paste the URL into **Settings → Packs**, and
from then on the files those providers leave unmatched get organized, postered,
and (optionally) subtitled from your pack.

## How packs stay safe (read this first)

- **Packs only ever touch files Kira couldn't match.** A pack is consulted at
  exactly one moment: when a file is about to be marked *No match* because every
  provider (TMDB / AniDB / TVDB) came up empty. It can **never** change a title
  Kira already matched. (Kira deliberately refuses to auto-match fan-edits —
  "One Pace" is too close to "One Piece" — which is precisely the gap a pack
  fills.)
- **A pack must declare what it matches.** It needs at least one signal
  (`titles`, `release_groups`, or a `filename_regex`) so it can't claim "every
  file."
- **Override is opt-in and folder-locked.** By default a pack is *fallback* only.
  If you switch it to *override* (so it wins over a *wrong* provider match), Kira
  requires you to restrict it to one or more folders — a community regex can
  never be given library-wide override power.
- **Regexes are sanitized.** Catastrophic-backtracking patterns are rejected and
  every pattern is length-capped, so a hostile pack can't hang Kira.

## Minimal example

```json
{
  "kira_pack": 1,
  "id": "one-pace",
  "name": "One Pace",
  "media_type": "anime",
  "show": {
    "title": "One Pace",
    "aliases": ["One Pace (One Piece fan edit)"],
    "year": 1999,
    "poster_url": "https://example.com/one-pace/poster.jpg",
    "season_posters": {
      "1": "https://example.com/one-pace/season01-poster.jpg",
      "2": "https://example.com/one-pace/season02-poster.jpg"
    },
    "overview": "A fan re-edit of One Piece that trims filler."
  },
  "match": {
    "titles": ["One Pace"],
    "release_groups": ["One Pace"],
    "filename_regex": "(?i)\\bone[ ._-]?pace\\b"
  },
  "episodes": [
    {
      "season": 1,
      "episode": 1,
      "title": "Romance Dawn 01",
      "overview": "Luffy sets out to sea.",
      "match": {
        "crc32": "a1b2c3d4",
        "regex": "Romance Dawn 0?1\\b",
        "release": "[One Pace][Romance Dawn 01]",
        "arc": "Romance Dawn",
        "arc_episode": 1
      },
      "subs": [
        {
          "lang": "en",
          "url": "https://example.com/one-pace/rd01.en.ass",
          "format": "ass",
          "sync": "guaranteed",
          "hi": false,
          "forced": false
        }
      ]
    }
  ]
}
```

## Field reference

### Top level
| Field | Required | Notes |
|---|---|---|
| `kira_pack` | ✅ | Format version. Currently **1**. |
| `id` | ✅ | 1–64 chars of `A-Z a-z 0-9 . _ -`. Used (with a hash of your URL) to group the pack's episodes into one card. |
| `name` | ✅ | Display name. |
| `media_type` | – | `anime` (default), `tv`, or `movie`. |
| `show` | ✅ | See below. |
| `match` | ✅* | The show-level signature. At least one of `titles` / `release_groups` / `filename_regex` (or a `show.title`/`aliases`) is required. |
| `episodes` | – | The episode list. |

### `show`
`title` (required), `aliases` (list), `year`, `poster_url`, `season_posters`, `overview`.
- `poster_url` — the show poster; the fallback for every episode.
- `season_posters` — *optional* map of **season number (as a string) → poster URL**
  for per-season / per-arc cover art (the Jellyfin `seasonNN-poster.png` layout). An
  arc-based edit like One Pace has distinct art per arc, so list one per season and
  Kira gives each episode its own arc cover, falling back to `poster_url` for any
  season you don't list. Use `"0"` for specials.

Don't list the *original* show's exact name as an alias — keep aliases specific
to the edit.

### `match` (show gate)
- `titles` — names that should appear in the filename/parsed title.
- `release_groups` — exact release-group tags (e.g. `One Pace`).
- `filename_regex` — a regex tested against the filename. No nested quantifiers
  (`(a+)+`), ≤ 200 chars.

### `episodes[]`
`season` (default 1), `episode` (required), `absolute`, `title`, `overview`,
`match`, `subs`.

The renamer uses the pack's `season`/`episode` **verbatim** — lay your arcs out
as seasons however you like (e.g. Season 01 = Romance Dawn). Files land as
`One Pace/Season 01/One Pace - S01E01 - Romance Dawn 01 …`.

### `episodes[].match` — the claim ladder
Kira decides which episode a file is by trying these in order, strongest first:

1. **`crc32`** — the `[ABCD1234]` hash token in the filename. Mathematically
   exact; always prefer this when your release stamps a CRC.
2. **`regex`** — a regex against the filename.
3. **`release`** — a substring that must appear in the filename.
4. **`arc` + `arc_episode`** — the arc name plus its local number appear in the
   filename.
5. Bare **`episode`** / **`absolute`** number (weakest; used only after the show
   gate already matched).

### `episodes[].subs[]`
`lang` (2-letter ISO), `url`, `format` (`srt`/`ass`/`ssa`/`vtt`/`sub`),
`sync` (`guaranteed`/`likely`/`unknown`), `hi`, `forced`.

Pack subtitles are made for the exact cut, so `sync: "guaranteed"` is honest —
they outrank guessed subtitles from the normal providers and drop in as
sidecars next to the video.

## Installing & testing

1. **Settings → Packs → Add a pack**, paste the URL.
2. Kira validates it and shows a preview, including **"would rescue N of your
   unmatched files"** — a dry run against your current *No match* list.
3. Add it, then click **Re-run on unmatched files** to apply it to files already
   scanned (or just let your next scan pick it up).

Packs are re-fetched at most once every 24 hours; use **Refresh** to force it.
