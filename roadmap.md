# Kira — Roadmap v2 (the next 20)

> Companion to `matching.md`. Matching parity is done (all 20 gaps shipped
> across 4 passes). This plan is everything *after* matching: finishing the
> staged sub-parts, the original friction list, the automation story, and the
> the reference renamer breadth that makes Kira a full replacement rather than "just a
> very good matcher."
>
> Same rules as matching.md: clean-room (reimplement approach + open data,
> copy no GPL source), implementation-ordered, **5 per pass**, each item
> resolves a real class of need. Stability first — nothing here should put
> the now-solid matcher at risk.

Status legend: ✅ done · 🟡 partial/staged · ⏳ planned · ❌ deferred

---

## Where we are

- ✅ Matching engine at/past the reference renamer (parser coverage, episode-list
  validation gate, folder/batch series lock, multi-metric cascade,
  normalization, acronyms, embedded IDs, date matching, anime cour routing).
- ✅ Supporting: in-place re-parse, Sonarr live queue, folder cleanup,
  sidecar co-rename, per-season + franchise cards, marquee titles.
- ✅ Robust AniDB rate-limiting (5 s cross-process gate + circuit breaker +
  cross-ref-first + heavy caching) — we don't self-trigger bans.

**Pass 5 complete.** Phase 5 matcher wire-in, Phase 6 series-boost,
feedback gaps (cold-start skeleton + background-job notifications), and
MediaInfo activation all shipped. Remaining staged sub-parts from
matching.md for demand-driven follow-up: Phase 17 token externalization,
Phase 13 prefilter index, Phase 20 consumer.

---

## Pass M — Matching completeness (the 5 missing the reference renamer techniques) · FRONT OF QUEUE

All 20 matching.md phases shipped, but the reference renamer still has **5 matching
*techniques* we don't**. These are what make matching robust on *other people's*
arbitrarily/badly-named libraries — the user's stated goal ("I want people to
use this in their setup"). Do these before the rest of the roadmap. Ordered
low-risk → high-value.

### M1. ReleaseInfo dataset expansion + token-table externalization ✅ Shipped
the reference renamer ships a large, independently-refreshed `ReleaseInfo` dataset (groups,
source/codec/clutter tokens). Ours was a small curated set. **Shipped:**
substantially expanded the curated tables (`format_stripper`: sources, codecs,
resolutions, audio, editions, HDR); added a new **case-sensitive
`RELEASE_FLAGS`** clutter table (PROPER/REPACK/INTERNAL/… — title-safe because
only the uppercase scene form strips); fixed the **multi-token-of-same-class
leak** (`2160p UHD`, `BluRay REMUX` left the second token in the title — now
`_extract_first_strip_rest` keeps the first as the value and strips the rest;
source is extracted before resolution so `UHD-BluRay` compounds stay intact);
wired **scene-rules.json user-extensibility** into every table (`extra_sources`
… `extra_release_flags`, folded in `format_stripper._build()`, hot-reloadable
via `reload_rules()`). +22 tests; fixed the long-standing `oppenheimer` parser
failure as a side effect. Touch: `parser/format_stripper.py`,
`parser/scene_rules.py`, `tests/test_format_stripper.py`.

### M2. Offline name→id prefilter indices ✅ Shipped
the reference renamer keeps offline name indices so it can resolve without a network round
trip. AniDB search was already in-memory (trigram over the title dump), so the
real gap was **acronym-only filenames** (`AoT - 01.mkv`, `JJK S02E01.mkv`):
`trigram("aot", "attack on titan")` ≈ 0, so the correct AID never even became a
candidate and the AcronymMetric had nothing to confirm. **Shipped:** a shared
`matcher/acronyms.py` (one source of truth for `KNOWN_ACRONYMS`, `acronym_forms`,
`is_acronym_shaped`); two new offline indices built alongside the AniDB title
dump (`_name_index` exact-normalized→AID, `_acronym_index` generated-initialism
→AID, capped); `search_tv` now injects the right AID for acronym queries
(curated → expansion trigram + exact-name; non-curated → generated index) — all
in-memory, ban-resilient. A curated-acronym exact match now scores **tier-1** in
`AcronymMetric` so an acronym-only anime file clears the 0.80 floor (a tier-2 hit
tops out ~0.73 and would orphan). The engine query-ladder gained an
acronym-expansion rung so TMDB/TVDB resolve non-anime acronyms (`LotR` → "lord
of the rings"). +21 tests, full suite green. Touch: new `matcher/acronyms.py`,
`providers/anidb.py`, `matcher/engine.py`, `matcher/cascade/metrics/acronym.py`.

### M3. Metric funnel / weight balancing ✅ Shipped (Observer Mode)
**Audit finding:** Kira's tier-banded cascade (`max(tier1, 0.7·tier2 + 0.3·tier3)`,
MAX-per-tier) is already at/past the reference renamer's flat weighted-sum on architecture. The
one place ours genuinely differs: the reference renamer's funnel rewards *corroboration* —
multiple independent metrics agreeing raises confidence — whereas our MAX-per-tier
deliberately doesn't (correct for the overlapping string-distance metrics, but it
also flattens genuinely independent signals like acronym + numeric + cluster).
Changing that blind would re-score the whole library, so per Pattern A this ships
as **instrumentation, not a behavior change**: a shadow funnel computes what an
"agreement-bonus" rebalance *would* score (string-distance metrics collapse to one
family vote; a second independent family adds a bounded bonus; tier-1 still wins
outright), and the runner logs `funnel_diverge` whenever the shadow would pick a
different top candidate. The live `final_score` is untouched. Enable with
`KIRA_FUNNEL_OBSERVER=1`, run normal scans 24–72 h, then triage the divergences
to decide whether the flip is strictly better. +6 tests, full suite green. Touch:
`matcher/cascade/runner.py`, `matcher/cascade/types.py` (trace `shadow_score`).
**Deferred (the flip):** promote the agreement bonus to live scoring only once
the divergence log shows it wins on real cases.

### M4. Smaller corroboration signals (duration / filesize / region) ✅ Shipped (runtime; filesize/region skipped)
the reference renamer corroborates with runtime, filesize, region. **Shipped the runtime
signal — the only one the original audit rated worth building:**
- `parser/mediainfo.py` now reads true container **duration** (`duration_to_seconds`,
  General-track-first with a Video-track fallback) → `ParsedFile.duration` (seconds).
  Free: it rides the SAME single MediaInfo read that already backfills quality, so no
  added per-file I/O; `enrich_parsed` always fills duration (a filename never carries it).
- Pure `runtime_similarity()` in `matcher/text_distance.py` — ±20% tolerance band with a
  3-min absolute floor (so a 4-min OP/ED isn't absurdly strict), linear decay to 0 past 3×
  the band, abstains (None) when either side is missing.
- New tier-3 `RuntimeCorroborationMetric` (`cascade/metrics/corroboration.py`), registered
  for every media type. **Bounded by design — it NEVER fetches:** expected runtime is read
  only from data already on hand (a cached episode list — `EpisodeResult.runtime`, populated
  by `EpisodeTitleMetric` / the validation gate — or `candidate.raw["runtime"]` from a
  details fetch). Abstains otherwise. Tier-3, so it can only gently nudge, never override
  identity/similarity. +20 tests, suite green.
**Deliberately NOT built** (matches the original audit): **filesize** (weak for *identity* —
tracks quality/length, not which show) and **region** (overlaps the parent-path anime hint +
FribbAidFilter). **Documented tunable left out:** the *active per-candidate runtime fetch*
(the case that would fire on every movie) — still skipped to avoid the per-candidate detail
fetch = rate-limit pressure. Flip it on once observer data shows it earns the cost.
Touch: `matcher/cascade/metrics/corroboration.py`, `matcher/text_distance.py`,
`parser/mediainfo.py`, `parser/parser.py`, `matcher/cascade/runner.py`.

### M5. Content-hash identification (filename-independent) ✅ Shipped (backend)
The marquee the reference renamer technique: identify a file by its **content hash**, not its
name — the only thing that works on a totally-garbage filename. **Shipped:**
`providers/_osdbhash.py` — the pure OSDb 64-bit hash (filesize + first/last 64 KiB
as uint64 sums, mod 2⁶⁴), no native lib, no network, fully unit-tested with
deterministic vectors; `providers/opensubtitles.py` — a key-gated REST client
(`identify_by_hash` + pure `parse_identity`, prefers an exact `moviehash_match`)
plus an `identify_file_by_hash` orchestrator; and endpoint
`POST /api/v1/files/{id}/identify-by-hash` that hashes `MediaFile.file_path`,
asks OpenSubtitles what release it is, and pins the resulting TMDB id through the
**hardened `select_manual_match` writer** (sticky pin, commandeer-or-append, zero
risk to the scan/match pipeline). +22 tests, suite green. **Remaining (UI):** the
OpenSubtitles API-key field + an "Identify by content" button — bundled with the
full OpenSubtitles feature (Pass 7 #11) since they share the same Settings block.
**Deliberately deferred:** anime ED2K via AniDB's UDP API (separate auth +
rate-limit + ban surface vs the HTTP API — high risk, low marginal value).
Touch: new `providers/_osdbhash.py`, `providers/opensubtitles.py`,
`api/matches.py`.

**Order rationale:** M1 first (foundational, immediately testable, zero matcher
risk — done). M2 next (pure speed/resilience win, no behavior change). M3 stays
Observer Mode (funnel rebalance touches every file). M4 shipped as a bounded,
free-data tier-3 nudge (active per-candidate fetch remains the documented
tunable). M5 last (biggest, and its movie path naturally piggybacks on the
OpenSubtitles infra from Pass 7).

### M6. Filesystem-persisted match identity (xattr / NTFS ADS) ✅ Shipped (backend)
Not in the original Pass-M list — surfaced in a later FileBot review. the reference renamer
stamps every processed file with `net.filebot.*` extended attributes and reads
them back on re-scan for instant, filename-independent re-identification. **Shipped:**
`kira/xattr_store.py` — cross-platform `write_ids`/`read_ids` (POSIX `os.*xattr`
under `user.kira.ids` → the Docker/Linux + NAS path; NTFS Alternate Data Stream
`<path>:kira.ids` on Windows; silent no-op on filesystems supporting neither, so
it's a pure optimisation, never a correctness dependency). A successful rename
(`api/rename.py`) stamps the destination with the resolved `{provider: id}`; the
scan worker (`api/scans.py`) reads it back into `ParsedFile.provider_ids` when the
filename carries no embedded ID — where the **existing Phase 14 bypass resolves it
by ID with zero search**, so the matcher needed no changes. Payload is the same
tiny JSON shape as `provider_ids`. +8 tests (round-trip + graceful-degradation).
**Remaining (UI):** a Settings read-out of whether the library filesystem supports
persistence (`xattr_store.supported`) — cosmetic, bundle with a future Settings pass.
Touch: new `kira/xattr_store.py`, `api/rename.py`, `api/scans.py`.

---

## Pass 5 — Finish the matching story + the original friction list

The friction items (#164/#165) are here first because they're the ones *you*
flagged as "fix before anything else," and they make every later feature feel
better.

### 1. Wire in anime-lists per-episode mappings (Phase 5 integration) ✅ Shipped
The ScudLee ingester + resolver existed and were tested; the matcher just didn't
consult them. **Shipped:** new `route_file_to_cour_precise()` in `cour_routing.py`
consults `resolve_tvdb_to_anidb()` FIRST and falls back to the summed-count
table — it trusts ScudLee only when (a) a real multi-cour table exists and (b)
the resolved AniDB id is one of the franchise's sibling cours in that table
(sanity vs a stray mapping). Strict refinement: it can only correct a routing the
summed-count math got wrong (offset cours, mid-season special inserts,
non-contiguous ranges), never introduce a worse one. Wired into all three
primary routing sites (scan `_match_cluster`, `_rematch_one` discovery,
`bulk_select_manual_match`). Sourced from GitHub (not AniDB) + 24h-cached →
ban-safe, no AniDB rate-limit pressure. +5 tests, suite green. Touch:
`matcher/cour_routing.py`, `api/scans.py`, `api/matches.py`.

### 2. Scan performance (#164) ✅ Shipped
Delivered as a focused 5-fix scan pass:
- **SQLite WAL + busy_timeout** (`database.py`) — the headline fix. In the
  default rollback-journal mode the scan worker's writer takes an EXCLUSIVE
  lock that blocks ALL readers, so the frontend's progress polls stall and the
  UI looks frozen mid-scan. WAL lets readers run concurrently with the writer;
  busy_timeout=5000 + synchronous=NORMAL turn write contention into a brief
  wait instead of "database is locked".
- **Batch DB loads in `_match_phase`** — replaced the per-fid `session.get`
  storm (4×N round-trips) with chunked `IN (...)` loads + one `matched` query
  per cluster.
- **Responsive discovery walk** — `path.stat()` offloaded to a worker thread so
  a slow NAS stat can't starve the event loop; the await also yields per file
  so the file count climbs smoothly.
- **Opt-in concurrent cluster matching** (`KIRA_MATCH_CONCURRENCY`, default 1 =
  unchanged sequential) — each cluster runs in its own session; WAL makes the
  concurrent commits safe. AniDB clusters still serialize on the 5s gate;
  non-AniDB clusters overlap. Touch: `database.py`, `api/scans.py`.

### 3. Feedback gaps (#165) ✅ Shipped
- ✅ **Provider-error surfacing** — the matcher now records WHY a provider
  failed (auth/config vs unreachable) instead of swallowing it into a
  no-match; the scan worker raises a Notification ("Some providers failed…")
  so a bad/missing API key reads as a clear warning. (`matcher/engine.py`,
  `api/scans.py`; +4 tests.)
- ✅ **Frozen-UI-during-scan** addressed indirectly by the WAL + responsive-walk
  work above — the existing progress UI now actually updates live instead of
  stalling on the writer lock.
- ✅ **Cold-start skeleton** — `LibraryGrid` renders 12 skeleton cover-cards
  while `hydrated=false`; `HistoryPage` has its own `loading`/`firstLoadDone`
  stale-while-revalidate pattern. No spinner needed — skeleton cards are
  industry-standard and already in place.
- ✅ **Background-job visibility** — auto-heal, bulk rematch, and re-parse now
  create Notification rows on completion (success/warning with stats), surfaced
  by the existing NotificationsBell popover. Touch: `api/matches.py`,
  `api/scans.py`.
- ✅ **Scan popup survives refresh** (post-Pass-7 fix) — the poll loop was
  extracted to a reusable `trackScan`; on mount the app finds an in-flight scan
  (GET /scans, status scanning/matching, no `completed_at`) and re-attaches the
  progress banner + polling, so a reload no longer loses it. Paired with boot-time
  `reconcile_orphaned_scans()` (settles scan rows a crash/restart left mid-flight
  → `failed: interrupted`) so the resume can't latch onto a dead scan and spin.
  Touch: `App.tsx`, `api/scans.py`, `main.py`. +3 tests.

### 4. Episode-title series boost (Phase 6 remainder) ✅ Shipped (read-only; see fix)
**Shipped:** new `EpisodeTitleMetric` in `cascade/metrics/episode_title.py`
(tier-2 SIMILARITY). When `parsed.episode_title_guess` is set and a candidate's
episode list is **already in `ctx.enrich_cache`**, it trigram-matches the guess
against the episode titles and boosts the candidate (floor 0.55, scaled 0→1) —
disambiguating two same-titled series by which one owns the episode title.
Registered for TV + anime.

> **⚠️ Regression + fix (post-Pass-7):** the first cut had the metric *fetch*
> the episode list per-candidate inside the cascade. That broke the cascade's
> pure/in-memory contract and hammered AniDB's rate-limited `get_episodes`
> (5s cross-process gate, ban-prone) — every anime cluster serialized behind it,
> so scans appeared **stuck** and files fell to **no_match**. Fixed: the metric
> is now **read-cache-only, never fetches** (regression-guarded by a test
> asserting the source has no `.get_episodes(` call). The high-value
> episode-title *resolution* still runs ban-safely in `bipartite.py` Pass 5; the
> series-*boost* activates only when a future caller pre-populates the cache from
> a ban-safe source. Touch: `cascade/metrics/episode_title.py`, `cascade/runner.py`.

### 5. Activate MediaInfo (Phase 16 remainder) ✅ Already shipped
**Already complete.** `pymediainfo>=6.1` in `pyproject.toml`; `parser/
mediainfo.py` reads true resolution/codec/HDR from files with graceful
degradation when the native lib is absent; `api/scans.py` integrates it as a
fallback (fills missing quality/codec only, respects `parsing.read_mediainfo`
setting); Settings UI exposes both the enable toggle and authoritative override.
No further work needed.

---

## Pass 6 — Automation (hands-off / arr-stack workflow) ✅ Shipped

Turns Kira from "I click scan" into "drop a release, it organizes itself."
All five items shipped. +35 tests, full suite green (392). Frontend Settings →
Integrations gained Media-servers, Inbound-webhook, and Notifications cards
(verified live in the preview); the watched-folder per-folder mode/threshold UI
and the confidence auto-approve toggle already existed.

### 6. Watched folders (#166) ✅ Shipped (daemon prior; auto-rename execution now)
The `watchfiles` daemon (debounce + poll fallback, per-folder mode) shipped in
Tier 1.1. **This pass landed the execution half:** `watcher.maybe_auto_rename`
is no longer a log-only stub — it auto-organizes newly-matched files. Pure,
unit-tested gate `compute_auto_rename_eligibility(cfg, files)` partitions the
batch; eligible files are renamed by reusing the real `rename()` executor (so
they get RenameHistory, the summary notification, media-server refresh, and
fan-out for free), default op **hardlink** (non-destructive). Touch:
`watcher.py`. 8 gate tests (`test_auto_rename_gate.py`) + 3 execution-seam tests
(`test_auto_rename_execute.py`: only eligible files reach `rename()`, with
`dry_run=False` + the user's op/profile). *(PLAN.md + a few code comments lagged
behind here, still calling it a "log-only stub" — reconciled.)*

### 7. Auto-approve mode (Phase 20 consumer) ✅ Shipped
The strict-mode gate is now wired into the auto-rename consumer:
`meets_threshold(confidence, STRICT, folder_threshold)` decides per file —
clears the folder's threshold → auto-renamed; below → held for manual review
(never auto-acted). The per-folder mode (`scan` | `auto_rename`) + threshold are
the opt-in switch, editable in Settings → Paths (UI already existed). STRICT
mode enforced, so a shaky match is never auto-organized. Touch: `watcher.py`,
`matcher/strict_mode.py` (consumed).

### 8. Sonarr/Radarr post-processor recipe + webhook ✅ Shipped
New `api/webhooks.py`: `POST /api/v1/webhooks/sonarr` + `/radarr`, **token-gated**
(`integrations.webhook.token`; unset → 404, wrong → 403). The payload is treated
as untrusted data — any path in it is honoured ONLY if it resolves under a
configured library root / watch folder (`path_under_roots`), else Kira scans the
configured roots; an attacker-supplied path can never make Kira scan outside the
library. Fires `_start_scan(source="auto")`, so it flows through match →
auto-rename → notify. `Test` events 200 without scanning. Settings → Integrations
shows the copy-paste webhook URL. 14 tests. Touch: new `api/webhooks.py`,
`main.py`.

### 9. Plex / Jellyfin refresh after rename ✅ Shipped
New `integrations/media_server.py` (`refresh_plex` GET `/library/sections/all/
refresh`, `refresh_jellyfin` POST `/Library/Refresh`, `refresh_all` reading
`integrations.plex.*` / `integrations.jellyfin.*`). Hooked into the rename
batch-completion point (after the final commit, only when ≥1 file succeeded) —
best-effort, short timeout, fully exception-isolated so a down server never
affects the rename. Auto-rename inherits it via `rename()`. Settings →
Integrations "Media servers" card. 8 tests. Touch: new
`integrations/media_server.py`, `api/rename.py`.

### 10. Notifications fan-out ✅ Shipped (Discord + generic; Apprise via generic)
New `kira/notify.py` `fan_out(kind, title, body)` reading
`notifications.discord_webhook` (rich content w/ severity emoji) +
`notifications.webhook_url` (generic JSON POST — Apprise / n8n / custom). Wired
at rename-complete and auto-scan-found. Best-effort, one sink failing never
blocks the other, never raises. Settings → Integrations "Notifications" card.
6 tests. Touch: new `kira/notify.py`, `api/rename.py`, `api/scans.py`.

---

## Setup recipe — arr-stack + media servers

**Sonarr/Radarr → Kira (auto-scan on import):**
1. Settings → Integrations → Inbound webhook: set a token, copy the shown URL.
2. In Sonarr: Settings → Connect → add **Webhook**; URL =
   `http://<kira-host>:6546/api/v1/webhooks/sonarr?token=<token>`, method POST,
   triggers On Import + On Rename. (Radarr: same, `/webhooks/radarr`.)
3. Optional hands-off: Settings → Paths → set the relevant watched folder to
   **Auto-rename high-confidence** with a threshold (e.g. 95%). Imports now scan,
   match, and (above threshold) organize with no clicks; lower-confidence
   matches wait in Review.

**Plex/Jellyfin refresh:** Settings → Integrations → Media servers → paste URL +
token/key. Every rename batch (manual or auto) then triggers an immediate
library refresh.

**Notifications:** Settings → Integrations → Notifications → paste a Discord
channel webhook and/or a generic JSON endpoint to hear about scans + renames
outside the app.

---

## Pass 7 — Metadata richness (Plex / Jellyfin polish) ✅ Shipped

All five items shipped. +40 tests, full suite green (432); frontend typecheck
clean; the OpenSubtitles "Subtitles" Settings card verified live in the preview.

### 11. OpenSubtitles auto-download ✅ Shipped
Built on M5's hash backend. `providers/opensubtitles.py` gained pure parsers
(`parse_subtitle_candidates` — ranks moviehash-match first then download count;
`pick_best_per_language`; `parse_download_link`; `parse_login_token`;
`subtitle_sidecar_name`), client methods (`login` for the download JWT, `search`
hash-first w/ tmdb/imdb/season/episode fallback, `download_link`), and the
`fetch_and_save_subtitles` orchestrator that writes `<stem>.<lang>.srt` beside
the video (which the existing sidecar co-move then carries). Endpoint
`POST /api/v1/files/{id}/fetch-subtitles`; opt-in `subtitles.auto_fetch` hook
fires after a rename batch (one shared client). Settings → Integrations
"Subtitles" card (API key, username, password, languages, auto-fetch toggle).
Key/credential-gated + best-effort throughout. +16 tests. Touch:
`providers/opensubtitles.py`, `api/matches.py`, `api/rename.py`, `SettingsPage.tsx`.

### 12. NFO generation (Kodi / Emby) ✅ Shipped
New `renamer/nfo.py` — pure builders for `<movie>` / `<episodedetails>` /
`<tvshow>` (XML-escaped, empty fields omitted, `<uniqueid>` from the match
provider) + `plan_nfo_writes` (movie → `<stem>.nfo`; episode → `<stem>.nfo` +
write-if-absent `tvshow.nfo` at the show root via `series_root_for`). Written at
the rename hook from `Match.metadata_blob` — pure output, no API calls. Opt-in
`naming.write_nfo` (default off).
**Enriched (Pass T+):** movie/tvshow now also emit `<originaltitle>` (native /
romaji / alt-title — big for anime), `<country>`, `<thumb>` poster + `<fanart>`
URLs, movie `<set>` (the #14 collection → Kodi movie sets), and tvshow
`<status>` (Continuing/Ended); episode gains `<showtitle>`. Every added tag is a
real Kodi/Emby field backed by data we actually have and omitted when absent —
deliberately NO fabricated per-episode `<aired>`/`<uniqueid>` (not stored).
**User-configurable (Pass T+):** Settings → Naming shows a per-field include/
exclude grid (12 toggles: plot, genres, cast, director, studio, runtime,
country, original title, artwork, collection, status, show title) stored as
`naming.nfo_fields`. Builders take a `fields` set (`None` = all on, so unset
libraries are unchanged); `_resolve_nfo_fields` reads it at rename time with
absent-key-defaults-on semantics. Structural identity (title/year/season/
episode/`<uniqueid>`) is always written. 22 NFO tests; live-preview verified
(grid renders, default-on, individual toggle). Touch: `renamer/nfo.py`,
`api/rename.py`, `SettingsPage.tsx`.

### 13. Full artwork download ✅ Shipped (poster + fanart)
`tmdb.get_movie_details`/`get_tv_details` now surface `fanart_url` (the backdrop,
`original` size). `_download_artwork_files` writes `<stem>-poster.jpg` /
`<stem>-fanart.jpg` beside the renamed file (Plex local-asset convention),
write-if-absent, short timeout, exception-isolated. Opt-in `naming.download_artwork`
(default off). +7 tests. (banner/clearlogo/per-season deferred — needs extra
provider art endpoints; poster+fanart is the high-value 80%.) Touch:
`providers/tmdb.py`, `api/rename.py`. **Extended in Pass X** — fanart.tv now
supplies clearlogo / clearart / banner / disc / character art + fixes the
TV/anime "poster-only" gap, with a per-type picker in Settings.

### 14. Movie collection grouping ✅ Shipped
`Match.collection_id`/`collection_name` columns (idempotent ALTER);
`tmdb.get_movie_details` reads `belongs_to_collection`; `_match_singleton` writes
them and sets the selected movie's `series_group_id="tmdb-collection:<id>"` so
collected films group under one band — reusing the anime franchise-grouping UI.
`LibraryGrid` titles the band with the collection name (threaded through
`MatchData`/`LibraryItem`). +3 tests. Touch: `database.py`, `models.py`,
`providers/tmdb.py`, `api/scans.py`, `lib/adapters.ts`, `lib/types.ts`,
`LibraryGrid.tsx`.

### 15. Multi-disc movie handling ✅ Shipped
`ParsedFile.disc` + movie-only `_extract_disc` (unambiguous `CD`/`Disc`/`Disk`
markers — `Part N`/`D1` deliberately excluded as frequent real-title components
like "Deathly Hallows: Part 1"). `{{disc}}` template token (Plex-style " - cdN")
added to the default movie templates so split-film halves land on distinct,
stack-detectable paths. No-op for single-file movies + all non-movie types.
+11 tests. Touch: `parser/parser.py`, `renamer/templates.py`.

---

## Pass 8 — Power-user naming + robustness

### 16. Template-engine parity (Jinja2) ✅ Shipped (v0.5.0, Tier 1.5)
Jinja2 SandboxedEnvironment with pipe filters, conditionals, ~60 tokens
(director/cast/genres/collection/ids/runtime/tech tags) + canonical-preset
macros (`{{plex}}`/`{{jellyfin}}`/`{{kodi}}`/`{{emby}}`), plus the
`naming.custom.*` `{token}`→`{{token}}` boot migration. Touch:
`renamer/templates.py`.

### 17. Naming template live-preview ✅ Shipped
The backend endpoint `POST /api/v1/rename/preview-template` renders the user's
templates against their own recent matched files through the REAL
`format_target_path` pipeline (single source of truth, can't drift from a real
rename). **UI shipped too:** the Settings → Naming panel's right pane
(`LiveTemplatePreview` in `settings-blocks.tsx`) debounces edits, calls the
endpoint, and shows the actual paths it would produce against the real library
(empty-state + per-sample error handling included). Touch: `settings-blocks.tsx`,
`lib/api.ts`.

### 18. Token-table externalization (Phase 17 remainder) ✅ Shipped
**Shipped:** `format_stripper`'s base tables (sources, sources_ambiguous, codecs,
resolutions, wxh_to_p, audio, subtitles, editions, hdr, bit_depth, release_flags)
now load from a shipped `parser/release_tokens.json` at `_build()` time; the
in-code literals stay as the guaranteed FALLBACK (a missing/malformed file never
breaks parsing). The shipped JSON mirrors the literals exactly, so behavior is
unchanged out of the box. User `scene_rules.json` extras still fold ON TOP of the
JSON base (M1's `extra_*` layer). Added `[tool.setuptools.package-data]` so the
file ships in wheels. +5 tests. Touch: new `parser/release_tokens.json`,
`parser/format_stripper.py`, `pyproject.toml`.

### 19. CLI mode ✅ Shipped
`kira status | scan | ls | rename | identify` — a scriptable client that drives
a RUNNING Kira server over its HTTP API (the natural shape for a daemon-style /
Docker app; reuses the exact endpoints the web UI does, so there's no second
match/rename path to drift). `rename` is **dry-run by default** (`--apply` to
execute), pulls op/profile from the server's saved defaults, and supports
`--ids` / `--status` / `--all` selectors. `status` + `ls` take `--json` for
piping. ASCII output + forced UTF-8 so it never mojibakes a Windows console;
friendly connection-error message; exit codes 0/1/2. +12 tests. Touch: new
`kira/cli.py`, `pyproject.toml` (`[project.scripts] kira = "kira.cli:main"`).
Run as `kira …` (after `pip install -e .`) or `python -m kira.cli …`.

### 20. Local name→id prefilter index (Phase 13 remainder) ✅ Resolved (AniDB done; TVDB sliver intentionally not built)
**The buildable part is already done.** M2 built the dump-based offline indices
(`AniDBProvider._name_index` exact-normalized→AID + `_acronym_index`
initialism→AID), consulted in `search_tv` before any network call — instant,
ban-resilient, feeding the acronym metric. That's the whole value for the only
provider with a bulk title source.
**The "+TVDB on demand" sliver is deliberately NOT built.** TVDB/TMDB expose no
title dump to index offline, so it would have to be a *learned* name→id cache
populated from past matches — which (a) overlaps the new **xattr persistence**
(M6), the superior mechanism for "remember what this file matched across
re-scans," and (b) TVDB/TMDB search isn't rate-limited the way AniDB is, so the
offline-prefilter speed/ban win that justified the AniDB index doesn't apply.
Net: low marginal value, real staleness/mis-pin risk. Reopen only if a concrete
need appears. Touch (if reopened): `providers/tvdb.py`, `matcher/engine.py`.

---

## Pass S — Stability, recovery & feature management ✅ Shipped

A hardening arc after Passes 5–7, triggered by real-library testing. Closed a
set of regressions + environmental issues + added a place to manage feature
maturity. All test-backed (suite at 461).

**Regressions fixed (introduced during Passes 5–7, found in testing):**
- **In-cascade AniDB hammering** — `EpisodeTitleMetric` fetched episode lists
  per candidate inside the cascade, serializing every anime cluster behind
  AniDB's 5 s gate → "stuck scan". Now read-cache-only (pure cascade restored).
- **Per-file I/O in the discovery walk** — the xattr ID probe and MediaInfo
  header read ran per file during the walk (a NAS round-trip each). Moved xattr
  to match-time; MediaInfo to the match phase then **off by default** (Labs).
- **O(N) `resolve()` per walked file** — `_norm` called `Path.resolve()` for
  every file; replaced with one-time per-root aliasing (zero per-file FS hits).
  Also dropped a redundant `is_file()` stat (scandir already classified them).

**Network resilience (environmental, on the user's NAS/ISP):**
- **Force-IPv4** (`kira/net.py`, default on) — dual-stack hosts (TMDB) behind
  broken IPv6 caused intermittent `ConnectError`. One `socket.getaddrinfo`
  filter + an IPv4-bound httpx transport (`local_address`) make every client
  IPv4-only. Toggle: `network.force_ipv4` / `KIRA_FORCE_IPV4`.
- **Split retry policy** — connection/TLS blips retry FAST (0.2–1 s ×5);
  rate-limits keep the long backoff. A dropped connect no longer costs 7 s.
- **Connection reuse** — warm keep-alive (300 s) + HTTP/2 when `h2` present →
  a scan re-handshakes ~once instead of per-file (the real cure for TMDB's
  TLS-handshake resets from the user's security software).

**Crash recovery:**
- **`init_db` migration isolation** — schema ADD-COLUMNs now commit
  independently of, and before, data backfills, so a backfill error can't roll
  back a column and leave the ORM selecting a missing one (the "breaks until DB
  reset" bug).
- **Orphaned scan + file reconciliation on boot** — a killed scan's Scan row →
  failed, and stuck `MediaFile` rows (`matching`/`parsing`) → `discovered`, so
  covers stop animating; a scan now also re-matches leftover `discovered` files
  (resume), with the progress bar counting them.
- **Scan-popup resume on refresh** — the poll loop re-attaches to an in-flight
  scan on mount.

**Stale-match recovery:** confirmed (via DB inspection + a live rematch) that
"some episodes matched wrong" was *stale Match rows from an older run*, not a
live bug — re-matching corrects them (current pairing is right). Candidate
follow-up: an opt-in self-heal that flags `match.episode ≠ parsed.episode` and
re-matches.

**Labs — feature-maturity management (new):** `Settings → Labs` surfaces the
experimental / cost-bearing toggles with status chips, all off by default:
MediaInfo-on-scan (`Perf cost`), episode-title series-boost (`Experimental`,
now a **bounded TVDB/TMDB-only** impl that can't hammer AniDB),
runtime-corroboration (`Needs MediaInfo`). NFO + artwork toggles surfaced in
Naming. Flags are `labs.*` settings read through the matcher cache. +7 tests.
Touch: `matcher/engine.py`, `cascade/runner.py`, `SettingsPage.tsx`, `ui.tsx`.

---

## Ordering rationale

| Pass | Theme | Why here |
|------|-------|----------|
| 5 | Finish matching + your friction list | Closes every loose end from matching.md and fixes the scan-speed/feedback you flagged first. |
| 6 | Automation | Watched folders + auto-approve + arr hooks turn Kira hands-off — highest leverage for a Sonarr user. |
| 7 | Metadata richness | Subtitles / NFO / artwork / collections — the Plex+Jellyfin polish you'd notice daily. |
| 8 | Power-user + robustness | Template parity is big but optional; CLI + token data file are for tinkerers. |

**Explicit scope cut (unchanged):** Music (MusicBrainz + AcoustID). Native
installers (.exe/.dmg) + multi-user accounts remain weeks-out, demand-driven.

---

## Pass T — Self-heal, activity surface, OpenSubtitles UI & CLI ✅ Shipped

The five "remaining" items from the previous revision of this section, now all
closed (suite 461 → 483):

1. **Stale-match self-heal (episode drift)** — `_heal_episode_number_drift`
   (`_HEAL_VERSION` 24): a version-gated one-shot that flags selected,
   non-manual `tv_episode` rows whose stored `episode_number` matches NEITHER
   the parsed season-local NOR absolute episode (the One Piece "files parsed as
   1156–1160 but Match rows stuck on 1–5" class), then NULLs `episode_title` +
   `metadata_blob` so the existing **ban-aware** BATCH loop re-matches each row
   through the REAL engine. Deliberately does NOT decide the episode itself
   (number comparison can't tell genuine drift from a legit cour/absolute
   remap), KEEPS `episode_number` (so a ban-deferred re-match doesn't blank a
   correct row), and is one-shot (cour false-positives re-confirm idempotently,
   not every boot). +6 tests.
2. **Activity surface + cold-start** — new in-memory `kira/activity.py`
   (`begin/progress/end/snapshot`, crash-safe via a `stale_after` window) +
   `GET /api/v1/activity`; the auto-heal + warm-up report progress, and boot
   reconcile records what a restart recovered. Frontend `useActivity` hook +
   `ActivityPill` (shown in the Toast `leading` slot when no scan is running)
   + a one-time "recovered N files after a restart" toast. The cold-start
   skeleton already existed (`hydrated` flag). +4 tests.
3. **Naming live-preview UI** — turned out already built (`LiveTemplatePreview`);
   the prior "UI pending" note was stale. See #17 above.
4. **OpenSubtitles + identify-by-content UI** — the Settings → Subtitles block
   already existed; added the missing per-file actions: `api.identifyByHash` +
   `api.fetchSubtitles`, an **"Identify by content"** button in the manual-search
   modal (content-hash identify for garbage-named files) and a **"Fetch
   subtitles"** button in the file-details modal. App handlers throw-on-failure
   so the modal stays open on error, resolve-to-close on success.
5. **CLI mode** — see #19 above. +12 tests.

---

## Pass U — Real-library bug fixes ✅ Shipped

Four issues surfaced by reviewing a real library (Rent-a-Girlfriend, Loki,
Nobody 2). Root-caused against the live DB (read-only), then fixed (suite
499 → 506):

1. **Anime grouped under "TV Series"** — `media_type` is decided once at scan
   time (only `/anime/` paths or fansub groups → "anime") and a successful
   AniDB match never corrected it, so a copy scanned from a release-named
   folder came out "tv". Fix: a forward hook in the scan finalizer + a
   version-gated heal `_heal_media_type_from_provider` (v25) set
   `media_type='anime'` whenever the selected match is AniDB (anime-only),
   recomputing the series/variant keys so the row re-clusters under anime.
2. **AniDB per-cour season orphaning in the popup** — investigated; the popup
   ALREADY reconciles AniDB's season-1-local numbering with `S0N`-labelled
   files (season-agnostic `ep-{N}` key + `1-{ep}`/`abs-{ep}` normalization +
   filename-episode offset rescue). The screenshot orphaning was a mid-scan
   transient plus one stray `tv`-typed duplicate (no episode), which the
   media_type heal (#1) + episode-drift heal (v24) re-match. No new pairing
   code — would have duplicated existing logic.
3. **Black season poster (Loki S1)** — TVDB returned a BANNER (landscape) as
   S1's poster; the portrait card rendered it black. `get_season_poster` now
   detects poster-vs-banner from the artwork URL's *type* segment (every TVDB
   v4 URL is under `/banners/v4/`; the real type is `…/posters/…` vs
   `…/banners/…` after the id), prefers a type-7 Season Poster, and falls back
   to the series poster over a banner. Frontend `<img onError>` now degrades to
   the initials card instead of a blank gradient. +3 tests.
4. **"Nobody 2" matched the 2021 "Nobody"** — a stale low-confidence fallback
   (matched before the year parsed). Version-gated heal
   `_heal_movie_year_mismatch` (v25) re-matches selected movies whose stored
   year ≠ the parsed year, through the real matcher (idempotent for confident
   picks). +4 tests.

Items 1, 3, 4 apply on the next backend restart (the v25 heal pass runs once;
the new activity pill surfaces it).

**Follow-up (manual-match UX), from the second review pass:**

5. **Popup didn't refresh after a manual re-match** (hit on Nobody 2 + a stray
   Kanojo episode) — the ReviewPage popup re-synced its item by `id`, but a
   manual pick flips the id (`lib_<seriesKey>_<provider>_<id>`) and movies have
   no `seriesKey`, so the fallback missed and the popup showed stale data.
   Rewrote the re-sync to match by **file-id overlap** (file ids are stable
   across re-matches), so movies, id-changed clusters, and media_type shifts
   all re-sync instantly.
6. **Manual pick didn't move the file out of TV Series** — `select_manual_match`
   / `bulk_select_manual_match` left `media_type` untouched. New shared helper
   `_apply_media_type_for_manual_pick` sets it from the pick (AniDB → anime;
   else the chosen result's type) and recomputes the series/variant keys.
7. **Stale poster flashed on refresh** — only the initial fetch wrote the SWR
   cache, so a post-mutation refresh hydrated a pre-mutation snapshot until the
   background fetch landed. App now mirrors `state.files` into the cache
   (debounced) on every change. +3 tests (manual-pick media_type).
8. **MediaInfo embedded-title rescue** — `read_embedded_title` reads the
   container General-track `title`; in the match phase, a file that parsed to
   no title / `media_type='unknown'` (the matcher would skip it entirely) gets
   re-parsed from the embedded title before giving up. Runs even when the
   global MediaInfo-on-scan toggle is off (these files never match otherwise),
   adopts the re-parse only when it yields a real title without regressing one
   that already exists. Best-effort — the tag is often blank/junk, so
   "Identify by content" (OSDb hash) stays the dependable path for nameless
   files. +5 tests.

**Considered + deferred — provider fan-out for misclassified anime.** When a
`tv`-classified file doesn't confidently match TVDB/TMDB, also consult the
offline AniDB name index and let the existing global ranking decide. Feasible
(the engine already gathers-and-ranks; AniDB title search is offline so it's
cheap) and would help SxE-named anime sitting outside an `/anime/` folder. NOT
built: a foldered Plex/Jellyfin library already classifies anime correctly, it
can't disambiguate an anime from a same-titled live-action adaptation (the
file-503 twin), and it adds risk to the matcher. Reopen if loose/un-foldered
anime becomes a recurring cost. Touch (if reopened): `engine.py`
`PROVIDER_PREFERENCE` + an exact-name-index gate.

---

## Pass V — Anime correctness (absolute-numbered long-runners) ✅ Shipped

Surfaced dogfooding the real library (Attack on Titan, One Piece, Bleach). One
root cause runs through all of it: **series-absolute episode numbers vs. AniDB's
per-cour local numbering.** Every fix is general / data-driven (Fribb mappings,
provider episode lists, settings) — a grep audit confirmed **no show id or name
in logic** (only comments, test fixtures, and UI example text).

### V1. Provider preference per media type ✅ Shipped
`matching.provider_order.<movie|tv|anime>` overrides the hardcoded default order;
`resolve_provider_order()` is **soft** (preferred-first, omitted defaults kept as
fallbacks so a pick never strands a title as no-match). Settings → Providers
picker. Touch: `matcher/engine.py`, `pages/SettingsPage.tsx`,
`tests/test_provider_order.py`.

### V2. Anime numbering: Absolute / Seasonal ✅ Shipped
`naming.anime_numbering` selects a per-profile `anime_absolute` template variant
via `select_template()` + a collision-safe `{{absx}}` token (absolute number, or
SxE fallback so a flat layout can't collide two seasons). Settings → Naming
toggle. Touch: `renamer/templates.py`, `api/rename.py`, `pages/SettingsPage.tsx`,
`tests/test_anime_numbering.py`.

### V3. TVDB→AniDB franchise fold ✅ Shipped
A long-runner whose pure-absolute files route to TVDB used to sit on its own
card. `compute_series_group_id` now reverse-maps a known-anime TVDB id through
Fribb (`aid_by_tvdb`) to its AniDB franchise root, folding it into the one card; a
one-shot `_refold_tvdb_anime_groups` migration re-folds existing rows on boot (no
rescan). Scoped to `tv_episode`; live-action TVDB untouched. Touch:
`matcher/engine.py`, `database.py`, `tests/test_series_group_fold.py`.

### V4. Absolute→cour routing + whole-franchise veto abstain ✅ Shipped
Two gaps kept absolute files (AoT Final Season `- 60..89`) off their AniDB cours:
- `route_file_to_cour` gained an `abs_to_local` bridge (built from the episode
  list's `absolute_number↔episode` pairs) so absolute files reach the
  season-local cour table.
- `EpisodeCountSanityMetric` no longer vetoes a Fribb cour when the **whole
  franchise's** absolute span covers the cluster (`aids_by_tvdb`) — it used to
  judge each cour against the absolute max (89 vs a 30-ep cour) and force TVDB.
Touch: `matcher/cour_routing.py`,
`matcher/cascade/metrics/episode_count_sanity.py`, `providers/anime_mappings.py`,
`api/scans.py`, `api/matches.py`, `tests/test_absolute_cour_routing.py`.

### V5. Cour-local episode number + popup absolute pairing ✅ Shipped
- When cour routing fires, `Match.episode_number` is now the cour-**local** number
  (consistent with the cour AID), so the popup pairs files against that AID's own
  1..N list. Rename output is unaffected (renders from parsed / `{{absx}}`).
- `/series` now emits `absolute_number`; the CoverPopup pairs absolute-named files
  against it (cache version bumped to drop stale lists). Touch: `api/scans.py`,
  `api/matches.py`, `api/series.py`, `lib/episodes.ts`, `lib/api.ts`,
  `components/CoverPopup.tsx`, `lib/cache.ts`, `tests/test_series_absolute_number.py`.

### V6. Uniform franchise labels ✅ Shipped
Franchise members sharing a base name get a uniform distinguisher: keep years
only if ALL have them, else relabel the group `<base> Part N` in chronological
order (bare/earliest = 1). Fixes "Season 3" + "Season 3 (2019)" reading
inconsistently; card order now matches the labels. Touch:
`components/LibraryGrid.tsx`.

### V7. Re-identify / manual-pick routing parity ✅ Shipped
The V4 absolute→cour bridge first lived only in the scan path, so clicking
**Re-identify** (or manually picking a match) couldn't route absolute-numbered
files into their cours — only a full rescan could. Wired `abs_to_local` into all
three write paths: `_rematch_one` (Re-identify / auto-heal / bulk rematch — the
episode-list fetch was reordered ahead of routing so the map is ready),
`bulk_select_manual_match` (manual pick), and the enrichment title-lookup fast
path. Re-identify now matches a full rescan. Touch: `api/matches.py`.

### V8. Flat-umbrella local→absolute remap (One Piece "S23E04") ✅ Shipped
The inverse of V4's absolute→cour bridge. One Piece's whole run lives under ONE
flat AniDB AID (69) numbered absolutely; Fribb carries no `season.tvdb` for it
(`tvdb_season(69) is None`). A file that arrives in TVDB-season-LOCAL form —
`One.Piece.1999.S23E04` — parses `episode=4`, and the bipartite pairs it to the
Elbaf cour's LOCAL episode 4 (whose `absolute_number` is 1159). Storing the local
`4` mislabelled it as the 1999 "Red-Haired Shanks" instead of 1159 "Destroy the
Miniature Garden" — which is in fact a DUPLICATE of the user's `S23E1159` file.

Fix: `remap_umbrella_local_to_absolute()` (pure, in `cour_routing.py`) rewrites a
flat-umbrella file's stored `episode_number` local→absolute via the cluster's
`local_to_abs` map, so duplicates line up on the same number. Tightly gated — it
no-ops for absolute-named siblings (1159 ∉ the local map), per-season AIDs
(Frieren S2 `tvdb_season=2`, AoT cours `=4` — their lists ARE local), normal
western TV (no `absolute_number` → empty map), and early-cour self-maps (One Piece
ep 4 in the 1999 season, where absolute == local). A flat umbrella inherently has
no Fribb cours, so cour routing never fires for it — the `routed_aid is None`
guard makes the two systems provably disjoint. Wired into all three write paths
(scan `_match_cluster`, `_rematch_one`, `bulk_select_manual_match`) for parity;
the enrichment fast path is fill-only and untouched. Rename output is unaffected
(renders from parsed / `{{absx}}`). Verified live against the real file:
`4 → 1159`. Touch: `matcher/cour_routing.py`, `api/scans.py`, `api/matches.py`,
`tests/test_umbrella_local_to_absolute.py` (+10).

**Suite: 681 passed** (+44 across six new test files). The matcher special-cases
the AniDB *provider* (its per-cour data model), never a specific show.

---

## Pass W — Subtitle sources (multi-provider) 🟡 In progress

Beyond the existing OpenSubtitles.com auto-fetch: pull subs from more sources,
cheapest-first, each skipping languages already on disk. (Default stance stays
"Bazarr does this better if you run it" — this is for standalone use.)

### W1. Embedded subtitle extraction ✅ Shipped
Extract TEXT sub tracks already *inside* the container — the highest-yield source
for anime (fansub MKVs are full of them), and entirely offline. `subtitles/
embedded.py`: pymediainfo enumerates Text tracks (image subs PGS/VobSub skipped,
their stream ordinal still consumed so the ffmpeg index stays right), then
`ffmpeg -map 0:s:N -c copy` extracts to a language-tagged sidecar in the track's
native format (`.srt`/`.ass`). No key, no network; clean no-op when ffmpeg or
pymediainfo are absent. Runs BEFORE OpenSubtitles in the rename hook (free first,
OpenSubtitles fills the rest); both skip a language that already has any sidecar.
+8 tests. Needs ffmpeg on PATH (trivial in a Docker image). Touch:
`subtitles/embedded.py`, `providers/opensubtitles.py` (sidecar `ext` param +
multi-ext exists-check), `api/rename.py`.

### W2. AnimeTosho ❌ Skipped — verified redundant
Verified the API before building (as promised). The JSON feed
(`feed.animetosho.org/json`) is clean and cross-refs AniDB ids
(`anidb_aid/eid/fid`) — great for *finding* a release — **but subtitles are not
in it**. Extracted subs live only on the HTML view page as
`/storage/attach/…_track3.und.ass.xz` (xz-compressed, language usually `und`), so
retrieval is an HTML scrape, not an API call. And crucially those extracted subs
ARE the torrent's EMBEDDED subs — exactly what **W1 already pulls from the user's
own files, locally, free, with real MediaInfo language tags**. So AnimeTosho-for-
subs is a fragile scrape to get something we already have. Skipped as redundant.

### W3. Subtitle aggregator + YIFY scraper ✅ Shipped
A small registry — `subtitles/aggregate.py:fetch_subtitles()` — is now the single
place the rename hook calls: it runs the enabled sources cheapest-first
(embedded → OpenSubtitles → YIFY), each skipping a language already on disk, each
wrapped so one source failing can't block the others or the rename. Per-source
toggles: `subtitles.embedded` (default ON), OpenSubtitles (whenever a key
exists), `subtitles.yifysubtitles` (default OFF — it's a scraper).

YIFY (`subtitles/yifysubtitles.py`) is the one HTML-scraper source, built after
**verifying the live site** (no Cloudflare): `/movie-imdb/tt<id>` listing → the
language is embedded in each `/subtitles/<slug>` link → download
`/subtitle/<slug>.zip` and unzip the `.srt`. Movies only (IMDb id pulled from the
match's metadata blob); no HTML parser needed (the slug carries the language).
+8 tests. CAVEAT stands: it's a scraper and will break if the site changes
markup. Touch: `subtitles/{aggregate,yifysubtitles}.py`,
`providers/opensubtitles.py` (sidecar `ext`), `api/rename.py`.

### W4. Remaining scrapers (addic7ed / tvsubtitles / supersubtitles) ⏳ Deferred
Per the "build one, live-verified" decision — not built. They plug into the same
aggregator if/when wanted, but each needs live reverse-engineering + ongoing
upkeep, and addic7ed needs login + Cloudflare (likely non-functional without a
bypass lib). Recommendation: run Bazarr for the long tail.

---

## Pass X — Artwork sources (fanart.tv) ✅ Shipped

The "Download artwork" toggle used to fetch only the matched provider's poster +
TMDB's backdrop — so TV (TVDB) and anime (AniDB) matches got **poster only, no
fanart**, and richer types (clear logo, clear art, banner, disc) were never
available. Now fanart.tv is wired in as a dedicated artwork source.

### X1. fanart.tv provider ✅ Shipped
`providers/fanarttv.py` — verified live against the official client's type defs
(github.com/fanart-tv/fanart.tv-api): base `webservice.fanart.tv/v3`, `?api_key=`
(+ optional `client_key`), movies `/movies/{tmdb|imdb}`, TV `/tv/{thetvdb}`. Maps
fanart.tv's typed arrays → Kira's local-asset *kinds* (poster, fanart, clearlogo,
clearart, banner, landscape, disc, characterart) and picks the best image per
kind (language preference, textless for backgrounds, then community `likes`).
Artwork-only (no search) → lives outside the matcher registry. +12 tests.

### X2. Rename-hook integration + per-type options ✅ Shipped
`_download_artwork_files` now consults fanart.tv for the rich kinds and backstops
poster + background with the matched provider (so the toggle still works with no
key). Anime resolves its TVDB id via the Fribb cross-ref → fanart.tv `/tv`. Files
land as `<stem>-<kind>.<ext>` (`.png` for logo/clear-art/disc/character, `.jpg`
else). Two per-batch caches: one fanart.tv call per series id, one image fetch
per URL (a 24-episode season → 1 API call + 1 download per artwork). Settings →
Naming gains an artwork-type picker (poster + background + clearlogo default on);
the fanart.tv **API key lives in Connections** with the other provider
credentials (its own ProviderCard, status from whether a key is set; masked, with
`client_key` added to the secret markers). `naming.artwork_types` dict mirrors the
NFO-field picker, and the Naming picker links to Connections for the key. +10 tests. Touch:
`api/rename.py`, `api/settings.py`, `providers/fanarttv.py`,
`pages/SettingsPage.tsx`, `tests/test_fanarttv.py`, `tests/test_artwork_download.py`.

**Suite: 703 passed** (+22 across two new test files).

> Known follow-up: artwork is written **per file** (correct for movies; for TV it
> repeats the show art beside every episode rather than once at the show/season
> folder root — the standard Plex/Kodi layout). The per-batch image cache makes
> this cheap (one download, reused), but show-level placement is a cleaner model
> if it ever matters.

### X3. Polish bundle ✅ Shipped
Three small quality wins:
- **Subtitle-source toggles** — Settings → Integrations → Subtitles now exposes
  the per-source switches the backend aggregator already honored: embedded
  extraction (on) + YIFY scraper (off), alongside the existing OpenSubtitles
  fields + auto-download master. (`pages/SettingsPage.tsx`.)
- **fanart.tv "Test connection"** — the Connections card gained a working Test
  button. fanart.tv is artwork-only (not in the matcher registry), so the
  `/providers/{provider}/test` route was relaxed from the `ProviderKey` enum to
  a string + a fanart.tv branch that pings its API with the saved key
  (`fanarttv.test_key` distinguishes 200 vs 401, unlike `fetch_artwork`).
- **CORS-on-500 hardening** — `_catch_errors_mw` (registered INSIDE CORS) turns
  an unhandled exception into a normal 500 Response that flows back out through
  CORSMiddleware and gets the CORS headers. Previously a raised exception
  bypassed CORS's decoration, so a cross-origin 500 reached the browser as a
  misleading "Failed to fetch" that hid the real error (the trap behind the
  Sonarr-test masked-key bug). The traceback still prints to the server log.
  Touch: `main.py`, `api/settings.py`, `providers/fanarttv.py`,
  `pages/SettingsPage.tsx`, `tests/test_fanarttv.py`, `tests/test_cors_error_handling.py`.

**Suite: 707 passed.**

---

## Pass Y — MediaInfo surfacing + smarter dupe-keep ✅ Shipped

`parser/mediainfo.py` already extracts HDR · codec · audio-codec · channel-layout
· resolution · duration (opt-in `parsing.read_mediainfo`), but they were dropped
at the UI / NFO / ranker layers. This pass spends what's already read.

### Y1. File-row chips ✅ Shipped
The cover-popup file rows + the duplicate-resolver cards now show **HDR**
(amber-accented), **channel layout** (5.1 / 7.1), and the **primary audio codec**
(TrueHD / DTS-HD …) alongside the existing quality / source / codec / release-group
tags. The backend already serialized the full `parsed_data` blob (`MediaFileOut.
parsed_data` is a raw dict), so this was frontend-only: typed `hdr`/`channels`/
`audio` onto `ApiParsedData` + `MediaFile`/`LibFile`, mapped in the adapter (both
directions), rendered in both chip sites. Touch: `lib/api.ts`, `lib/types.ts`,
`lib/adapters.ts`, `components/CoverPopup.tsx`, `index.css`.

### Y2. Smarter duplicate "keep best" ranker ✅ Shipped
`rankFile` already ordered by resolution → source → codec → bit-depth → file-size;
it ignored the two MediaInfo signals it now has. Added **HDR** (right after
resolution: any HDR grade beats SDR; DV > HDR10+ > HDR10 > HLG) and **audio
channels** (after bit-depth: more speakers win), so the duplicate-resolver's
auto-picked "primary" copy reflects real A/V quality, not just bytes. Touch:
`components/CoverPopup.tsx`.

### Y3. NFO `<streamdetails>` ✅ Shipped
NFO output now emits Kodi/Emby `<fileinfo><streamdetails><video>` (codec, width/
height from the resolution label, `hdrtype`, `durationinseconds`) + `<audio>`
(codec, channel count) from the file's own tech data — filename-derived when
that's all there is, richer with MediaInfo on. New toggleable `streamdetails`
NFO field (default on); emits only what's known. Touch: `renamer/nfo.py`,
`api/rename.py`, `pages/SettingsPage.tsx` (NFO-field picker), `tests/test_nfo.py` (+5).

**Suite: 715 passed** (+8). **Honest caveat:** HDR / channels / audio populate
only when `parsing.read_mediainfo` is enabled (off by default — it opens every
file). Codec + resolution come from the filename too, so chips/streamdetails are
never empty; the new signals just get richer with MediaInfo on.

---

## Pass Z — Docker packaging (the "Docker-native" promise) ✅ Shipped

The tagline was "self-hosted, **Docker-native**," but there was no Dockerfile,
no compose, and the backend didn't even serve the frontend (two dev servers).
This delivers a real single-image deploy.

### Z1. Backend serves the built SPA ✅ Shipped
`kira.main` now serves the built React app **same-origin** (one container, one
port, no CORS) when a `frontend/dist` exists (`KIRA_FRONTEND_DIST`). Implemented
as a **404-aware exception handler**, NOT a catch-all route — a catch-all
`/{path}` would be matched before any route registered later and shadow it
(it broke the CORS-test's dynamic probe). The handler fires only on genuine
404s: non-API GET → real built file or the `index.html` shell (so deep-links /
hard-refresh on `/review` work); `/api` 404s stay JSON with their real `detail`.
In dev it no-ops (no dist → Vite serves). `frontend/.env.production` sets
`VITE_API_BASE=/api/v1` so the built bundle calls the API relative. +4 tests
(`test_spa_serving.py`, skipped when unbuilt). Touch: `main.py`, `.env.production`.

### Z2. Dockerfile + compose ✅ Shipped
Multi-stage `Dockerfile`: stage 1 (`node:22-slim`) `npm ci && npm run build`;
stage 2 (`python:3.12-slim`) `apt ffmpeg + libmediainfo0v5`, `pip install ./backend`,
copy the built SPA, run one uvicorn loop. `docker-compose.yml` (config + media
volumes, port 8000, env for keys / auth / `KIRA_BROWSE_ROOT=/media`, a
python-based healthcheck) + `.dockerignore`.

**Verified:** the frontend production build (`vite build` → `dist/`), the backend
SPA serving (`/` + `/review` → shell, real files served, `/api` 404 → JSON), and
the full suite with the handler active — **719 passed**. **Honest caveat:** I
could not run `docker build` in this environment (no daemon), so the image
assembly itself (apt + pip layers, multi-stage copy) is unverified — run
`docker compose up` once to confirm; the app-level wiring it depends on is tested.

---

## Pass AA — MediaInfo enrichment as a background process ✅ Shipped

MediaInfo (true resolution/codec/HDR/channels/audio read from the file
container) was on the **match critical path**: with `parsing.read_mediainfo` on,
the matcher opened every tag-less file inline — a NAS round-trip each — so the
feature was off by default to keep scans fast. The "authoritative" mode (let the
container OVERRIDE a mislabelled filename) was worse: it reads EVERY file. This
moves the whole read off the critical path.

### What it found (a half-built, broken seam)
`/files/reparse-all` imported `_read_mediainfo_authoritative_setting` (which
didn't exist in `scans.py`) and called `_maybe_enrich_mediainfo` with 4 args (it
took 3) — so the endpoint **crashed the instant it was invoked**. And the
Settings UI already had an "Authoritative tech tags" toggle wired to
`parsing.mediainfo_authoritative`, a key **no backend code ever read**. Two dead
seams pointing at a feature that was never finished.

### AA1. Background enrichment pass ✅ Shipped
New `enrich_mediainfo_background(file_ids)` (own `SessionLocal`, off any request/
scan path): reads each file's container off the event loop, **paced**
(`sleep` between files so a big backfill never monopolises the worker), fully
exception-isolated (one slow NAS / bad file rolls back and the rest continue),
commits per file so the UI fills in incrementally. Detached via
`_spawn_mediainfo_enrich` with a strong task-ref set + done-callback (asyncio
only weak-refs a bare `create_task`, so without it the task can be GC'd
mid-flight). Writes only `parsed_data` — the enriched fields don't feed
`series_key`/`variant_key`/`media_type`, so nothing else recomputes and there's
no UNIQUE-collision risk.

### AA2. Off the critical path + finished wiring ✅ Shipped
- Removed the inline tech-tag read from `_match_phase` (kept the
  matching-essential embedded-title rescue, which is bounded to unidentifiable
  files). The scan now finishes on filename data alone; chips + the dupe-ranker
  **sharpen on the next `/files` poll**.
- Kick off the background pass at **scan completion** (all sources), at
  **reparse-worker** completion, and from **`/files/reparse-all`** (which now
  does the cheap regex reparse synchronously, then hands the container reads to
  the detached pass — so it returns immediately even in authoritative mode).
- Added the real `_read_mediainfo_authoritative_setting` reading
  `parsing.mediainfo_authoritative` (the key the UI already used → the toggle now
  does something); made `_maybe_enrich_mediainfo` 4-arg (`authoritative`) and
  back-compatible.
- Fixed a Settings honesty bug: the card defaulted "Read file metadata" to ON
  while the backend treats unset as OFF — now both default OFF. Reworded the
  toggles ("Background" not "Perf cost"; "runs in the background, never slows a
  scan").

### AA3. Visible progress — "how much has processed?" ✅ Shipped
A background pass that prints only to the server log is invisible to the user.
The pass now publishes live progress to the existing activity surface
(`activity.begin/progress/end` → `GET /api/v1/activity`): the frontend's
always-mounted poller already renders any active job as the bottom-left glass
pill, so it shows **"Reading file media info · N/total"** with no frontend
changes. `begin` fires only AFTER the enabled-check (a disabled/no-op run never
flashes an empty pill); `progress` after every file (also keeps the job from
being marked stale during a slow NAS read); `end` in a `finally` (clears the
pill even on early-abort). Stable job name → reused per run, never accumulates.

### AA4. Enabling the toggle backfills the EXISTING library ✅ Shipped
The triggers above (scan / reparse) only cover NEW or re-parsed files — so a
user who just flipped the toggle on saw *nothing happen* to their already-
scanned media, which reads as broken. `put_settings` now kicks off the
background pass over **every current file** the moment the read turns on (or
authoritative turns on while read is already on). It captures each toggle's
prior value during the upsert and fires ONLY on a genuine OFF→ON flip of a key
actually present in the payload — so an unrelated save / whole-object re-PUT
never re-reads the whole library.

### AA5. Durable completion record — "did it finish? did it cover everything?" ✅ Shipped
The live pill was the ONLY signal, and a fallback pass over a library whose
filenames already carry tags finishes in seconds — often *between* the activity
poller's ticks — so the user saw no pill, no notification, nothing in the
dashboard's "Recent activity" and reasonably concluded it was broken. The
enable-triggered pass (`reason="settings"`) now writes a **durable
Notification** on completion — which surfaces in BOTH the notifications popover
and the dashboard "Recent activity" feed (it reads notifications). It fires even
when **0 files changed** ("Read media info for N files; nothing changed — your
filenames already carried these tags"), because *that* is the reassurance that
it ran and covered the whole library. And if the user enables the toggle while
the native MediaInfo lib is **missing**, they get an explanatory warning instead
of silence (the one trap that would otherwise look identical to "broken").
Per-scan / reparse runs stay quiet (they have their own completion signals and
would otherwise spam a media-info notification on every scan).

**Verified:** +21 tests (`test_mediainfo_background.py`) — disabled/lib-absent
no-ops, fallback fills only blanks, authoritative overrides, per-file exception
isolation, the key-mismatch regression, detached-spawn (with/without a loop),
the activity surface (live N/total; no job when disabled), the enable-backfill
trigger (read/authoritative on, no-op re-save, disable, unrelated save), and the
completion notification (posted with reason; quiet without; warns when the lib
is absent). Full suite **740 passed**; frontend `tsc` clean.

### AA6. Pill no longer needs a manual refresh ✅ Shipped
The activity poller (`useActivity`) runs on a lazy cadence — 12s when idle — so
after flipping the toggle the pill could take up to 12s to appear, and a page
refresh "fixed" it only because mount forces an immediate poll. Now
`api.putSettings` fires a `kira:activity-refresh` window event on success and
the hook listens for it: poll NOW instead of waiting out the interval, plus one
~1.2s retry (the detached job's `begin()` lands a beat after the HTTP response).
Single-chain polling preserved (each poll clears the prior timer), listener
torn down on unmount. Generic — any settings save that starts background work
gets the same immediacy. Frontend `tsc` clean.

---

## Fix — UTC timestamps on the wire ("everything says 5 hours ago") ✅ Shipped

Every "x ago" in the UI (notifications bell, dashboard "Recent activity",
history, scans) was off by the viewer's UTC offset — a fresh notification read
"5 hours ago" for a user at UTC+5. Cause: our timestamps are stored **naive but
mean UTC** (`func.now()` / `datetime.now(timezone.utc)`), and the API emitted
them ISO-**without a timezone**, so the browser's `new Date("…")` parsed them as
**local** time. Confirmed the frontend never compensated anywhere (so the skew
was uniform, and a wire-level fix is safe — no double-correction).

Fix is one reusable Pydantic type — `UtcDateTime` (`schemas.py`): a
`PlainSerializer` that treats a naive value as UTC (and converts an aware one),
emitting ISO with a trailing `Z`. Applied to every timestamp field that crosses
the wire — `ScanOut`, `MediaFileOut`, `NotificationOut` (system.py), `HistoryOut`
(history.py). The browser now parses the instant correctly with **no frontend
change**. Validation is unchanged (`when_used="json"` → only output gains the
`Z`), so it's fully backward-compatible.

**Verified:** +5 tests (`test_utc_timestamps.py`) — naive→UTC, aware→UTC
normalization, `Z` on Scan/Notification output, null `completed_at` preserved,
round-trip to the same instant. Full suite **745 passed**.

---

## Pass AB — Per-stream audio/subtitle languages ✅ Shipped

The read-and-surface slice of candidate (c) — extends the MediaInfo work so a
file's real per-track languages show up, not just what the filename guessed.

### AB1. Read every track's language ✅ Shipped
`read_media_info()` now walks ALL audio + text tracks (no early break) and
collects each track's `language`, normalized by `normalize_language()` to a
canonical ISO-639-2/B code (`en`/`eng`/`English`/`en-US` → `eng`; `und`/`mul`
and unknown long names dropped; unknown short codes pass through). New
`ParsedFile.audio_langs`/`sub_langs` (defaulted lists → backward-compatible with
old `parsed_data`), mapped in `enrich_parsed` (authoritative overwrites, fallback
fills). The background pass already populates them — no new trigger needed.

**Also removed a bound that defeated this:** `_maybe_enrich_mediainfo` used to
SKIP the container read for any file whose filename already carried a quality
tag (an I/O optimization for the quality-fill case). That also skipped
channels / duration / **languages**, which have no filename source — so most
files (which carry a quality tag) would never get language chips. Now the
background pass always reads the file it's handed (paced + off-critical-path);
fallback still keeps the filename's quality, it just no longer skips the read.

### AB2. Chips + NFO ✅ Shipped
- **Chips**: dual/multi-audio (`JPN+ENG`, cyan accent) shown when ≥2 audio
  languages; subtitle chip (`SUB ENG+SPA`) when any are present — on both the
  file rows and the duplicate-resolver cards (capped at 3 + `+N`). Plumbed
  through `ApiParsedData` → `MediaFile` → `LibFile` + both adapter sites.
- **NFO `<streamdetails>`**: one `<audio>` per language (the first carries the
  primary codec + channels) and one `<subtitle><language>` per sub track — what
  Kodi/Emby read to flag a file's tracks.

**Deferred (noted on candidate c):** folding real audio languages into the
`variant_key` — it's computed at scan time but languages arrive post-scan, and it
feeds a UNIQUE grouping; a careful follow-up, not a bolt-on.

**Verified:** +8 tests — `normalize_language`, enrich fill/override of the lang
lists, `read_media_info` collecting+deduping across tracks (mocked `_MediaInfo`),
NFO per-track `<audio>`/`<subtitle>` output, and that a quality-tagged file now
still gets its languages read (the skip-removal). Full suite **753 passed**;
frontend `tsc` + `vite build` clean.

---

## Fix — streaming platform tags miscounted as the source ✅ Shipped

Surfaced by a real dupe-resolver suggestion: for S03E01 of Euphoria the resolver
suggested keeping a **WEBRip** over an **AMZN WEB-DL REPACK** (DDP5.1 Atmos,
FLUX) — the wrong call (WEB-DL > WEBRip, and the WEB-DL had better audio + was
the corrected REPACK). Root cause: the parser's `SOURCES` table lumped streaming
**platform** tags (AMZN, NFLX, DSNP, HULU, ATVP, PCOK, PMTP, CRAV) together with
real **delivery types** (WEB-DL, WEBRip, BluRay…), and extraction took whichever
matched first. In `…AMZN.WEB-DL…` AMZN won → `source="AMZN"`, which the dedupe
ranker's `_SRC_RANK` doesn't recognize → scored as worst (9) → the WEBRip (3)
beat it on the source tier. The ranker's ordering was right; it just never
received "WEB-DL".

Fix (`format_stripper.py`): split platforms into their own `PLATFORMS` list
(in-code; also removed from `release_tokens.json` `sources`). They're still
stripped from titles, but a platform only *defines* the source when no real
delivery type is present — then it implies **WEB-DL** (a platform tag alone is
virtually always a web download). Streaming members of the ambiguous set
(HMAX/NF/STAN) route the same way; `MAX`/`TS`/`BD` and the "Max" title guard are
untouched. Also fixes the `{{source}}` naming token (renames now read `WEB-DL`,
not `AMZN`). Note: the platform label no longer shows as its own chip — folded
into the source; a dedicated `{{platform}}` token/chip is a possible follow-up.

**Verified:** the two real filenames now parse `WEBRip` vs `WEB-DL` (so the
resolver flips to the FLUX file), plus platform-only→WEB-DL, disc sources
untouched, and the "Max" title preserved. +6 tests (`test_format_stripper.py`).
Full suite **762 passed**. (Needs a backend restart + a re-parse for existing
rows to pick up the corrected source.)

---

## Fix — three dupe / Sonarr / pill UX issues ✅ Shipped (frontend)

Three rough edges the user hit in real use:

1. **Stale duplicate sign after deleting a dupe.** The popup deletes optimistically
   via local `deletedIds`, but that state is lost on close and the *global*
   `state.files` cache was never refreshed — so reopening showed the (now-gone)
   duplicate until App's slow poll caught up. Fix: both delete handlers now
   dispatch a new `kira:files-changed` event; App listens and re-pulls the files
   list (a *light* refresh — NOT a full disk scan), so the dupe group is gone for
   good on reopen and the card's dupe sign clears immediately.

2. **Sonarr import race — we scanned once, too early.** When a download's queue
   entry vanished we dispatched a single (debounced) rescan; if the import
   actually landed seconds-to-minutes later (slow move / NAS propagation /
   post-processing), that scan missed it and nothing re-scanned → the file sat
   un-indexed. Fix: the rescan handler now runs a **bounded retry sequence** —
   scan, and if no new files appeared, retry at +30s and +90s — stopping early
   the instant the file count grows (`refreshFiles` now returns the list so the
   check is render-race-free). Coalesces bursts; a scan already in flight isn't
   stacked.

3. **Downloading pill hidden behind the cover title.** The cover card's Sonarr
   activity pill was `z-index:2`; the cinema-mode title overlay (`.cc-meta`) is
   `z-index:5` and paints over the bottom of the cover where the pill sits. Since
   `.cc-cover` isn't a stacking context (its hover-transform is overridden to
   none), bumping the pill to `z-index:6` lifts it above the title in every state.

Frontend `tsc` + `vite build` clean; backend untouched (still 762). Needs a
frontend reload to take effect.

---

## Fix — prune files that vanished from disk (the missing "sweep") ✅ Shipped

A file deleted from disk (manually, or by Sonarr's cleanup) lingered in Review
forever: the scanner only ever ADDED/updated discovered files — it never removed
rows for files that disappeared. A rescan didn't help, and a page refresh just
re-read the stale DB row.

Added the missing **mark-and-sweep** to `_scan_worker`: the walk is the "mark"
(`walked_paths_this_scan`), and new `_prune_missing_files` is the "sweep". A
tracked row is dropped only when ALL hold — its path is **under a scanned root**
(never touches libraries this scan didn't cover), the walk **didn't see it**, and
a `stat()` raises **FileNotFoundError** (CONFIRMED gone; a permission / NAS error
is treated as "can't tell → keep"). It reuses the manual-delete path
(`_delete_one(keep_on_disk=True)`) so Matches cascade and RenameHistory is
preserved; nothing is removed from disk (the file's already gone). A notification
records what was removed.

**Safety gate (this is the dangerous one — a NAS blip must not wipe the
library):** the sweep runs ONLY after a **fully healthy walk** — no unreachable
root (`dead_roots`) and no scandir error (`scanner.get_walk_errors()`). If any
part of the tree was unreadable, "not seen" ≠ "deleted", so it skips pruning
entirely. The per-file FileNotFoundError-only check is the second guard.

**Verified:** +3 tests (`test_prune_missing_files.py`) with REAL temp files —
prunes the confirmed-missing file, keeps a present-but-unwalked (filtered) file
via the stat() check, keeps files outside the scanned roots, and posts the
notification. Full suite **800 passed**. (Triggers on the next scan — a deleted
file clears on rescan, not on a bare page refresh; deleting *inside* Kira already
clears immediately via `kira:files-changed`.)

---

## Fix — CoverPopup cover flash ~1s after OPEN ✅ Shipped (frontend)

The popup flashed the blurred background-bleed through the hero cover ~1s after
opening. (First two attempts misdiagnosed it as a *close* glitch — it's on
OPEN.) Real cause: `Hero` gated the cover `<img>` on `settled`
(`{settled ? <img> : null}`), so the image only MOUNTED at the fly-in handoff.
At that instant `handleFlyEnd` hides the flying cover and reveals the in-flow
slot — but the slot's freshly-created `<img>` hasn't decoded/painted yet, so for
a frame the foreground cover is gone and the `cx-bg-bleed` (the blurred copy of
the same cover behind it) shows through — the flash. The "atomic swap" the
comment intended wasn't atomic because the image wasn't paint-ready.

Fix (`Hero.tsx`): mount the cover `<img>` DURING the flight (the slot is
`opacity:0` via CSS until `settled`), so it's loaded + decoded by the time the
slot flips visible — the flyer hands off to an already-painted cover, no decode
gap. The misdiagnosed close-side change was reverted. `tsc` + `vite build` clean.

---

## Fix — undo orphaned artwork/NFO (rename→undo→rename pile-up) ✅ Shipped (backend)

Symptom (user): "renamed a movie, undo it, renamed again… multiple times and I
end up with so many files in that folder." An *Evil Dead Rise (2023)* folder held
the MKV plus THREE artwork sets named after three different rename targets
(`…[720p WEBRip]-{poster,fanart}`, `…[1080p WEBRip]-{poster,fanart,landscape,logo}`,
`…PSA-{poster,landscape,backdrop,logo}`) + two `.nfo` files.

Root cause: each rename writes `<target_stem>-<kind>.<ext>` artwork (`rename.py`)
and `<target_stem>.nfo`, but `RenameHistory` only tracks the video move + subtitle
sidecars (own rows via `parent_id`) — **NOT** the artwork/NFO. So undo reverted the
video and left the artwork/NFO orphaned; the next rename wrote a *fresh* set under
the new target's stem → accumulation, one set per attempt.

Fix (`api/history.py`): new `_remove_orphaned_assets(video_new_path, roots)` called
from both `undo_entry` and `undo_bulk`, for primary video rows only (`parent_id is
None`; subtitle children carry no artwork). Because the names are deterministic from
the video's own stem, it removes EXACTLY what Kira wrote — matched on the literal
stem prefix `<stem>-*.{jpg,jpeg,png,webp}` plus `<stem>.nfo` — and nothing else:
the generic Kodi assets (`folder.jpg`/`backdrop.jpg`, no stem prefix), subtitle
sidecars (stem-prefixed but not images), and other titles' artwork all survive.
Stem-prefix matching (via `iterdir` + `str.startswith`, NOT `Path.glob`) deliberately
sidesteps two traps: (1) kind-name drift — on-disk `-logo`/`-backdrop` aren't even in
the current `ALL_KINDS`, so a kind-list loop would miss them; (2) bracketed stems
like `… [1080p WEBRip]` being mis-read as glob character classes. Guarded by
`path_under_roots` (never deletes outside a managed library root) and fully
best-effort (per-file try/except, off-thread `iterdir`/`unlink`, never raises). The
undo notification now reports the count removed.

Scope note: this fixes the reported rename→undo→rename cycle (every undo now cleans
up after itself). It does NOT retroactively clean orphans already on disk from
*past* undos, nor the rarer rename→**re-rename-to-a-different-target** case (no undo
in between) — flagged as a follow-up if it surfaces.

New tests `tests/test_undo_orphan_cleanup.py` (3): removes artwork+NFO / keeps
video+generic+subtitle+other-title; bracket-stem matched literally; roots guard
refuses outside-library deletion. Full history/undo/rename/nfo/artwork suite green
(120 passed).

> **Superseded by the Rename-hardening pass below.** The stem-derived sweep is now
> the *fallback* for legacy rows; new renames record exact `created_assets` and undo
> deletes those authoritatively. The forward re-rename case is now handled too.

---

## Pass — Rename-core hardening (the "main thing") ✅ Shipped (backend)

After the orphan band-aid, the user asked what else the **rename itself** — the core
of the app — needs. A read of `perform_rename` surfaced one structural weakness
(it *creates* satellite files it doesn't *record*) plus a cluster of correctness/
safety gaps. All seven were fixed, each with tests, full suite green (**831 → passing**).

**First, a test net (prereq).** The suite only *spied* on `perform_rename`
(`test_auto_rename_execute`) or checked route binding (`test_rename_route`) — nothing
drove a real rename. Added `tests/test_rename_e2e.py`: real temp files + real SQLite,
exercising move/copy/dry-run, sidecar co-move, history rows, and NFO — the behavioral
net that made the later refactor safe. Grew to 18 cases across the pass.

**#1 — `created_assets` provenance → authoritative undo** *(the headline; supersedes the
band-aid).* New `RenameHistory.created_assets` JSON column (migrated via
`_MIGRATION_COLUMNS`). `_write_nfo_files` + `_download_artwork_*` now RETURN the paths
they wrote; `perform_rename` records them on the video's history row. Undo
(`_cleanup_entry_assets`) deletes the **recorded** paths exactly — no stem-derivation
drift — falling back to the old sweep only for legacy (null) rows. The per-file NFO +
`<stem>-<kind>` artwork are tracked; the shared `tvshow.nfo` is deliberately NOT (other
episodes need it). Plus a **forward-orphan sweep** (`sweep_superseded_assets`): re-renaming
a file to a different target with no undo in between now removes the prior target's
recorded assets — closing the case the band-aid couldn't.

**#2 — never move an untrackable sidecar.** If the flush that assigns the parent
history id failed, the sidecar was still physically moved but got no history row →
undo orphaned it. Now: no parent id → the sidecars are left in place with a clear
note, never moved without a row to undo them by.

**#3 — in-batch duplicate-target guard.** Two source files rendering to the same
destination used to silently overwrite (overwrite on) or error obscurely (off). A
`claimed_targets` map (normalized via `webhooks._norm`) now fails the second
collider with a pointer to the first — no clobber, no data loss. Surfaced in dry-run too.

**#4 — write-ahead intent journal + boot reconcile.** The physical move commits to
disk before the DB row does; a crash in that window diverged disk vs DB with no
repair. New `RenameIntent` table: an intent is committed *before* the move and deleted
in the *same* commit that persists the row. On boot, `reconcile_pending_renames()`
(wired beside `reconcile_orphaned_scans`) finalizes intents whose move landed
(dst present, src gone → fix `file_path` + add a recovery history row if missing) and
discards those whose move never ran. Crash-recoverable renames.

**#5 — deterministic match selection.** When nothing is `is_selected`, the fallback was
`f.matches[0]` (arbitrary relationship order → could rename to the wrong match,
non-reproducibly). Now: highest `confidence`, ties broken by lowest id.

**#6 — dry-run previews the full footprint.** `RenameItemResult` gained optional
`sidecars` / `nfo` / `artwork` lists, populated on dry-run, so the preview shows every
side effect (subs that would move, NFO that would be written, artwork kinds) — not just
the destination path. Null on real runs → existing consumers unaffected.

**#7 — extracted `_rename_one_file`.** The ~550-line per-file loop body is now a named
unit (a thin dispatcher loop calls it); each terminal outcome is an explicit `return`
instead of an `append`+`continue` buried mid-loop. Chose the nested form (zero-dedent,
compiler-verified `continue`→`return`) over a riskier module-level re-flow on the app's
most dangerous function, since branch-level testability was already delivered by the
e2e net.

Tests: `tests/test_rename_e2e.py` (18) covers move/copy/dry-run + preview, deterministic
selection, `created_assets` recording + authoritative undo + forward sweep, untrackable
sidecar, duplicate-target, and all three reconcile branches; `tests/test_undo_orphan_cleanup.py`
(5) covers the dispatcher (recorded vs legacy-derived) + the original sweep cases.

Scope notes carried forward: existing on-disk orphans from *past* undos aren't
retroactively cleaned (the fix is forward-looking); the intent journal covers the video
move (sidecars carry their own history rows).

---

## Fix — re-submitted rename = idempotent no-op (the "twice in history" report) ✅ Shipped (backend)

User renamed one movie but saw **two** history rows: `Z:\…` → `\\192.168.0.63\Data\…`
(the real rename — `Z:` is a mapped drive for that UNC share) followed by
`\\192.168.0.63\…` → `\\192.168.0.63\…` (a `src==dst` **self-move**). DB confirmed one
`MediaFile` (id 480), zero intents → `perform_rename` simply ran **twice** on the same
file (a frontend double-trigger), and the second pass moved the file onto itself and
recorded a pointless row.

Fix (`_rename_one_file`): after the phantom guard (src known to exist), if `src`
already equals `target` it's a no-op — mark renamed, **no move, no history row**, return
the same "[PHANTOM] Already at target" result the genuine already-at-target branch uses.
Comparison is **separator-normalized but CASE-SENSITIVE** — a case-only rename
(`Movie.MKV` → `movie.mkv`) is a real intended op and must NOT be swallowed, so it
deliberately does NOT reuse `_norm` (which case-folds). This makes the rename idempotent:
re-submitting (or the file already living at its destination) can't manufacture phantom
history or self-moves.

Note: the *root* double-trigger is on the frontend (CoverPopup's debounced per-row
flush overlapping a direct approve/`onUpdate` call) — now harmless, flagged as a
separate follow-up. Test: `test_re_submitting_same_rename_is_noop_no_duplicate_history`
(re-run after the file is at its target adds no second row); the 11 case-rename tests
still pass (case-only renames proceed).

---

## Feature — library-wide artifact sweep (post-organize media-server junk) ✅ Shipped

User found a `Bleach/Season 17/` folder full of `<episode>-thumb.jpg` files Jellyfin/Plex
generated AFTER Kira organized the library, with no way to clear them. Root cause: the
existing folder cleanup (`_cleanup_empty_source_parents`) only fires after a **Move** and
only removes folders that become **entirely artifacts/empty** — it never strips artifacts
OUT of a folder that still holds videos, and media servers keep dropping new ones in long
after the rename. So there was no mechanism to clean a populated, already-organized folder.

New `operations.sweep_artifacts(roots, *, dry_run, trash_root)` — walks the managed roots
and removes media-server artifacts (`<stem>-thumb.jpg`, `poster.jpg`, `.tbn`, `.actors/`,
season art, …) from folders that still contain media, leaving the videos. **Allow-list
ONLY** (reuses `_is_artifact_file`/`_is_artifact_dir`): videos, subtitles, and anything
Kira doesn't positively classify as a server artifact are never touched; artifact dirs are
removed whole and not descended into; honors the recoverable-trash setting.

Surfaced two ways (user picked "both" + full allow-list):
- **On-demand**: `POST /api/v1/cleanup/artifacts` (`api/cleanup.py`) — `dry_run` defaults
  TRUE (preview). Settings → Folder cleanup gains a "Find leftover artifacts" button that
  previews the count + a sample, then a destructive "Delete/Move N" confirm.
- **On-scan (opt-in)**: `cleanup.sweep_artifacts_on_scan` (default OFF) — `_scan_worker_locked`
  runs the sweep after each scan + auto-rename settles, best-effort, with a notification.

Safety: preview-before-delete, allow-list only, recoverable-trash option, scoped to managed
roots (`_managed_roots`), blocking walk offloaded to a thread. Tests `tests/test_sweep_artifacts.py`
(5): removes artifacts / keeps media+subs+user files; dry-run deletes nothing; trash mode
moves not deletes; only walks given roots; endpoint preview-then-delete via dependency override.
`tsc` + `vite build` clean.

---

## Fix — undo cleans up the folders it created + anime cours unify into one show ✅ Shipped (backend)

Two coupled real-library reports after undoing/renaming Bleach TYBW.

**A. Undo left empty Show/Season folders + show-level files.** Undo reverted the video
(+ tracked artwork/NFO) but never removed the destination folders Kira created, leaving
`Bleach - Thousand-Year Blood War/…` shells with `tvshow.nfo`/`poster.jpg` inside. New
`_cleanup_undo_vacated_folders` (history.py) walks UP from the vacated `new_path` and
removes folders that are empty or ENTIRELY media-server artifacts (reusing the move-time
`_cleanup_empty_source_parents` walker), bounded by the managed root, honoring trash. A
folder that still holds media stops the walk. Wired into `undo_entry` + `undo_bulk`. Tests
`tests/test_undo_folder_cleanup.py` (3): removes empty show+season; keeps a show whose
sibling season still has media; never touches outside a managed root.

**B. AniDB cours fragmented into separate shows on disk.** AniDB gives each cour its own
AID + title, so renaming Bleach TYBW produced THREE folders (`…`, `… - The Separation`,
`… - The Conflict`) — even though the Review page groups them via `series_group_id`. Root
cause: the rename's collapse called `AniDBProvider._pick_display_title(group_root)`, which
needs the in-memory AniDB title dump loaded — at rename time it isn't, so it returned None
and the collapse silently no-opped, leaving each cour its own title. (Confirmed None for all
the user's AIDs.)

Fix (`_rename_one_file` + new `_anime_group_members`): unify the show folder to the title of
the EARLIEST member PRESENT in the library (lowest AID), read from the Match rows already in
the DB — reliable, no dump. For TYBW → "Bleach: Thousand-Year Blood War"; for AoT → "Attack
on Titan". Layout inside follows the user's `naming.anime_numbering`:
- **seasonal** (default): each cour → its own sequential season (by AID/air order), so the
  cours' overlapping per-cour episode numbers don't collide once unified (TYBW = S01/S02/S03);
- **absolute**: the existing franchise-absolute machinery flattens to continuous episode
  numbers under the one show; season left alone.

Only for genuine multi-cour anidb groups (>1 member present); manual pins still win verbatim
(and the new title source preserves a manual pick rather than overriding it to the franchise
root, fixing the old "Bleach: TYBW → Bleach" footgun by construction). Test
`test_anime_cours_unify_under_one_show_with_per_cour_seasons` (3 cours → one folder, S01/02/03,
no per-cour suffix). Forward-looking: existing fragmented folders are cleaned by undoing then
re-renaming (A cleans the shells, B unifies).

---

## Fix — folder-cleanup default: backend now matches the UI ✅ Shipped (backend)

User renamed Bleach (cours relocated `Bleach/Season 17/` → `Bleach - Thousand-Year Blood War/
Season 0X/`) and Euphoria (old season folders → `Euphoria (US)/Season N/`), but the emptied
SOURCE folders were left behind and there was no prompt. Root cause: a UI↔backend default
mismatch. The Settings toggle "Remove empty folders after Move" renders **ON** by default
(`SettingsPage` `masterOn` → `return true`), but `_resolve_cleanup_empty_dirs` defaulted
**FALSE** — so a user who never touched the setting SAW cleanup enabled yet the backend
silently skipped it. Flipped the backend default to TRUE to match the UI.

Safe to default on now (the reasons it was turned off are fixed): the walk is depth-capped
per media type (movie 1 / tv+anime 2, not the old 6-level over-walk) and computes removability
FIRST (#62), so it only ever removes a folder that is empty or entirely media-server artifacts
and is about to go — any real content stops the walk. With the artifact sub-toggle also
defaulting on, this clears both cases: Euphoria's empty old season folders and Bleach's
Season 17 (left holding only Jellyfin `-thumb.jpg` after the cours relocated). Users can still
opt out. Full suite green.

---

## Fix — empty optional-token residue + rename progress feedback ✅ Shipped

**Empty "()" / "[_]" in names.** With the unified anime title above, the Jellyfin profile
(whose template is `{{n}} ({{y}})`) rendered "Bleach - Thousand-Year Blood War ()" because
AniDB gives the cour no year — and empty release-group brackets render "[_]" (the blank-token
placeholder). Fix in `_safe` (templates.py): strip any bracket/paren/brace group containing
only whitespace and/or `_` placeholders (plus the preceding space), then re-collapse spaces.
General — fixes every profile/template (year, rg, quality, …) where an optional token is
missing. Test `test_safe_strips_empty_optional_token_residue` (strips `()`/`[]`/`[_]`/`{}`,
preserves `(2022)`/`[1080p]`/`[EMBER]`).

**Rename progress.** A season's worth of files (+ artwork/subtitle fetches) takes real time
with no feedback. `perform_rename` now drives the existing activity surface — `activity.begin/
progress/end("rename", …)` around the dispatcher loop (real runs only; dry-run is instant) —
so the bottom-left pill shows "Renaming N files" with a live count. The two frontend rename
call sites fire `kira:activity-refresh` on start so the pill appears immediately rather than
waiting out the poll interval. Best-effort; never affects the rename outcome. `tsc` + `vite
build` clean.

---

## Where we are now + what's next

**Done:** Passes 5, M, the FileBot stretch, 6, 7, the Pass-S hardening arc,
Pass T, Pass U (real-library bug fixes), and Pass V (anime correctness for
absolute-numbered long-runners + provider/numbering settings). Matching,
automation, metadata richness, network resilience, crash recovery, self-heal, an
activity surface, and a CLI are all in. **The app is feature-complete on its core
promise ("rename, organize, done")** — every item that had a concrete plan is
shipped.

**What's left is demand-driven, not roadmap-driven.** The honest next step is
**dogfooding**: keep running it on the real library and let what actually
surfaces set the priority. Candidate directions if/when a need appears:

- **Duplicate / upgrade handling** — the in-popup duplicate *resolver* (surface
  the group, auto-rank the best by resolution/HDR/source/codec/channels/size,
  one-click resolve) ✅ ships. What's left: library-wide **upgrade detection** —
  noticing a better copy *arrived later* (a 1080p replacing last week's 720p) and
  prompting to swap, rather than only resolving dupes present in one scan.
- **Bulk review ergonomics** — keyboard-driven approve/reject sweep for large
  no-match / low-confidence queues.
- **Provider breadth** — only if a real library hits a gap the current
  TMDB/TVDB/AniDB set can't cover.
- **Packaging** — ✅ Dockerfile + compose + single-image SPA serving shipped
  (Pass Z). Remaining: a *published* image (CI build → GHCR push) so install is
  `docker run` with no clone/build; native `.exe`/`.dmg` installers stay further out.
- **Surface the MediaInfo we already read + naming-binding breadth (reference-renamer
  stretch, pt. 2)** — (a) + (b) **✅ shipped in Pass Y**; (c) + (d) remain:
  - (a) **Surface existing reads** — ✅ Pass Y1: HDR / channels / audio-codec chips
    on file rows + the duplicate-resolver cards, and NFO `<streamdetails>`.
  - (b) **Smarter duplicate resolver** — ✅ Pass Y2: HDR + audio-channels added to
    `rankFile` (resolution + source + codec + bit-depth + size were already there).
    Note: raw *bitrate* isn't extracted — file size remains the bitrate proxy
    (fine for same-episode dupes, where size ≈ bitrate).
  - (c) **Per-stream audio/subtitle languages** — ✅ **mostly shipped (Pass AB)**:
    `read_media_info()` now reads every audio + text track's `language`
    (normalized to ISO-639-2/B) → `ParsedFile.audio_langs`/`sub_langs` → dual-audio
    (`JPN+ENG`) + multi-sub chips on file rows and the dupe cards, and one
    `<audio>`/`<subtitle>` per language in the NFO `<streamdetails>`. **Remaining:**
    folding the real audio languages into the `variant` suffix — deferred because
    `variant_key` is computed at scan time while languages arrive in the post-scan
    background pass (and it feeds a UNIQUE grouping); doing it right means the
    enrich recomputing the key, a careful follow-up rather than a bolt-on.
  - (d) **`crc32` → a verification feature** — compute the file CRC and compare to
    the `[ABCD1234]` anime releases embed in the filename → "verified ✓ / corrupt"
    badge. This is the file-verification gap from the reference-renamer audit.
  - Scope note: these are the *useful* slice of the reference renamer's ~110 format
    bindings (we ported ~58 — the everyday core). The rest are intentionally
    **skipped**: niche (rating/votes/age/today), photo/music-only
    (exif/camera/location/albumArtist), or Groovy-engine internals
    (self/model/json/object/info/omdb/media-objects) that don't translate to a
    Jinja template engine.
- **Remaining FileBot-diff gaps (tracked, not built):**
  - *Locally-named → absolute output* — ✅ **SHIPPED (Pass AC).** A file named
    per-cour-local (`AoT S4E01`, no absolute in the name) matched to an AniDB cour
    now renders the franchise-absolute number under absolute numbering. At rename
    time (only when `naming.anime_numbering == "absolute"`), `_resolve_franchise_
    absolute` calls `AniDBProvider.get_franchise_offsets(aid)` (cache-first on
    disk; returns [] when banned → ban-safe) and the new pure
    `cour_routing.franchise_absolute(offsets, aid, local_ep)` computes
    `abs_start + Match.episode_number − 1` (bounds-checked to the cour's span;
    out-of-range → None → SxE fallback). Set on the local `parsed` only (render-
    only; never persisted), so `{{absx}}` resolves. With V8's matching half, the
    absolute↔local bridge is now complete both directions. +11 tests
    (`test_franchise_absolute.py`). Known minor gap: the Settings live-preview
    doesn't run this (it'd need a provider build per keystroke), so a locally-
    named sample previews as SxE but renames correctly.
  - *Archive extraction* (rar/zip before processing) — FileBot does it; absent here.
  - *File verification* (SFV / MD5 / SHA generate + check) — only `crc32` is
    parked (inside the MediaInfo item above); broad checksum verify isn't built.

**Cut / deferred:** Music (MusicBrainz + AcoustID, ❌ cut), multi-user accounts
+ native `.exe`/`.dmg` installers (demand-driven). Jinja template parity, token
externalization, and the AniDB name→id prefilter are ✅ already shipped.
