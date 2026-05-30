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

**Staged sub-parts left from matching.md** (small, finish them in Pass 5):
Phase 5 matcher wire-in, Phase 6 series-boost, Phase 16 lib install +
override, Phase 17 token externalization, Phase 13 prefilter index, Phase 20
consumer.

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

### M4. Smaller corroboration signals (duration / filesize / region) ⏳ Deprioritized
the reference renamer corroborates with runtime, filesize, region. **Audit on attempt:** the
strongest signal (runtime) is doubly blocked today — it needs the native
`libmediainfo` lib to read file duration (inert without it) AND a provider
runtime *per candidate*, which our search results don't carry (would cost an
extra detail fetch per candidate = rate-limit pressure). Filesize is weak for
identity (it tracks quality/length, not which show), and RegionHint largely
overlaps the existing parent-path anime hint + FribbAidFilter. Net: low immediate
value, real cost. **Deferred** until MediaInfo is activated (Pass 5 #5) and a
per-candidate runtime source exists; revisit then as a genuine tier-3 nudge.
Touch (when revisited): new `matcher/cascade/metrics/corroboration.py`,
`parser/mediainfo.py`.

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
risk — done). M2 next (pure speed/resilience win, no behavior change). M3/M4 are
scoring changes → Observer Mode. M5 last (biggest, and its movie path naturally
piggybacks on the OpenSubtitles infra from Pass 7).

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

### 3. Feedback gaps (#165) 🟡 Partial
- ✅ **Provider-error surfacing** — the matcher now records WHY a provider
  failed (auth/config vs unreachable) instead of swallowing it into a
  no-match; the scan worker raises a Notification ("Some providers failed…")
  so a bad/missing API key reads as a clear warning. (`matcher/engine.py`,
  `api/scans.py`; +4 tests.)
- ✅ **Frozen-UI-during-scan** addressed indirectly by the WAL + responsive-walk
  work above — the existing progress UI now actually updates live instead of
  stalling on the writer lock.
- ⏳ **Cold-start spinner** + **background-job visibility** (auto-heal /
  rematch-all / re-parse shown in the activity surface) — frontend work, not
  yet done. Touch: `App.tsx`, notifications.

### 4. Episode-title series boost (Phase 6 remainder) ⏳
Thread the fetched episode list into the cascade context so an
`EpisodeTitleMetric` can *boost the series match* (not just resolve the
episode), disambiguating two same-titled shows by which one owns the episode
title in the filename. **~1 day.** Touch: cascade context, `api/scans.py`.

### 5. Activate MediaInfo (Phase 16 remainder) ⏳
Add `pymediainfo` to deps; expose a "trust file metadata over the filename"
toggle (currently fill-missing-only); surface true bit-depth / HDR / duration
as chips + template tokens. No-op-safe if the native lib is absent.
**~half-day.** Touch: `pyproject.toml`, `parser/mediainfo.py`, settings.

---

## Pass 6 — Automation (hands-off / arr-stack workflow)

Turns Kira from "I click scan" into "drop a release, it organizes itself."

### 6. Watched folders (#166) ⏳
`watchfiles` daemon, one task per watched root, debounced (downloads land in
bursts). On a stable event → single-file `_match_singleton`, not a full
re-scan. Restart-resilient; coexists with manual scans via the existing lock.
**~2 days.** Touch: new `scanner/watcher.py`, `main.py`, Settings → Paths.

### 7. Auto-approve mode (Phase 20 consumer) ⏳
The strict-mode gate exists; wire it to an opt-in "auto-approve ≥ threshold"
setting feeding watched-folder + scan. Below threshold → held for review,
never auto-acted. Safe unattended operation. **~half-day.** Touch:
`api/scans.py`, `matcher/strict_mode.py`, settings.

### 8. Sonarr/Radarr post-processor recipe + webhook ⏳
Document a custom-script that POSTs the import path to Kira, plus a thin
`/api/v1/webhooks/sonarr` that accepts Sonarr's native payload and queues a
single-file rematch. Captures the arr-stack user base. **~1 day.** Touch:
new `api/webhooks.py`, `docs/integrations/`.

### 9. Plex / Jellyfin refresh after rename ⏳
Settings block for Plex URL+token / Jellyfin URL+key; after each rename batch,
fire one library-refresh request so changes appear immediately. Failures log,
never block the rename. **~half-day.** Touch: new integration, `api/rename.py`,
Settings → Integrations.

### 10. Notifications fan-out ⏳
Settings "send to: Discord webhook / Apprise / generic POST"; fan out
rename/scan/heal events. Generic POST first (cheap), Discord rich-embed +
Apprise after. **~half-day–1 day.** Touch: notifications layer, settings.

---

## Pass 7 — Metadata richness (Plex / Jellyfin polish)

### 11. OpenSubtitles auto-download ⏳
Hash-first exact match (the 64-bit OSDb hash, ~30 LOC pure) + name-based
fallback; save as `<stem>.<lang>.srt` sidecars — which the shipped sidecar
co-rename then carries on every move. Per-file + per-season fetch, optional
auto-fetch-on-match. **~3 days.** Touch: new `providers/opensubtitles.py` +
hash module, endpoints, Settings → Subtitles, History "Subtitles" pill.

### 12. NFO generation (Kodi / Emby) ⏳
`movie.nfo` / `tvshow.nfo` / per-episode `.nfo` from `Match.metadata_blob`,
written beside the renamed file. Per-profile toggle, default off. Pure-output,
no API risk. **~1 day.** Touch: new `renamer/nfo.py`, `api/rename.py`.

### 13. Full artwork download ⏳
Per-profile "artwork set: poster / +fanart / full" → fanart/banner/clearlogo/
per-season beside the file (Plex local-asset convention). Reuse the TVDB
semaphore for parallel, rate-limited downloads. **~1–2 days.** Touch:
providers, `api/rename.py`, settings.

### 14. Movie collection grouping ⏳
Read TMDB `belongs_to_collection` into new `Match.collection_id/name`; group
movies sharing a collection under a sub-heading, reusing the franchise-band
UI from the anime work. **~1 day.** Touch: `engine.py`, `models.py`,
`LibraryGrid.tsx`.

### 15. Multi-disc movie handling ⏳
Detect `CD1 / D1 / Disc 1 / Part 1` in `format_stripper`; default movie
templates emit `- Part {disc}` when present (LOTR Extended, IMAX, old war
films). **~half-day.** Touch: `format_stripper.py`, `templates.py`.

---

## Pass 8 — Power-user naming + robustness

### 16. Template-engine parity (Jinja2) ⏳
Swap the `str.replace` engine for Jinja2: pipe filters, conditionals,
defaults, ~40 high-value tokens (director/cast/genres/collection/ids/runtime/
tech tags). The single biggest "the reference renamer quality" naming gap. **~1 week**
(steps 1–2 alone = 80%). Touch: `renamer/templates.py`, `_build_ctx`.

### 17. Naming template live-preview ⏳
Settings panel that runs the engine against the 5 most-recent files per media
type — the "what does my template actually produce?" sanity check. Plain text,
no API change. **~half-day.** Touch: Settings → Naming, small endpoint.

### 18. Token-table externalization (Phase 17 remainder) ⏳
Move `format_stripper`'s source/codec/edition tables to a JSON data file +
user override (the `scene_rules.json` already reads `sources`/`codecs` keys —
just wire them in pre-regex-compile). **~1 day.** Touch: `format_stripper.py`,
`scene_rules.py`.

### 19. CLI mode ⏳
`kira scan|match|rename|history` driving the same workers the API uses, JSON
output for piping. Alternate entry point, no architectural change. **~1–2
days.** Touch: new `cli.py`, `pyproject.toml` entry point.

### 20. Local name→id prefilter index (Phase 13 remainder) ⏳
Build an in-memory name→id index from the already-loaded AniDB dump (+ TVDB on
demand) for an instant offline prefilter before the network search — faster,
ban-resilient, and feeds the acronym metric. **~1 day.** Touch:
`providers/anidb.py`, `matcher/engine.py`.

---

## Ordering rationale

| Pass | Theme | Why here |
|------|-------|----------|
| 5 | Finish matching + your friction list | Closes every loose end from matching.md and fixes the scan-speed/feedback you flagged first. |
| 6 | Automation | Watched folders + auto-approve + arr hooks turn Kira hands-off — highest leverage for a Sonarr user. |
| 7 | Metadata richness | Subtitles / NFO / artwork / collections — the Plex+Jellyfin polish you'd notice daily. |
| 8 | Power-user + robustness | Template parity is big but optional; CLI + token data file are for tinkerers. |

**If you only do one pass:** Pass 5 — it finishes the matching story and
clears the friction that's been pending since the start.

**Biggest single item:** #16 (Jinja2 template parity) — defer until you
actually want custom naming beyond Plex/Jellyfin/Kodi presets.

**Explicit scope cut (unchanged):** Music (MusicBrainz + AcoustID). Native
installers (.exe/.dmg) + multi-user accounts remain weeks-out, demand-driven.
