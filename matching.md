# Kira Matching — Gap Analysis vs the reference renamer

> Goal: close the gap between Kira's matcher and the reference renamer's, in an order
> where each step resolves a real class of matching bugs (not one filename
> at a time). Everything here is **clean-room** — we reimplement the reference renamer's
> *approach* and use the same *openly-licensed data sources*; we copy none
> of the reference renamer's GPL code.

Status legend: ✅ have · 🟡 partial · ❌ missing

---

## The one idea that explains most of the gap

the reference renamer does **"parse loosely → resolve against the episode database."** The
filename only has to get you to the right *show* and a rough number; the
provider's episode list resolves the exact episode, and the episode *title*
in the filename is a first-class matching signal.

Kira does **"parse strictly → trust the parse."** `parsed.episode` flows
straight through to the Match row and the rename, and the only validation is
an anime-only aggregate episode-count sanity veto. When the parser mis-reads
a filename (German scene tag, "Final Season Part 3-01", "Special 05"), the
garbage propagates: wrong AID, wrong episode number, orphaned rows, polluted
clusters (the Attack on Titan card mixing S1 + Final Season + a Special is
exactly this failure).

Almost every item below is a step from "trust the parse" toward "resolve
against the database."

---

## What Kira already has (the strong foundation)

- ✅ Cascading SxE patterns P1–P7: `S01E01`, ranges `S01E01-E03`, `1x05`,
  verbose "Season 1 Episode 5", `YEAR-NN`, absolute `- 47`, episode-only
  `E05`/`EP1156`, bracket-absolute `[1234]`, compressed `105`, `1 of 12`.
  (`parser/patterns.py:extract_sxe`)
- ✅ Tiered metric cascade (identity / similarity / corroboration) with
  veto support, replacing naive weighted-sum. (`matcher/cascade/`)
- ✅ Folder-identity metric + `series_key` clustering that incorporates the
  parent folder. (`matcher/cascade/metrics/folder_identity.py`,
  `api/scans.py:_compute_series_key`)
- ✅ Anime absolute→AID routing via franchise offset tables built from the
  AniDB relations chain + per-AID episode counts. (`engine.py:_route_anime_
  absolute_to_aid`, `providers/anidb.py:get_franchise_offsets`)
- ✅ Multi-cour routing using Fribb `(tvdb_id, season)` sibling AIDs.
  (`matcher/cour_routing.py`)
- ✅ Bipartite file↔episode pairing. (`matcher/bipartite.py`)
- ✅ 7-rung provider query ladder + per-type provider preference + transient
  retry. (`engine.py:_query_ladder`)
- ✅ Anime episode-count aggregate sanity veto. (`cascade/metrics/
  episode_count_sanity.py`)
- ✅ Absolute-numbered files (One Piece / AoT Final Season `- 60..89`) route into
  their AniDB cours via an `absolute_number→episode` bridge
  (`route_file_to_cour(abs_to_local=…)`), wired into scan + Re-identify +
  manual-pick. The sanity veto ABSTAINS on a **whole-franchise** aggregate
  (`aids_by_tvdb`) so a tail cour isn't vetoed against a series-absolute episode
  max; the stored `episode_number` is the cour-local index so the popup pairs.
  A known-anime TVDB id also folds into its AniDB franchise card
  (`compute_series_group_id` reverse-Fribb). (roadmap Pass V)
- ✅ Flat-umbrella local→absolute remap — the INVERSE of the bridge above. For a
  single flat AniDB AID that numbers the whole long-runner absolutely (One Piece
  69, `tvdb_season` None), a TVDB-season-LOCAL file (`S23E04`, paired to the
  Elbaf cour's local ep 4) stores the ABSOLUTE `episode_number` (1159) so dupes
  line up. `remap_umbrella_local_to_absolute(local_to_abs=…)`, wired into scan +
  Re-identify + manual-pick; no-ops for per-season AIDs / normal TV / absolute-
  named files. (roadmap Pass V — V8)

This is already past the reference renamer on a few axes (cour graceful degradation under
AniDB ban, franchise grouping UI). The gaps are specific.

---

## Gap list — in implementation order

Order rationale at the bottom. Phases 1–3 are quick parser wins that unblock
today's visibly-broken files; Phase 4 is the architectural keystone; Phases
5–6 kill the deep anime long-tail; Phases 7–10 are progressive robustness.

---

### Phase 1 — Named-season / part / dash-episode / bare-E parsing ✅ Shipped

**Shipped:** `patterns.py` gains PA (`Season N-MM`), PB (`Part/Cour N-MM` →
captures `cour` on a new `SxEMatch.cour` field), inserted before P4 so the
match span covers the whole token and the title cuts cleanly. `parser.py`
threads `cour` (filename PB > parent path > trailing-title `Part N`) and adds
a `ParsedFile.named_season` hint ("final"). Verified end-to-end: `Season 2-06`
→ season=2/ep=6 (was ep=None); `Final Season Part 3 - 01` → ep=1/cour=3 (was
junk title). 9 new tests.

**the reference renamer:** recognizes "Final Season", "Part 2/3", "Season 2-06" dash-episode,
bare `E07`, via its `SeasonEpisodeMatcher` + the series-name normalizer.

**Kira today:** none of these parse. `Final Season Part 3-01` → title becomes
the literal junk "Shingeki no Kyojin-The Final Season Part 3-01 [ ]",
season=1, episode=None. `Season 2-06` → episode=None.

**Build:**
- New patterns in `parser/patterns.py`:
  - `Season\s+(\d+)[-\s]+(\d+)` → (season, episode) for "Season 2-06"
  - `(?:Final\s+Season|Part)\s+(\d+)[-\s]+(\d+)` → part + episode
  - bare `E(\d+)` with no `S` already caught by P5; add season inference
- Named-season keyword map: "Final Season" → a season-ordinal hint the
  matcher already understands (`anime_season_ordinal.py`), "Part N" → cour N.

**Fixes:** AoT `Final Season E07`, `Final Season Part 3-01/02`, `Season 2-06`
mis-parses → no longer collapse into the Season 1 card.

**Effort:** ~half-day. Self-contained + table-driven tests per pattern.

---

### Phase 2 — Special / OVA detection + S00 routing ✅ Shipped

**Shipped:** `patterns.py` `_PSPECIAL_NUM` / `_PSPECIAL_BARE` route
`Special NN` / `OVA` / `OAV` / `ONA` / `SP01` → season 0, with a
`_has_title_before` guard so the movie "Special 26" / "Special Edition" /
"Specialist" don't false-trigger. `anidb.get_episodes(include_specials=)`
returns type=2 specials tagged season 0 (regular-count cache kept clean);
the `/series` endpoint opts in when season 0 is requested. `templates.py`
routes season-0 files to a `Specials/` folder (Plex/Jellyfin convention).
7 new tests. Verified: `Bleach Special 05` → S0E5 → `…/Specials/…`.

**the reference renamer:** routes "Special", "OVA", "SP01", "S00E01" to season 0; the
anime-lists data maps specials explicitly.

**Kira today:** no S00 concept. Worse — AniDB `get_episodes` actively
*drops* specials/OP/ED (`epno type != "1"`), so a `Special 05` file can't
even find a title to match. It lands on the base AID with episode=None.

**Build:**
- Parser: detect `Special\s*(\d+)`, `OVA`, `\bSP(\d+)\b`, `S00E\d+` →
  `season=0, episode=N`.
- `anidb.get_episodes`: optionally include `type="2"` (specials) when the
  caller asks, tagged season=0.
- Rename templates: `Specials/` folder when season==0 (the reference renamer/Plex/Jellyfin
  convention).

**Fixes:** AoT `Special 05`; every fansub OVA/special currently orphaned.

**Effort:** ~1 day (parser + provider flag + template conditional).

---

### Phase 3 — Title cleanup: alt-name brackets + residue ✅ Shipped

**Shipped:** `parser._clean_title_brackets` (run after the SxE/year cut so it
can't shift a span) drops (a) empty/whitespace brackets, (b) release-flavor
noise the format-stripper's space-bracket carve-out keeps (`[Dual Audio]`,
`[Multi-Subs]`, `[BD]`…), and (c) same-language echoes (`Bleach [BLEACH]`,
trigram ≥ 0.65 vs the title remainder). Legit subtitles (`[Unlimited Blade
Works]`) survive. Cross-language alt-names (`[Shingeki no Kyojin]`) are
deliberately KEPT — Phase 11's folder lock handles the clustering side, since
parse-time can't tell an alt-name from a real subtitle. 4 new tests.

**the reference renamer:** strips bracketed alt-names, scene tags, and normalizes against a
huge precompiled known-series-name index.

**Kira today:** strips leading `[GROUP]`, trailing `-GROUP`, CRC noise, and
*technical* brackets — but **keeps multi-word brackets** as title (carve-out
for legit titles like `[Unlimited Blade Works]`). So `[Shingeki no Kyojin]`
stays glued into "Attack on Titan [Shingeki no Kyojin] Season 2-06", and
empty `[ ]` residue survives.

**Build:**
- After SxE extraction, run a second title-clean pass: drop a bracket group
  if its content trigram-matches an alias of the *already-extracted* primary
  title (i.e. it's a redundant alt-name, not a subtitle).
- Collapse empty/whitespace-only brackets `[ ]`, `( )`.
- (Stretch) seed a small known-alias index from the AniDB title dump we
  already load — use it to normalize before search.

**Fixes:** the `[Shingeki no Kyojin]` / `[BD ]` / `[ ]` junk that drags
similarity scores down and pollutes `series_key`.

**Effort:** ~half-day.

---

### Phase 4 — Episode-list validation gate (the keystone) ✅ Shipped (scoped)

**Shipped:** pure coverage helpers in `matcher/episode_validation.py`
(`episode_exists`, `coverage`, `should_promote`) + an async gate
`_validate_and_rerank_by_episodes` wired into `_match_cluster` and
`_match_singleton`. The gate fetches the top candidate's episode list and,
for a **western-TV** cluster whose TVDB/TMDB incumbent covers < 34% of the
cluster's episodes, probes alternates and promotes one that covers ≥ 67% (and
beats the incumbent by ≥ 34%). Deliberately scoped OUT of the anime / AniDB /
cour paths — there `EpisodeCountSanityMetric` (count veto + Fribb-sibling
aggregate), cour routing, the absolute→AID reroute, and bipartite pairing
already do the resolution, and per-cour coverage is legitimately partial. So
this fills the documented gap (western TV had *no* per-file existence check)
without destabilising the heavily-autopsied anime path. Ban-safe (reuses the
cross-ref-preferring fetch + circuit breaker). 5 new tests.

> Note: episode_number is still written from the parse (not overwritten with
> a resolved local number) — that's a deliberate, load-bearing choice
> documented in `_match_cluster` ("preserve the user's filename intent" for
> rename output); the AID-side resolution already happens via cour routing.

**the reference renamer:** after the series is matched, it pulls the episode list and
*resolves* the file against it — confirming the (season, episode) exists,
re-deriving via absolute/offset if not, and rejecting/re-ranking the series
match when the episode genuinely can't exist.

**Kira today:** trusts `parsed.episode`. For non-anime there's **no**
per-file existence check at all; an out-of-range S04E17 against a 12-episode
AniDB AID just produces episode_title=None and an orphan row, while the
*series* match stands. The number even flows into the rename out of range.

**Build:**
- In `_match_cluster` / `_match_singleton`, after the top series match:
  1. fetch the episode list (already cached for the title-lookup path),
  2. check `(season, episode)` — and the absolute/cour-derived candidates —
     exist,
  3. if none exist, attempt re-resolution (absolute→season offset, cour
     table) BEFORE accepting,
  4. if still impossible, drop the candidate's tier or veto it so a
     better-fitting series can win.
- Write the *resolved* episode number to the Match row, not the raw parse,
  for anime cross-refs (TVDB-S04E17 → AniDB-E01). This also removes the need
  for the frontend offset-pairing band-aid in `CoverPopup`.

**Fixes:** the whole "matched the right show, wrong/again-orphaned episode"
class. The AoT S04E17–E22 → AniDB E01–E06 mapping happens *server-side*,
correctly, instead of via the UI fallback.

**Effort:** ~2 days. This is the architectural shift; do it carefully with
the existing cluster path.

---

### Phase 5 — anime-lists per-episode mappings (the long-tail killer) ✅ Shipped (data module; integration staged)

**Shipped:** `providers/anime_lists.py` ingests ScudLee's `anime-lists` XML
(lazy download + 24h cache + corruption-safe, mirroring the AniDB title-dump
pattern; reimplemented parser, no the reference renamer code). It parses the three
`<mapping>` shapes — flat `defaulttvdbseason` + `episodeoffset`, `<mapping
start end offset>` ranges, and explicit `;anidb-tvdb;` pairs — into a
TVDB-keyed index, and exposes `resolve_tvdb_episode(index, tvdb_id, season,
episode) → (anidb_id, anidb_episode)` plus the async `resolve_tvdb_to_anidb`
front door. 7 fixture tests cover all three shapes + the multi-AID-per-TVDB,
unknown-id, and malformed-XML paths.

**Staged:** the deep wire-in (using the per-episode map inside cour routing /
the Phase 4 resolution gate) is deliberately a focused follow-up — it touches
the most-autopsied matcher code and needs real-library validation, not a
tail-of-session edit. The hard, reusable 80% (the data pipeline + resolver)
is done and tested; flipping the matcher to consult it is a small, isolated
next step.

**the reference renamer:** uses ScudLee's `anime-lists` XML — AID ↔ TVDB with per-episode
`<mapping>` blocks: start-episode offsets, mid-season special inserts,
non-contiguous ranges. This is *the* reason the reference renamer nails anime.

**Kira today:** loads only the **flat** Fribb `season` integer + cross-ref
IDs (`anime_mappings.py:_load_and_prune` discards the `<mapping>` blocks).
Routing relies on summed episode counts + bare-AID ordering — works for
contiguous franchises (One Piece, MHA) but breaks on offsets, reboots,
specials-interleaved numbering.

**Build:**
- Ingest the anime-lists XML (or a JSON derivative) including the
  `<mapping>` offset data — it's openly licensed; not the reference renamer's code.
- New resolver: given (tvdb_id, season, episode) → exact AniDB (aid, local
  episode) using the per-episode map, with the flat-season path as fallback.
- Plug into Phase 4's resolution step.

**Fixes:** offset-start cours, mid-season specials, reboot numbering — the
deep anime cases that regex can never solve. Makes anime matching *data-
driven* like the reference renamer's.

**Effort:** ~2–3 days (parser + cache + resolver + wiring). Highest-leverage
anime item.

---

### Phase 6 — Episode-title similarity matching ✅ Shipped

**Shipped:** parser extracts `ParsedFile.episode_title_guess` — the text run
after the SxE marker ("Game of Thrones - 3x09 - **The Rains of Castamere**"),
rejecting numbers / release tags / junk words. `bipartite.py` gains a Pass 4:
for files still unpaired after the number passes, it trigram-matches the
guess against the remaining episodes' titles and claims the best (≥ 0.6),
tagging `matched_via="title"`. Resolves SxE-less / wrong-numbered files by
name. Additive — only touches files the number passes left orphaned. 5 tests.
(The "boost the SERIES match by episode title" half is deferred: the cascade
is pure/in-memory and doesn't hold the provider episode list; the
resolution-side win is the high-value part and ships here.)

**the reference renamer:** matches the *episode title* in the filename against DB episode
titles — both as a scoring signal and as a fallback when there's no SxE
("Show - The Big Fight.mkv").

**Kira today:** never compares the filename's episode title to DB titles.
Bipartite pairing keys purely on numbers; `_lookup_episode_title` is
number→title only.

**Build:**
- Extract a candidate episode-title substring from the filename (text after
  the SxE / between known delimiters).
- Add an `EpisodeTitleMetric` to the cascade: trigram (reusing
  `similarity.py`) of filename-title vs each DB episode title; boosts the
  series match AND can resolve the episode when the number is missing/ambiguous.

**Fixes:** SxE-less files, mislabeled-number files, disambiguating two same-
titled series by which one has the matching episode title.

**Effort:** ~1–1.5 days.

---

### Phase 7 — Multi-metric similarity cascade ✅ Shipped

**Shipped:** pure helpers in `matcher/text_distance.py` (`levenshtein_ratio`,
`lcs_ratio`, `numeric_similarity`) + three tier-2 metrics in
`cascade/metrics/text_metrics.py` (Levenshtein, LCS, NumericDistance),
registered in the cascade. The runner takes the MAX across tier-2, so these
only RAISE a candidate's score when they catch a similarity trigram missed
(typos, word-order, numeric titles like "86") — never double-count. Each has
a 0.5 floor; NumericDistance skips the min-length guard so 2-char numeric
titles ("86") work. 12 tests.

**the reference renamer:** combines ~10 metrics (Levenshtein, longest-common-subsequence,
substring, numeric distance, name-match) in `EpisodeMetrics`.

**Kira today:** main path is single trigram (Sørensen-Dice). Cascade adds
substring + cluster-signal + folder, but no Levenshtein/LCS/numeric.

**Build:** add `LevenshteinMetric`, `LCSMetric`, `NumericDistanceMetric` to
the tier-2 similarity band; tune weights against a fixture set of real
filenames.

**Fixes:** edge series disambiguation (typos, word-order, numeric-heavy
titles like "86" / "91 Days" / "3×3 Eyes").

**Effort:** ~1 day. Each metric is a small pure function.

---

### Phase 8 — Franchise-offset ordering by air-date/Fribb-season ✅ Shipped

**Shipped:** `get_franchise_offsets` (anidb.py) flipped from the KI-9 observer
mode — it now builds the cumulative offset table in **Fribb-season order**
instead of bare-AID order, but ONLY when every franchise member has a known
Fribb season (high-confidence). If any member lacks a season it falls back to
bare-AID order rather than shoving unmapped members to the end via the 9999
sentinel — so this strictly improves fully-mapped franchises (reboots /
out-of-order AID registration route absolute episodes correctly) and never
regresses partially-mapped ones. The divergence log stays (now records which
order was applied).

**Kira today:** `get_franchise_offsets` orders AIDs by **bare AID number**
(KI-9). Fribb-season ordering is computed but only *logged* (observer mode).
Reboots / side-stories registered out of order mis-route absolute episodes.

**Build:** flip the observer-mode KI-9 code to actually sort by Fribb season
(then air-date tiebreak), per the bake-in evidence already being logged.

**Fixes:** reboot franchises, out-of-order AID registration (the documented
KI-9 risk).

**Effort:** ~1–2 hours (the code exists; flip the switch after reviewing the
divergence logs).

---

### Phase 9 — Date-based episode matching ✅ Shipped

**Shipped:** the parser detects a full `YYYY[sep]MM[sep]DD` (zero-padded
month/day, on the raw filename) → `ParsedFile.air_date` (ISO), but only when
there's no SxE (otherwise the date is just a release tag). It cuts the date
from the title and seeds the year so the provider search is clean + anchored,
and leans an otherwise-unknown type to TV. The bipartite pairing gains an
air-date pass (the validation gate now returns rich episode dicts incl.
air_date) that pairs daily/talk/news files against the provider's `air_date`
field. 4 tests.

**the reference renamer:** `DateMatcher` — "Show 2020.01.15.mkv" → match by air date.

**Kira today:** absent. `YEAR-NN` is treated as year+episode; a third date
segment is explicitly rejected.

**Build:** detect `YYYY[.-]MM[.-]DD`; when present and the series is daily
(no clean SxE), match against the provider's air-date field.

**Fixes:** daily shows, talk shows, news, some sports — the date-named tail.

**Effort:** ~1 day.

---

### Phase 10 — Media-type + token-table robustness ✅ Shipped (group list)

**Shipped:** the `_FANSUB_GROUPS` set grew from ~13 to ~60 curated anime
release/sub groups (Ember, Moozzi2, GJM, Yameii, Beatrice-raws, Ohys-raws,
Chihiro, …) so a `[Group]` / `-Group` tag is far more reliably recognized as
anime. Curated to anime-specific groups so a non-anime release with a
coincidental tag isn't misclassified. 1 test. (Moving the token tables to a
user-editable data file + Settings UI is the remaining demand-driven half.)

**Kira today:** media-type detection works but the fansub-group list (~13
entries) and source/codec token tables are tiny hardcoded sets; no
studio/genre signal.

**Build:** expand token tables (data-file, not code); add a "known anime
release groups" list; optionally use provider genre ("Animation" + JP origin
→ anime) as a media-type tiebreak.

**Fixes:** mis-typed media (a fansub release with an unknown group tag landing
as TV instead of anime).

**Effort:** ~half-day, demand-driven.

---

## Ordering rationale

| Phase | Why here | Fixes today's visible breakage? |
|------:|----------|:-------------------------------:|
| 1–3 | Quick, self-contained parser wins. Unblock the AoT card + every odd fansub name **now**. | ✅ |
| 4 | The keystone. Converts "trust the parse" → "resolve against DB". Everything downstream gets more reliable. | ✅ (server-side AoT fix) |
| 5 | Highest-leverage *anime* item; with Phase 4 it kills the deep anime long-tail as a class. | ✅ |
| 6–7 | Better disambiguation; depends on having the validation gate (4) to act on. | — |
| 8 | Cheap correctness fix; flip existing observer-mode code. | — |
| 9–10 | Long-tail (daily shows, exotic groups); demand-driven. | — |

**If you only do three:** Phases 1+2+4. That fixes the Attack on Titan card,
stops specials being dropped, and makes the matcher stop trusting bad parses
— which is 80% of the felt pain.

**The "resolves everything" pair:** Phase 4 (validation gate) + Phase 5
(anime-lists per-episode mappings). Together they move Kira to the reference renamer's
actual architecture for the cases that matter most in an anime-heavy library.

---

---

# Round 2 — deeper the reference renamer pass: additional gaps

A second, wider look at the reference renamer's matching subsystem (its `MediaDetection`,
`ReleaseInfo`, `EpisodeMetrics`, `SeriesNameMatcher`, the bundled index
resources, and the documented `--order`/`--def` behaviors). These are the
capabilities NOT covered by Phases 1–10 above. Same structure; reimplemented
in our own code, using open data where data is involved.

The headline find is **Phase 11** — it's the single thing that would have
*directly* prevented the Attack on Titan card mixing seasons + a special.

---

### Phase 11 — Batch / folder-level series locking ✅ Shipped  ← fixes AoT directly

**Shipped:** pure majority-vote logic in `matcher/folder_lock.py`
(`compute_relocks`) + `_apply_folder_series_lock` in `scans.py`, run after
Phase-1 parse and before clustering. Within each leaf folder, a strict
majority (> 50% of keyed TV/anime files, ≥ 2 agreeing) of
`(media_type, title, disambig)` relocks the outliers — title + disambig
unified, **each file's own season preserved** so a series-root folder with
S1+S2 never collapses into one cluster, and a 2-vs-2 folder is never
force-merged. Null-key (parser-failed-title) files get pulled in too, with
their season recovered from `parsed_data`. 8 new tests. This is the safety
net that catches whatever residue Phases 1-3 don't: the mangled outlier
rejoins its season's cluster and inherits the correct match.

**the reference renamer:** determines the series ONCE per folder/batch, then forces every
file in that folder to that series. A folder named `Attack on Titan\Season 4`
with 12 files locks all 12 to AoT S4 — a single weird filename can't escape
into another show.

**Kira today:** clusters by `series_key`, which is *derived from each file's
parsed title*. When the parser mangles one filename ("…Final Season Part
3-01 [ ]"), that file gets a DIFFERENT `series_key` and splinters into its
own cluster / matches the base AID. There is no "this folder IS this show, no
exceptions" lock. This is exactly why your AoT files scattered across S1 /
Final Season / a Special card.

**Build:**
- Before per-file `series_key`, compute a **folder-level identity**: for each
  leaf folder, take the majority `series_group_id` / matched AID across its
  files (the confident ones), and the folder name.
- Lock outlier files in that folder to the majority series (still let their
  *episode* number resolve per Phase 4). Surface a small "locked to folder
  series" note so it's not silent.
- Guard: only lock when ≥N files in the folder agree, so a genuinely-mixed
  folder isn't force-merged.

**Fixes:** the entire "one bad filename pollutes / escapes the cluster" class.
AoT misfits get pulled back into the right season card by their folder.

**Effort:** ~1–1.5 days. High leverage — pair it with Phase 4.

---

### Phase 12 — Common-word-sequence series-name extraction across a batch ✅ Shipped

**Shipped:** `_query_ladder` (engine.py) now PREPENDS the cluster signal —
the longest token run shared across every filename in the batch, computed by
`compute_cluster_signal` and stashed on the rep parsed object by
`_match_cluster` — as the FIRST search query (with + without year), ahead of
any single file's parsed title. So a batch where one filename is mangled
still searches the robust common name. Falls through to the per-file title
rungs if the signal returns nothing; singletons (no signal) are unaffected.
3 tests. (The signal was already a cascade SCORING metric; this makes it the
QUERY source too, completing the the reference renamer getSeriesName behavior.)

**the reference renamer:** `getSeriesName` derives the name from the **longest common token
sequence** across sibling files, not just one filename. 24 files sharing
"Attack on Titan" → that's the series, regardless of per-file junk.

**Kira today:** `cluster_signal.py` computes a word-sequence but uses it only
as a *scoring* metric, not as the primary series-name *source*. Series name
still comes from each file's parsed title.

**Build:** when a cluster forms, extract the longest common normalized token
run across its filenames; use it as the search query (and as a high-tier
identity signal) instead of any single file's mangled title.

**Fixes:** robust series name even when half the files have odd tags; feeds
Phase 11.

**Effort:** ~1 day (the LCS extractor already exists in `cluster_signal.py`).

---

### Phase 13 — Acronym + known-name index ✅ Shipped (acronym metric + offline AniDB index)

> **Update:** the stretch "local name→id index" is done for AniDB (M2:
> `_name_index` + `_acronym_index` from the title dump, consulted offline before
> any network call). The TVDB/TMDB sliver is intentionally not built — no title
> dump to index, and xattr persistence (below) covers cross-rescan recall better.
> See roadmap #20 for the full rationale.


**Shipped:** `cascade/metrics/acronym.py` `AcronymMetric` (tier 2). Two paths:
(1) a curated fan-acronym map (AoT, JJK, SnK, FMA, MHA, LOTR, GoT…) →
trigram against the canonical expansion; (2) generated initialisms of each
candidate (both all-words "attack on titan"→"aot" and without-stopwords
forms), matched against the parsed token. Only fires when the parsed title is
a single short acronym-shaped token, so a file literally named `JJK - 05`
resolves to "Jujutsu Kaisen". 6 tests. (Stretch local name→id prefilter
index deferred — demand-driven; the acronym metric is the high-value half.)

**the reference renamer:** bundles compressed name indices (series/anime/movie) for fast
local prefilter + name normalization, and generates/matches **acronyms**:
"AoT" → "Attack on Titan", "JJK" → "Jujutsu Kaisen", "SnK" → "Shingeki no
Kyojin", "FMA" → "Fullmetal Alchemist".

**Kira today:** has the AniDB title dump in memory (good base) but no acronym
generation and no TVDB/TMDB/movie name index. The "aot4"/"aot5" you saw were
provider-supplied *aliases*, not Kira-generated acronyms — Kira can't match a
file literally named `[AoT] …` to "Attack on Titan".

**Build:**
- Acronym metric: generate initialisms from candidate titles (first letters
  of significant words, plus known fan-acronym list) and match against the
  parsed title token.
- (Stretch) build a local name→id index from the AniDB dump we already load,
  for instant prefilter before the network search.

**Fixes:** acronym-named releases; faster/offline-resilient prefiltering.

**Effort:** ~1 day (acronym metric); +1 day for the index.

---

### Phase 14 — Embedded provider-ID extraction ✅ Shipped

**Shipped:** the parser scans the RAW filename + folder (before stripping eats
the braces) for `{tmdb-27205}` / `[tvdb-81797]` / `{anidb-9541}` / bare
`tt1375666` → `ParsedFile.provider_ids`. The matcher resolves directly by ID
(confidence 1.0, skips the title-search ladder entirely) for tmdb/tvdb/anidb,
running BEFORE the title guard so an ID-tagged file with a junk/empty title
still resolves. Canonical title/year/poster come from a get-by-id details
call (TMDB/TVDB detail methods now surface those fields; AniDB from the
in-memory title dump). imdb IDs are recorded but need a `/find` call we don't
do yet. 7 tests.

**the reference renamer:** if the filename/folder carries an explicit ID — `tt1375666`
(IMDB), `{tmdb-27205}`, `{tvdb-81797}`, `[anidb-9541]` — it matches that ID
DIRECTLY and skips search entirely. Zero ambiguity.

**Kira today:** ignores embedded IDs; always goes through title search.

**Build:** pre-scan filename + folder for `tt\d+`, `{tmdb-\d+}`, `{tvdb-\d+}`,
`[anidb-\d+]`; when present, resolve by ID and bypass the query ladder.

**Fixes:** anything the user (or a prior renamer) already tagged with IDs —
instant, perfect matches; great for re-scanning a Kira-renamed library.

**Effort:** ~half-day.

---

### Phase 15 — Thorough match normalization ✅ Shipped

**Shipped:** `similarity.normalize()` (already did diacritics, articles,
`&`→`and`, apostrophe/period folding) now also folds, per token: multi-letter
roman numerals (`II`→`2` … `XX`→`20` — single letters i/v/x/l/c/d/m
deliberately excluded to avoid "I Robot"/"X-Men"/"V for Vendetta"
collisions), ordinal words (`second`→`2` … `twentieth`→`20`), and numeric
ordinals (`2nd`→`2`). Applied to BOTH sides of every comparison, so identical
titles still match while `Spice & Wolf II` ≡ `Spice and Wolf 2nd` ≡ `Spice
and Wolf Second` ≡ `Spice and Wolf 2`. 8 tests.

**the reference renamer:** normalization folds far more than diacritics — `&`→`and`, roman
numerals (`II`→`2`, `III`→`3`), ordinal words (`Second`→`2`), apostrophe/quote
variants, `the`/`a` article handling, punctuation classes.

**Kira today:** `similarity.normalize` folds diacritics + keeps CJK/Cyrillic,
lowercases, strips articles. Missing `&`→`and`, roman numerals, ordinal words,
ampersand/symbol classes. (Anime ordinals handled separately at match time.)

**Build:** extend `normalize()` with `&`→`and`, roman-numeral↔arabic, ordinal-
word↔number, symbol folding. Applies to BOTH sides of every comparison.

**Fixes:** "Show & Co" vs "Show and Co", "Season II" vs "Season 2", "K-On!!"
vs "K-On", "JoJo's" apostrophe variants.

**Effort:** ~half-day. Pure function, big disambiguation payoff.

---

### Phase 16 — MediaInfo integration (real file metadata) ✅ Shipped

**Shipped:** `parser/mediainfo.py` reads true resolution/codec/HDR from the
file via `pymediainfo`, with first-class graceful degradation — if the native
`libmediainfo` isn't installed, every entry point returns None and the
pipeline behaves exactly as filename-only (so it's safe even though this env
doesn't have the lib). Pure mapping helpers (`height_to_quality`,
`normalize_codec`, `hdr_label`, `enrich_parsed`) are unit-tested (7 tests).
Wired into the scan worker as a FALLBACK — fires only when the FILENAME
yielded no quality (bounds the per-file I/O), runs off the event loop, fills
only missing fields (never overrides a filename tag). Toggle:
`parsing.read_mediainfo` (default on; no-op without the lib).

**the reference renamer:** reads actual resolution/codec/audio/HDR/bit-depth/duration from
the file via libmediainfo — so quality tags are *true* even when the filename
lies or omits them, and duration cross-checks runtime for confidence.

**Kira today:** parses quality/codec/source from the FILENAME only
(`format_stripper`). A mislabeled or tag-less file shows wrong/no chips, and
duration is never used as a match signal.

**Build:** `pymediainfo` (already on the original dependency list) per file,
cached by path; populate true resolution/codec/audio/HDR/bitdepth/duration;
optionally use duration vs episode runtime as a corroboration metric.

**Fixes:** accurate quality chips, dedup ranking, and a runtime-based
confidence signal. (Also feeds template tokens in the rename roadmap.)

**Effort:** ~1 day. Watch the per-file I/O cost — thread it + cache.

---

### Phase 17 — Scene-rules / release-info dataset ✅ Shipped (groups + base tables externalized)

> **Update (roadmap #18 done):** the base source/codec/resolution/audio/subtitle/
> edition/hdr/bit-depth/release-flag tables now load from a shipped
> `parser/release_tokens.json` (in-code literals remain the fallback); user
> `scene_rules.json` extras still fold on top. The `sources`/`codecs`/… keys are
> now fully wired — no longer staged.


**Shipped:** `parser/scene_rules.py` reads an OPTIONAL user JSON
(`$KIRA_SCENE_RULES` or `<backend>/.cache/scene-rules.json`) so power users
can teach Kira release groups it doesn't ship — `{"fansub_groups": [...]}` —
without editing source. Merged into `_FANSUB_GROUPS` at import; absent file →
empty extras → in-code set unchanged; malformed file is ignored, never breaks
parsing. 4 tests. (The `sources`/`codecs` keys are reserved — externalizing
format_stripper's precompiled token tables is the remaining demand-driven
half; the curated in-code set already grew ~13→~60 in Phase 10.)

**the reference renamer:** ships `ReleaseInfo` — thousands of curated release-group names,
source/format tokens, "clutter" patterns, stopwords, language patterns —
refreshed independently of releases.

**Kira today:** ~13 hardcoded fansub groups + small hardcoded source/codec
tables in `format_stripper.py`.

**Build:** move token tables to a data file (JSON/YAML), seed from an open
scene-rules list, allow user extension (ties into the planned Settings →
Advanced → format tokens). Bigger group list improves both stripping AND
anime media-type detection.

**Fixes:** unknown-group releases mis-typed as TV instead of anime; junk
tokens leaking into titles.

**Effort:** ~1 day (mostly data + loader).

---

### Phase 18 — TVDB episode ordering schemes (aired / DVD / absolute) ✅ Shipped

**Shipped:** `tvdb.get_episodes` gained an `order` param (default/dvd/absolute/
official/…) → `/series/{id}/episodes/{order}/eng`; the shared provider
signature carries it (no-op for TMDB/AniDB). `_match_cluster` now does a
**DVD-order retry**: for a TVDB-matched anime cluster with files the
aired-order bipartite pass left orphaned, it fetches DVD order once and
re-pairs only those files (bounded, best-effort). NOTE: absolute-numbered
anime was already handled — the default-order TVDB response carries
`absoluteNumber` and bipartite Pass 2 pairs on it — so DVD order is the real
gap this closes.

**the reference renamer:** lets you pick the episode order — TVDB exposes **aired**, **DVD**,
and **absolute** orderings; anime frequently needs DVD or absolute order to
line up with fansub numbering.

**Kira today:** uses TVDB's default (aired) order only. A file numbered in DVD
order against an aired-order episode list mis-pairs.

**Build:** fetch the alternate ordering from TVDB's `/series/{id}/episodes/
{order}`; for anime, try absolute/DVD order when aired-order pairing fails
(slots into Phase 4's resolution step).

**Fixes:** the subset of anime where fansub numbering follows DVD/absolute,
not aired order.

**Effort:** ~1 day.

---

### Phase 19 — Sample / extras / trailer exclusion ✅ Shipped

**Shipped:** the scanner skips scene samples / trailers / proofs and files
under extras folders (Featurettes/, Behind The Scenes/, Deleted Scenes/,
Trailers/, …) at walk time, so a 30 MB `sample.mkv` never becomes a MediaFile
row and can't be renamed as the real episode/movie. Unambiguous stems
(`sample`, `trailer`, `proof`, `*-sample`) are dropped regardless of size; a
`sample`/`trailer` token elsewhere in the name is gated on size (< 300 MB) so
a legit title ("Free Sample", "Trailer Park Boys") survives. "Specials" is
NOT treated as extras (Phase 2 routes specials to season 0). 5 tests. (The
opt-in "show samples" role/pill is deferred — these are silently excluded,
the reference renamer's default.)

**the reference renamer:** excludes `sample`, `trailer`, `proof`, `Extras/`, `Featurettes/`,
`Behind The Scenes/` from matching, so a 30 MB sample never matches as the
main episode.

**Kira today:** no exclusion — a sample file would match + rename as the real
thing.

**Build:** scanner-side filter: video files matching `^sample$|-sample$|
trailer$|proof$` or under known extras folders get flagged `role=sample` and
excluded from the review queue (with an opt-in "show samples" pill). (Already
sketched in the the reference renamer-parity roadmap Tier 3.3.)

**Fixes:** embarrassing sample-as-episode mis-renames.

**Effort:** ~half-day.

---

### Phase 20 — Strict vs opportunistic matching mode ✅ Shipped (gate ready)

**Shipped:** `matcher/strict_mode.py` — `MatchMode` enum, `parse_mode()`, and
the pure `meets_threshold(score, mode, threshold)` gate (strict = only
auto-act at ≥ 0.85, the tier-1 floor; opportunistic = any positive match;
None/0 never auto-acts). 5 tests. The interactive Review page is unaffected
(always shows every candidate). NOTE: the consumers — auto-approve /
watch-folder import — don't exist yet, so this ships the safety rail ready to
wire so those land WITH the gate rather than bolting it on after.

**the reference renamer:** strict mode requires high-confidence matches and skips the rest;
non-strict takes best-effort guesses. Lets automated/unattended runs avoid
acting on uncertain matches.

**Kira today:** confidence tiers exist but there's no user-facing "only act on
≥X confidence" gate for auto-approve / auto-rename / watch-folder flows.

**Build:** a confidence threshold setting feeding the (future) auto-approve +
watch-folder paths; below threshold → hold for review, never auto-act.

**Fixes:** safe unattended operation once watch-folders/auto-scan land.

**Effort:** ~half-day (the threshold; depends on auto-approve existing).

---

## Combined priority (Round 1 + Round 2)

The "resolve everything" core is now a **trio**, not a pair:

1. **Phase 4 — episode-list validation gate** (stop trusting the parse)
2. **Phase 11 — folder/batch series locking** (stop one bad filename escaping)
3. **Phase 5 — anime-lists per-episode mappings** (kill the anime long-tail)

Those three together address the *structural* causes of every matching bug
you've hit. Everything else is parser coverage (1–3), better signals
(6, 7, 12, 13, 15, 16), or correctness/safety polish (8, 9, 10, 14, 17–20).

Suggested build sequence:

```
Quick parser wins:        1 → 2 → 3
Structural keystones:     4 → 11 → 5        (the trio)
Stronger signals:         12 → 15 → 6 → 13 → 7
Correctness/perf:         16 → 18 → 8
Safety / polish:          14 → 19 → 17 → 9 → 20 → 10
```

---

# Round 3 — final FileBot matching techniques

A third review asked "is there anything left in FileBot's *matching* subsystem we
haven't taken?" These two were the answer. Both shipped.

### Phase 21 — Runtime corroboration (M4, the last EpisodeMetrics signal) ✅ Shipped

**FileBot:** cross-checks the file's actual runtime against the episode/movie's
expected runtime as a confidence signal.

**Shipped (the runtime signal — the only corroborator the audit rated worth it):**
MediaInfo now reads true container **duration** → `ParsedFile.duration` (free, on
the same read that backfills quality); pure `runtime_similarity()` (±20% band,
3-min floor, decay to 0); tier-3 `RuntimeCorroborationMetric` that fires only on
**already-available** runtime (cached episode list / candidate metadata) — never
fetches, never overrides identity. **Filesize** (weak for identity) and **region**
(overlaps existing hints) deliberately skipped. The *active per-candidate runtime
fetch* is the one documented tunable left off (rate-limit cost). See roadmap M4.

### Phase 22 — Filesystem-persisted identity (xattr / NTFS ADS) ✅ Shipped (backend)

**FileBot:** stamps `net.filebot.*` extended attributes on every processed file
and reads them back on re-scan for instant, filename-independent re-identification.

**Shipped:** `kira/xattr_store.py` — POSIX `os.*xattr` (`user.kira.ids`, the
Docker/Linux + NAS path) with an NTFS Alternate-Data-Stream fallback on Windows
and a silent no-op everywhere else (pure optimisation, never a correctness
dependency). A successful rename stamps the destination with `{provider: id}`; the
scan worker reads it back into `ParsedFile.provider_ids`, where the **existing
Phase 14 embedded-ID bypass resolves it with zero search** — so a re-scan of a
Kira-renamed library re-identifies instantly even if the filename was later
mangled. The matcher needed no changes. See roadmap M6.

**Verdict:** with Phases 21–22, every catalogued FileBot *matching* technique is
either shipped or a documented, deliberately-deferred tunable. What's left
(active runtime fetch, a TVDB learned-cache) is low-value / cost-gated, not a
capability gap.

---

## Licensing note

All of the above is reimplementation of *approach* + use of *open data*
(anime-lists XML is openly licensed; AniDB title dump + Fribb already in use).
No the reference renamer source is read, translated, or copied — this document maps
the reference renamer's *capabilities* so we can build our own. Kira stays MIT/Apache.
