# Kira — Master Architecture & Feature Blueprint

> Primary engineering reference. Traces every subsystem from "Scan" click to
> matched result, documents the custom matching logic and the workarounds that
> exist because of real-world provider hostility (AniDB IP-bans, multi-cour
> season modelling, absolute-vs-local numbering). Source of truth is the code;
> this maps it. (Companion docs: `ARCHITECTURE.md`, `matching.md`, `roadmap.md`,
> `AUDIT_FINDINGS.md`.)

**Stack:** Python 3.12 / FastAPI / SQLAlchemy 2.0 async / SQLite (WAL) · React 18 / TS / Vite.
**Backend root:** `backend/kira/`. **Frontend root:** `frontend/src/`.

---

## 1. The Macro Map — lifecycle of a file from "Scan" to displayed match

### 1.1 Trigger & scan record
1. **`POST /api/v1/scans`** → `api/scans.py::create_scan` → `_start_scan(paths, source)`.
   - A single global **DB scan lock** (`_release_db_scan_lock`, `reconcile_orphaned_scans`) serializes scans; a crashed/orphaned scan is reconciled on startup.
   - A `Scan` row is created (`status: pending → running → done|failed`, `file_count`, `matched_count`, `estimated_total`, `current_path`). `source ∈ {manual, watch, webhook}`.
2. Work runs in a detached, strong-ref'd task: **`_scan_worker` → `_scan_worker_locked`**.

### 1.2 Discovery (the walker) — `scanner.py`
- `scanner.walk(root)` yields candidate media paths. Pure filesystem, **no parsing**.
- Filters: `VIDEO_EXTENSIONS ∪ AUDIO_EXTENSIONS`; skips dotfiles/`@`/`$`/`__MACOSX`/`#recycle`, NAS junk (`System Volume Information`, `lost+found`), and **sample/extras/trailer** releases (Phase 19).
- Edge cases solved: no per-file `resolve()` in dedup (normalized `_norm`), no redundant `is_file()` stat, MediaInfo is **not** read during the walk (kept out of the hot discovery path).
- Dead-NAS-root guard: `_root_reachable()` — a scan over an unreachable SMB/NFS root fails cleanly instead of reporting "0 files".

### 1.3 Parse — `parser/parser.py` → `ParsedFile`
- `parse(path)` → `ParsedFile` dataclass: `media_type` (movie|tv|anime|music|unknown), `title`, `year`, `season`, `episode`, `episode_end`, **`absolute_episode`**, `cour`, `named_season`, `episode_title_guess`, `provider_ids` (embedded `{tmdb-…}`/`{tvdb-…}`/`{anidb-…}`/`ttNNN`), `air_date`, `disc`, format tokens (`quality/source/codec/hdr/bit_depth/channels/release_group`), MediaInfo cache (`mi_raw`, `mi_stamp`, `duration`, `audio_langs`, `sub_langs`), `sub_sidecars`, parser `confidence`.
- Helpers: `format_stripper.strip` (token extraction), `patterns.extract_sxe/extract_absolute_after/extract_year`. Hard cases handled: absolute-overload, glued multi-ep, empty-stem, sequels, daily/air-date shows.
- Persisted as `MediaFile.parsed_data` (JSON). `MediaFile`: `file_path` (unique), `media_type`, `status` (`discovered → matching → matched/no_match/needs_resolution → approved/renamed`), `parsed_data`, **`series_key`** (cluster key), **`variant_key`** (dedup/quality variant).

### 1.4 Identity stamping (xattr / embedded IDs)
- `_apply_xattr_ids` reads IDs a prior Kira rename stamped on the file (xattr) into `parsed.provider_ids` (`rename.stamp_ids`). These let the matcher resolve **by ID, skipping title search entirely**.

### 1.5 Clustering & match phase — `_match_phase`
- Files grouped by **`series_key`**. Singletons → `_match_singleton`; multi-file series → **`_match_cluster`**.
- `_match_cluster`:
  1. **Representative selection** (`_rep_score`): pick the most "standard" episode (lowest season/episode; Specials/OVAs/movies sort last) so the search query isn't skewed by an OVA being `files[0]`.
  2. Run **`engine.match(rep_parsed)`** ONCE → ranked `ScoredMatch[]`. Reuse the same provider/provider_id/title/year/poster for every file; each file keeps its own season/episode.
  3. **Cour routing** (§2.5) maps each file's episode → correct sibling AID + local episode.
  4. **Bipartite assignment** (§2.7) pairs files → episodes for titles/validation.
  5. **Episode validation & rerank** (`_validate_and_rerank_by_episodes`, `_fetch_episodes_for_match`): fetch the season's episode list ONCE (ban-safe), fill `episode_title`, reject candidates whose episode count can't hold the cluster.
  6. **Folder series-lock** (`_apply_folder_series_lock`): a confidently-identified series folder pins its files so a later weak per-file rematch can't drift.
  7. **Franchise rescue** (`_franchise_rescue_unpaired`): unpaired long-runner files re-paired by franchise-absolute numbering.
- Writes one **`Match`** row per file (`is_selected=true` for winner): `provider/provider_id`, `series_group_id` (`"{provider}:{canonical_aid}"`), `match_type`, `confidence`, `season_number/episode_number/episode_title`, `poster_url`, `metadata_blob` (carries the **cascade trace** for UI hover + heal pass).
- `Scan.matched_count` advances; the UI polls `GET /scans/{id}` + `GET /files` and renders cards/clusters.

### 1.5.1 Title-rescue (PRE-match, inside `_match_phase`)
**Corrected placement.** Before a cluster is scored, `_match_phase` ([scans.py:1716](backend/kira/api/scans.py)) calls `_maybe_rescue_title_from_mediainfo(mf)` for any file whose **filename yielded no usable title or `media_type='unknown'`** — files the matcher would otherwise skip entirely. It reads the container's embedded title (a single bounded read, even when global MediaInfo-on-scan is OFF), re-parses from it, and recomputes `series_key`/`variant_key`, **then** the file flows into matching in the *same scan*. There is therefore **no "rescued title sits dormant until manual rematch" problem** — the rescue is synchronous and upstream of matching. (Truly nameless files still fall back to "Identify by content" — the OSDb byte-hash.) Guard: never regresses a file that already had a title into a vaguer `unknown`.

### 1.6 MediaInfo enrichment (background) — `enrich_mediainfo_background`
- Reads real container metadata (resolution/codec/HDR/audio/duration/embedded sub-langs) when `parsing.read_mediainfo` is on, cached on `parsed_data` via `mi_stamp` so unchanged files skip the NAS re-read. `mediainfo_authoritative` lets tech tags override filename guesses.
- Spawns: subtitle backfill (`backfill_after_scan`), subtitle upgrade (`subtitles.upgrade`), **anime poster warm-up** (`posters.warm_anime_posters`).
- This pass only adds **tech tags** (it does NOT re-match). Identity rescue happens earlier — see §1.5.1 (corrected; the title-rescue is **pre-match**, not part of this background pass).
- **Surfaced as the scan popup's 3rd "Tech tags" line** (not a separate, easy-to-miss pill): the frontend keeps the popup mounted and narrates the enrich job to completion. A plain **rescan backfills** every file still missing tech tags (`mi_stamp` null via `_ids_missing_tech_tags`), not just newly-discovered ones — so "rescan" dependably reads them. Self-limiting (each file is read at most once). (§9.18)

### 1.6.1 Pruning (ghost-file removal) — `_prune_missing_files`
After a **fully healthy walk** (no unreachable root), the scan worker calls `_prune_missing_files` ([scans.py:1788](backend/kira/api/scans.py), invoked ~line 2080). It removes a `MediaFile` row **and its `Match` rows** only when ALL hold: the path is **under a root this scan walked** (never touches other libraries), the path was **not seen in this walk**, and `stat()` raises **`FileNotFoundError`** (CONFIRMED gone — a permission/NAS error counts as "can't tell → keep", never as deleted). `RenameHistory` is preserved; nothing is deleted from disk (the file's already gone). This is what stops deleted-on-NAS files and `move`-d-out source rows from accumulating as "ghosts" in the UI.

### 1.7 Self-heal & reconcile (incl. startup recovery)
- **Match self-heal:** stale matches (episode drift, year-mismatch, renamed-file status) self-heal via the matcher heal path, which **rescues from the persisted cascade trace** in-place (no re-search). Cour routing is applied on EVERY write path (scan, rematch, manual, bulk) via the shared `cour_routing` helpers so heal can't orphan cours.
- **Startup reconcile (lifespan hook, `main.py`):** on boot, before serving:
  1. `reconcile_orphaned_scans()` ([scans.py](backend/kira/api/scans.py)) — a scan that died mid-run is closed out and its stuck `matching` files reset to `pending` (re-matched next scan); the global scan lock is released.
  2. `reconcile_pending_renames()` ([rename.py:950](backend/kira/api/rename.py), called at [main.py:136](backend/kira/main.py)) — reads the **`PendingRename` intent journal**, and for each unresolved intent **checks the filesystem**: if the target exists, finalize the DB row to the new path; if not, discard the intent (rolled back). This closes the "died between file-move and DB-commit" window — the source-gone/target-exists/DB-stale crash case is recovered at next boot, not lost.

### 1.8 Execution & Notification (the rename climax) — `api/rename.py`, `renamer/*`
Matching/approval does **not** rename. Renaming is a **separate, explicit action** (`POST /api/v1/rename`), driven by the user (Review page) or — when `matching.auto_approve` is on — pre-cleared automatically but still requiring the rename call. `auto_approve` only flips `status → approved` ([scans.py:117](backend/kira/api/scans.py)); it **never blocks the scan worker with file I/O**.

The rename route runs this ordered pipeline (`api/rename.py`):
1. **Preflight & journal** — duplicate-target guard; write a `PendingRename` intent row (so a crash mid-rename is reconciled, not lost).
2. **`_rename_one_file`** (`renamer/operations.py`) per file — render the target via the Jinja template (`renamer/templates.py`), then **move | copy | symlink | hardlink** (`rename.default_op`). Cross-device move verify, case-only-rename safety, atomic temp+replace. Created sidecars/artwork tracked in `RenameHistory.created_assets` for **authoritative undo**.
3. **NFO + artwork** (`renamer/nfo.py`) — write `.nfo` (`naming.write_nfo`/`nfo_fields`) + download artwork (`naming.download_artwork`/`artwork_types`) when enabled. NFO `<showtitle>` and the `tvshow.nfo` `<title>` use the **unified** `library_title` (the same franchise name the FOLDER uses), never the per-cour AniDB title — so the NFO names the show its folder belongs to (§9.20). Episode artwork is **show-level** (one `poster.jpg`/`fanart.jpg`/… at the series root, shared, write-if-absent); **anime cours additionally get per-season posters BOTH ways** — a `Season NN/poster.jpg` file (Plex/Jellyfin/Emby) AND a `<thumb aspect="poster" type="season" season="N">` per cour in the unified `tvshow.nfo` (Kodi / NFO-driven), each from the cour's own `Match.poster_url` (§9.21).
4. **Folder cleanup** — remove empty source dirs / media-server artifacts / trash (the `rename.cleanup_*` keys), GC scoped to the rename teardown.
5. **xattr ID stamp** (`xattr_store.write_ids`, `rename.stamp_ids`) — write provider IDs so a future rescan resolves by ID.
6. **Subtitle auto-fetch** (`subtitles.auto_fetch`) — `subtitles/aggregate.fetch_subtitles` writes `.srt` sidecars for every renamed file (bounded concurrency).
7. **Media-server refresh** — `integrations/media_server.refresh_all` (Plex/Jellyfin) **+** Sonarr rescan (`integrations/sonarr`).
8. **Notification fan-out** — `notify.fan_out` → Discord / generic webhook.

> **STEPS 6–8 RUN IN THE BACKGROUND (perf, §9.24).** Steps 1–5 are awaited inline: the moment `/rename` returns, every file is moved, its `RenameHistory` is committed, and the rename is **fully undoable**. The network tail (notify → subtitle auto-fetch → media-server refresh → Sonarr) is then handed to a **tracked background task** (`tasks.spawn_tracked`) and the response returns immediately — a full-season rename used to block the request for *minutes* on the per-episode subtitle fetch. The task opens its own `SessionLocal` and **re-loads** the renamed files there (the request session is already closed, so its instances are detached). The activity pill narrates the subtitle phase as the sidecars land. Tests/shutdown await it via `tasks.drain_background_tasks()`.
>
> **ORDERING INVARIANT (bug-fixed):** steps 6 → 7 must stay in that order. Plex/Jellyfin/Sonarr are refreshed **after** subtitle sidecars are written, so the media server indexes the file *and* its subtitles in a single scan. Firing the refresh before subtitles (the previous bug) made Plex index a sub-less file and miss the `.srt` until its next scan. Still holds inside the background task — it runs its steps **sequentially**, so backgrounding the tail did not relax the invariant. Enforced in `api/rename.py` post-rename hook; covered by the ordering comments + `test_post_rename_network_tail_is_backgrounded`.

**Sidecar/NFO placement in link modes.** Subtitles and `.nfo`/artwork are always written next to the **TARGET** (the organized library path = `RenameResult.new_path`), never the source — the subtitle fetch builds its `SearchContext(video_path=r.new_path, …)`. So in `symlink`/`hardlink` mode the artwork sits beside the link in the library (where Plex looks), and the source download folder stays clean. (Edge: deleting a symlink later leaves its sidecar orphaned in the library; the trash/cleanup endpoints can remove it but don't proactively sweep that case — see §8.)

**Sonarr rescan assumes aligned roots.** `rescan_series_by_tvdb` issues a `RescanSeries` command that scans **Sonarr's own configured series path** — it does not (and intentionally won't) move files or rewrite Sonarr's path. It therefore only links the renames if Kira's `naming.profile` target lands under the same location Sonarr manages (`integrations.sonarr.root_folder_path`). Diverging roots = a silent no-op, by design (auto-repathing Sonarr could shuffle files server-side). Keep Kira's library root and Sonarr's root pointed at the same tree.

---

## 2. The Engine & "The Tricks"

### 2.1 `MatchEngine.match()` orchestration (`matcher/engine.py`)
Per file:
1. **Provider order** — `resolve_provider_order(media_type, settings)`: user override (Settings → Matching) else default (anime→anidb,tvdb,tmdb; tv→tvdb,tmdb; movie→tmdb,tvdb). DB settings cached 30s, invalidated on `PUT /settings`.
2. **Embedded-ID bypass** (`_match_by_embedded_id`) — if `parsed.provider_ids` present, resolve directly and skip title search (handles junk-titled-but-ID-tagged files).
3. **Title guard** — empty title → no match.
4. Walk providers; per provider run **`_match_with`** (query ladder → provider search → cascade scoring). Provider failures classified: `ProviderPermanentError` (config/key — recorded, surfaced as notification), `ProviderTransientError` (retries exhausted), other.
5. **Anime guardrail** (non-AniDB providers): `_filter_anime_to_known_aids` — a TVDB/TMDB candidate MUST have a Fribb cross-ref to a known AID or it's dropped (kills "One Page Love ↔ One Pace" drift). R2-C3 fallback does a language check when the Fribb dump is empty.
6. **Early-exit:** AniDB hit ≥ `ANIME_ANIDB_TRUST_FLOOR` wins outright (AniDB IS the anime source of truth); general hit ≥ `EARLY_EXIT_CONFIDENCE` short-circuits.
7. **No-match floor:** `MIN_CONFIDENCE` / `MIN_CONFIDENCE_ANIME` — below floor returns empty → UI shows manual-search affordance.
- **Query ladder** (`_query_ladder`): progressively-relaxed queries (with/without year, stripped season suffix).
- **Retry policy (load-bearing):** two schedules picked by error class — `_RETRY_BACKOFFS (1,2,4s)` for rate-limit/5xx, `_CONNECT_BACKOFFS (0.2..1.0s)` for TCP/TLS blips. Conflating them turned every TMDB connect-blip into a 7s stall (~20× matching slowdown).

### 2.2 The Cascade runner (`matcher/cascade/runner.py` + `types.py`)
- A **`Cascade`** is an ordered list of **`Metric`**s; `score_one` evaluates **every** metric (observability > microseconds); `score_all` runs candidates concurrently (`asyncio.gather`), sharing `ctx.enrich_cache` to coalesce duplicate HTTP.
- **Tier bands (locked):** Tier-1 IDENTITY `[0.85,1.00]`, Tier-2 SIMILARITY `[0.50,0.85)`, Tier-3 CORROBORATION `[0.20,0.50)`. Each metric's `raw∈[0,1]` is `clamp_to_tier`'d into its band — a tier-3 glitch can never overshadow a clean tier-1 signal.
- **Aggregation rule (user-locked):** `final = max(tier1_max, weighted_avg(tier2_max, tier3_max))`, `weighted = 0.7·t2 + 0.3·t3`. Each tier contributes its **MAX** (not sum) so overlapping similarity metrics don't double-count. Perfect t2+t3 ≈0.85, never beating tier-1.
- **Veto:** any metric returning `raw=-1.0` forces `final=0.0`.
- **Tier-1 ambiguity:** if ≥2 traces tie within `_AMBIGUITY_EPSILON (0.01)` at/above 0.85, all flagged `is_ambiguous`; engine can mark file `needs_resolution` instead of a non-deterministic coin-flip (the "Bleach umbrella AID vs correct cour AID" danger).
- **`CascadeTrace`** persisted to `metadata_blob['cascade_trace']`: `final_score`, `dominant_metric/tier`, every metric `{raw, score, tier_confidence, reason}`, `is_ambiguous`, optional `shadow_score`. Drives the popup "why 65%?" hover + in-place heal rescore.

### 2.3 The metrics (`build_default_cascade`)
Filters run **first** (vetoes drop candidates pre-scoring), then tier-1, tier-2, tier-3.

| Metric | Tier | Fires when | Trick / note |
|---|---|---|---|
| **FribbAidFilter** | veto | anime + non-AniDB provider | `raw=-1.0` if TVDB/TMDB id has no Fribb anime entry → drop. Neutral when Fribb dump empty. AniDB candidates never filtered (they ARE the source). |
| **EpisodeCountSanity** | veto | anime | Vetoes a candidate that can't physically hold the cluster's max episode, **summing counts across Fribb sibling cours** (Bleach 13+13+14 ≥ 40 passes; 12-ep spin-off vetoed). Abstains if any sibling count uncached. Pure disk. |
| **Substring** | 1 | parsed title is a clean **word-boundary substring** of a candidate alias | ≥4 chars after `normalize`; uses `cluster_signal` when present. |
| **FolderIdentity** | 1 | parent folder trigram ≥0.7 to a candidate title/alias | Rewards Plex/Sonarr folder hygiene; neutral on generic folders (`Downloads`, `Season N`); never penalizes. |
| **FribbAuthority** | 1 | anidb + `parsed.season` + candidate Fribb `tvdb_season == parsed.season` | Promotes the AID Fribb confirms IS that TVDB season. Pure in-memory, ban-safe; abstains for non-AID providers. |
| **ClusterSignal** | 2 | multi-file cluster | Scores cluster's longest shared word-sequence (`ctx.cluster_signal`). The "One Pace" fix; replaced the old M7 short-title penalty. |
| **Trigram** | 2 | needle ≥4 chars | Char-trigram Jaccard vs title + aliases (max). |
| **Levenshtein / LCS / NumericDistance** | 2 | — | Extra edit-distance measures (`text_distance.py`); MAX-per-tier so they only *raise* on what trigram missed (typos, word-order, numeric titles). |
| **Acronym** | 2 | acronym-shaped title | Expands curated acronyms (`acronyms.py`). |
| **EpisodeTitle** | 2 | tv/anime + `episode_title_guess` + **cached** episode list | **Cascade purity (load-bearing): never fetches.** Reads only `ctx.enrich_cache[("ep_titles",…)]`; abstains otherwise — resolution happens ban-safely in bipartite Pass 6. |
| **AnimeTVDBJP** | 2 | anime + tvdb | JP-origin/animation enrichment on TVDB extended info. |
| **AnimeSeasonOrdinal** | 2 | anime | Season-ordinal ("2nd Season") match. |
| **Year / Rank** | 3 | — | Year corroboration; provider result-rank position. |
| **RuntimeCorroboration** | 3 | Labs opt-in + file `duration` | Compares MediaInfo runtime to candidate runtime. **Never fetches**; off by default. |

### 2.4 AniDB rate-limit & ban protection (`providers/anidb.py`)
AniDB IP-bans 12h if requests arrive <2s apart. The module is built to never trip it:
- **5-second gate:** `_API_DELAY_SEC = 5.0`, serialized via class-level `_api_lock` + last-request timestamp — every API call ≥5s apart (margin over the 2s threshold).
- **Circuit breaker:** `≥ _ERROR_THRESHOLD` errors within `_ERROR_WINDOW`s → circuit **opens** for `_CIRCUIT_OPEN_SEC = 300s`, refusing outgoing requests so a failure burst can't drive a real ban.
- **Disk-backed ban file** (`_load_ban_state` / `is_banned()`): cross-worker — one worker sees a ban, all short-circuit. Every method checks `is_banned()` and degrades gracefully.
- **Cache-first everything** (all disk-persisted, usable while banned): title index, episode-count cache, relations, franchise-offsets, picture cache (`anidb-pictures.json`). Posters prefer TVDB/TMDB cross-ref; AniDB CDN is last resort.

### 2.5 Cour-routing (`matcher/cour_routing.py`)
Problem: AniDB models a multi-cour TVDB season as **N separate AIDs** (Bleach TYBW S17 → 15449/17849/18671); TVDB lumps them as Season 17; users number files E01–E40 continuously.
- **`build_cour_routing_table(provider, top_aid, parsed_season, registry)`** → `[(start_ep, end_ep, cour_aid, offset)]` in **season-local** space, by summing each sibling's cached episode count (Bleach → `[(1,13,15449,0),(14,26,17849,13),(27,40,18671,26)]`). Returns `None` (refuse to route) on non-AniDB, missing season, stale Fribb season-disagreement that IS a real Fribb season, <2 siblings, or uncached counts. **Lazy-fetches** missing sibling counts (ban-checked).
- **`route_file_to_cour(table, file_episode, abs_to_local)`** → `(aid, local_episode)`, with an **absolute→local bridge** consulted only on a direct miss (AoT Final Season `- 60..89` → cours keyed 1..30); cour-local shows unaffected.
- **`route_file_to_cour_precise(...)`** — summed-count table authoritative for contiguous cours; **ScudLee XML only fills gaps** the table can't place, and only when ScudLee lands on a sibling already in `table`. *Why table-first:* ScudLee's flat `defaulttvdbseason+episodeoffset` returns the FIRST cour's AID for every episode when cours share a TVDB season — which silently collapsed all of Bleach/AoT onto Cour 1.
- **`remap_umbrella_local_to_absolute(...)`** — inverse bridge for **flat umbrellas** (One Piece AID 69, Naruto, Conan). A file that arrived TVDB-season-local ("One Piece S23E04" → local 4) is stored as ABSOLUTE (1159) so it lines up with absolute-named siblings and is recognized as the duplicate. Hard-gated to flat umbrellas.
- **`franchise_absolute(offsets, aid, local_ep)`** — rename-output inverse: AID-local → franchise-absolute for `{{absx}}` (AoT cour `(…,60,87)` local 1 → 60); refuses if outside the AID's span.

### 2.6 Fribb mappings (`providers/anime_mappings.py` + `anime_lists.py`)
Why cour-routing and the anime filter run **ban-free**:
- **`anime_mappings.py`** — Fribb `anime-list-full.json` (community AID↔TVDB↔TMDB↔Kitsu↔AniList). Downloaded weekly, served from memory. Lets Kira **match with AniDB IDs but fetch artwork/episodes from TVDB/TMDB**, avoiding AniDB API hits. Exposes `tvdb_id(aid)`, `tvdb_season(aid)`, `aids_by_tvdb_season(tvdb_id, season)` (sibling cours). Carries only a FLAT `season` int per AID.
- **`anime_lists.py`** — ScudLee XML expressing start-offsets, mid-season inserts, non-contiguous ranges via `<mapping>` blocks (flat/range/explicit). Resolver `(tvdb_id, season, ep) → (anidb_id, anidb_ep)`. 24h disk cache, GitHub-sourced (ban-safe), never raises.

### 2.7 Bipartite episode assignment (`matcher/bipartite.py`)
Greedy file↔episode pairing for `len(cluster) ≥ 3` (N=1 no signal, N=2 coin-flip). Multi-pass, each claims & removes pairs:
1. **Exact** `(parsed.season, parsed.episode)`.
2. **Absolute** `parsed.absolute_episode` → provider `absolute_number` (AniDB-native fallback `ep.episode`); stores the absolute, not the local index.
3. **Season-agnostic** `parsed.episode == ep.episode` (anime only) — One Piece S23E1158 (AniDB lists E1158 as season 1).
4. **3.5 absolute-by-episode** (`absolute_sxe`): "S23E1156" parses episode=1156, no absolute; pair by `ep.absolute_number == parsed.episode`, store 1156. Hard-gated (anime, no parsed absolute, after Pass 3, `ep.absolute_number != ep.episode`, `parsed.episode > max_local`).
5. **Air-date** (daily/talk/news).
6. **Episode-title similarity** — remaining orphans by `episode_title_guess` trigram, claimed only on ≥0.6.
- Unclaimed → `matched_via="unpaired"` (orphan "no episode" pill).

### 2.8 Observer Mode / shadow funnel (`runner.py`)
Evaluate a scoring change without flipping it blind. Enabled via env **`KIRA_FUNNEL_OBSERVER`** (read per-call).
- Computes a **candidate rebalanced score** side-by-side: string metrics (`trigram/levenshtein/lcs`) collapse to ONE family vote; different-family metrics that independently agree add `_AGREEMENT_BONUS = 0.15` of the 2nd-best signal (rewards corroboration the MAX-per-tier rule ignores). Tier-1 still wins.
- **Never drives behavior** — `final_score` still ranks. When the shadow funnel would pick a different top candidate, the runner logs `funnel_diverge media=… current=… shadow=…` so a future weight flip is data-justified. Stored in `CascadeTrace.shadow_score`.

---

## 3. Settings & Configuration Matrix

Two layers: **env/bootstrap** (`config.py`, `KIRA_` prefix) and **runtime DB settings** (`settings` table, edited via `PUT /api/v1/settings`, cached 30s in the engine, invalidated on write).

### 3.1 Env / bootstrap (`config.py`)
| Key | Impact |
|---|---|
| `KIRA_DATABASE_URL` | SQLite path (`database.py`). |
| `KIRA_MEDIA_ROOT` | Default media root. |
| `KIRA_TMDB_API_KEY` / `KIRA_TVDB_API_KEY` | Dev bootstrap keys (prod uses DB `providers.*`). |
| `KIRA_CORS_ORIGINS` | Allowed dev origins. |
| `KIRA_AUTH_USER` / `KIRA_AUTH_PASS` | Env HTTP Basic auth (both set → required on every `/api` call; health, token webhooks, `/img`, SPA shell exempt — `main.py::_auth_exempt`). |
| `KIRA_FUNNEL_OBSERVER` | Enables shadow-funnel divergence logging (§2.8). |

### 3.2 Runtime DB settings (`settings` table)
**Providers / credentials** → `providers/factory.py`, `engine.registry_from_settings`:
- `providers.tmdb.api_key`, `providers.tmdb.language`; `providers.tvdb.api_key`
- `providers.anidb.client`, `providers.anidb.clientver` (AniDB HTTP-API registration; wrong pair = `_client_rejected`)
- `providers.fanarttv.api_key`, `providers.fanarttv.client_key`
- `providers.opensubtitles.api_key/username/password`, `providers.subdl.api_key`, `providers.subsource.api_key`

**Matching** → `engine.py`:
- `matching.auto_approve` (bool) + `matching.auto_threshold` (float) — `_read_auto_approve_setting`: auto-flip high-confidence matches to `approved`. **Default OFF.** (Provider-order override read from the same dict.)

**Parsing** → `scans.enrich_mediainfo_background`, `parser`:
- `parsing.read_mediainfo` (read tech tags; off → filename-only), `parsing.mediainfo_authoritative` (tags override filename guesses).

**Naming / output** → `renamer/templates.py`, `renamer/nfo.py`:
- `naming.profile` (Jinja template), `naming.anime_numbering` (absolute vs SxE), `naming.write_nfo` + `naming.nfo_fields` (Kodi/Emby `.nfo`), `naming.download_artwork` + `naming.artwork_types`.

**Rename / file ops** → `renamer/operations.py`, trash, `download_guard`:
- `rename.mode`, `rename.default_op` (move|copy|symlink|hardlink), `rename.concurrency`, `rename.stamp_ids` (xattr IDs → ID-bypass on rescan), `rename.cleanup_empty_source_dirs`, `rename.cleanup_media_server_artifacts`, `rename.cleanup_trash`, `rename.trash_dir`, `rename.trash_retention_days`.

**Subtitles** → `subtitles/*`:
- `subtitles.languages` (+ per-type `subtitles.languages.{movie,tv,anime}`), `subtitles.embedded`, source toggles `subtitles.{subdl,podnapisi,subsource,animetosho,yifysubtitles}`, `subtitles.hearing_impaired`, `subtitles.forced`, `subtitles.auto_fetch`, `subtitles.backfill_after_scan`, `subtitles.min_score`, `subtitles.upgrade` + `subtitles.upgrade_below`, `subtitles.cache_retention_days` (reuse-cache retention; 0 = keep forever).

**Integrations** → `integrations/sonarr.py`, `api/integrations.py`, media-server sync:
- `integrations.sonarr.{url,url_base,api_key,quality_profile_id,root_folder_path,season_folders,monitor_new_seasons,audio_preference}`
- `integrations.plex.{url,token}`, `integrations.jellyfin.{url,api_key}` (library refresh, SSRF-guarded)
- `integrations.webhook.token` (inbound *arr secret), `notifications.discord_webhook`, `notifications.webhook_url` (outbound)

**Paths / watch** → `watcher.py`, scanner:
- `paths.library_root`, `paths.library_roots`, `paths.watch_folders`, `watch.config` (auto-scan watcher: `awatch` + poll fallback for NAS/SMB; opt-in).

**History / advanced / auth / labs:**
- `history.retention_days`, `advanced.update_check`, Labs feature flags (`engine.labs_flag` — gates e.g. RuntimeCorroboration), `auth.user` + `auth.password_hash` (in-app account, alternative to env Basic auth).

> **Editing model (frontend, §9.19):** the Settings page buffers ALL edits as a local DRAFT and persists only on an explicit **Save** (a floating Save/Cancel bar shows when dirty; Cancel reverts to the last-saved baseline) — there is **no auto-save**. This closes the "browser autofill silently overwrites a saved API key/token" gap and gives every change a confirmation step. `PUT /settings` is unchanged: it just receives one batched write per Save instead of one per keystroke/toggle. Leaving the page with unsaved edits confirms first.

---

## 4. Component Relationship Map

### 4.1 Call graph (scan → match → display)
```
POST /scans (api/scans.py)
  └─ _start_scan → _scan_worker → _scan_worker_locked          [DB scan lock]
       ├─ scanner.walk()                       discovery (no parse)
       ├─ parser.parse() → ParsedFile           per file → MediaFile.parsed_data (DB)
       ├─ _apply_xattr_ids()                    embedded provider IDs
       ├─ _match_phase()
       │    ├─ _match_singleton / _match_cluster
       │    │    ├─ engine.match(rep)           ─► MatchEngine (engine.py)
       │    │    │     ├─ resolve_provider_order / _query_ladder
       │    │    │     ├─ _match_with(provider) ─► providers/{anidb,tvdb,tmdb}.py
       │    │    │     │     └─ Cascade.score_all ─► cascade/runner.py + metrics/*
       │    │    │     │            ├─ Fribb metrics       ─► anime_mappings.py
       │    │    │     │            └─ EpisodeCountSanity  ─► anime_mappings + anidb ep-count cache
       │    │    │     └─ _filter_anime_to_known_aids ─► anime_mappings.py
       │    │    ├─ cour_routing.build/route    ─► anime_mappings.py + anime_lists.py + anidb cache
       │    │    ├─ bipartite.assign_files_to_episodes
       │    │    ├─ _fetch_episodes_for_match / _validate_and_rerank_by_episodes ─► providers
       │    │    └─ _apply_folder_series_lock / _franchise_rescue_unpaired
       │    └─ writes Match rows (DB) + cascade_trace in metadata_blob
       └─ enrich_mediainfo_background()  MediaInfo + spawns subtitle backfill/upgrade + posters.warm_anime_posters
GET /scans/{id}, GET /files  (frontend polls) → React grid / CoverPopup

POST /rename (api/rename.py)        [explicit user/approve action — NOT the scan worker]
  ├─ preflight + PendingRename journal
  ├─ _rename_one_file (renamer/operations.py) → move|copy|symlink|hardlink + render (templates.py)
  ├─ NFO + artwork (renamer/nfo.py) ; folder cleanup (rename.cleanup_*)
  ├─ xattr_store.write_ids (rename.stamp_ids)
  ├─ subtitles/aggregate.fetch_subtitles            (#6 — write .srt FIRST)
  ├─ integrations/media_server.refresh_all + sonarr  (#7 — refresh AFTER subs)
  └─ notify.fan_out → Discord / webhook              (#8)
```

### 4.2 Module responsibility matrix
| Module | Owns | Talks to |
|---|---|---|
| `api/scans.py` | scan lifecycle, clustering, cluster-match orchestration, MediaInfo enrich, reconcile | `scanner`, `parser`, `engine`, `cour_routing`, `bipartite`, providers, DB |
| `scanner.py` | filesystem walk, discovery, exclusions | — (pure) |
| `parser/` | filename → `ParsedFile` | `format_stripper`, `patterns`, MediaInfo |
| `matcher/engine.py` | provider orchestration, query ladder, retries, anime guardrails, early-exit, `series_group_id` | providers, `cascade/runner`, `anime_mappings`, settings |
| `matcher/cascade/runner.py` + `types.py` | tiered scoring, veto, ambiguity, observer mode | metrics, `similarity`, `text_distance` |
| `matcher/cascade/metrics/*` | scoring contributions | `anime_mappings` (Fribb), `similarity`, `ctx.enrich_cache` |
| `matcher/cour_routing.py` | absolute/local→cour-AID routing, umbrella/franchise remaps | `anime_mappings`, `anime_lists`, anidb ep-count cache |
| `matcher/bipartite.py` | file↔episode pairing (6 passes) | `similarity` (title pass) |
| `providers/anidb.py` | AniDB API + 5s gate + circuit breaker + disk ban + caches | disk caches, `anime_mappings` |
| `providers/anime_mappings.py` | Fribb AID↔TVDB/TMDB cross-ref (weekly) | GitHub (Fribb) |
| `providers/anime_lists.py` | ScudLee per-episode cour XML (24h) | GitHub (ScudLee) |
| `providers/{tmdb,tvdb}.py` | metadata/episode/artwork | TMDB/TVDB APIs |
| `renamer/` | template render, file ops, NFO, artwork, trash, authoritative undo | `download_guard`, `RenameHistory`/`PendingRename` |
| `subtitles/` | coverage, scoring, providers, pack extraction, backfill/upgrade | providers, `download_guard`, `net` |
| `posters.py` | persist anime poster URLs (warm-up) | anidb picture resolution, DB |
| `api/images.py` | image proxy/cache (`/img`) for slow CDNs | `download_guard`, `net` |
| `models.py` | `Scan / MediaFile / Match / Setting / RenameHistory / PendingRename / Notification / SubtitleAsset` | — |
| `main.py` | app wiring, lifespan (warmups), Basic-auth middleware, router mounts | all routers |

### 4.3 Persisted state & caches
- **DB:** `Scan`, `MediaFile` (`series_key`/`variant_key`/`parsed_data`), `Match` (`series_group_id`, `metadata_blob.cascade_trace`, `is_selected`/`is_manual`), `RenameHistory` (`created_assets` for authoritative undo), `PendingRename` (intent journal + reconcile), `SubtitleAsset`, `Notification`, `Setting`.
- **Disk caches (`.cache/`):** `anime-mappings.json` (Fribb, weekly), ScudLee XML (24h), AniDB title index / ep-count / relations / franchise-offsets / `anidb-pictures.json`, AniDB ban-state file, `images/` (poster proxy).
- **In-process:** engine settings cache (30s TTL, invalidated on `PUT /settings`), per-call `CascadeContext.enrich_cache`, frontend AniDB-poster map.
- **Subtitle reuse-cache (`<library>/.kira-subcache/`, §9.17):** when undo removes a `.srt`, it's parked here keyed by OSDb content-hash + lang; the next fetch for that file reuses it before re-downloading (saves provider quota). Retention `subtitles.cache_retention_days`, swept on scan-tail (`subcache.sweep_expired`).

---

## 5. Key engineering hurdles solved (index)
- **AniDB 12h IP-ban** → 5s gate + 300s circuit breaker + cross-worker disk ban + cache-first everything.
- **Multi-cour TVDB seasons as N AniDB AIDs** → summed-count cour routing + ScudLee gap-fill + episode-count-sanity veto with sibling aggregation.
- **Absolute vs season-local vs umbrella numbering** (One Piece, AoT, Bleach TYBW) → bipartite absolute passes + abs↔local bridges + flat-umbrella remap + franchise-absolute for `{{absx}}`.
- **Live-action drift in anime search** → Fribb AID filter veto.
- **Non-deterministic tie-break** (umbrella vs cour AID) → tier-1 ambiguity flag → `needs_resolution`.
- **Cascade purity** → no metric ever fetches; AniDB-rate-limited work stays in ban-safe paths (bipartite, validation).
- **Provider connect-blips tanking throughput** → split fast vs back-off retry schedules.
- **Risky global scoring changes** → Observer Mode shadow funnel logs disagreements before any flip.
- **Heal orphaning cours** → shared `cour_routing` helpers on every Match-writing path.

---

## 6. Complete File Map (annotated tree)

Every source file, grouped by the feature it belongs to. `# 🟢` marks a feature
subsystem boundary. Backend = `backend/kira/`, Frontend = `frontend/src/`. (Tests
are summarised at the end; the full set lives in `backend/tests/`.)

### 6.1 Backend — `backend/kira/`

```text
backend/kira/
├── main.py              # The ONLY glue file — mounts routers, lifespan (warmups + reconcile + watcher), Basic-auth middleware
├── models.py            # All DB tables: Scan / MediaFile / Match / Setting / RenameHistory / PendingRename / Notification / SubtitleAsset
├── schemas.py           # Pydantic API models (MediaFileOut, MatchOut, …)
├── database.py          # SQLite WAL + busy_timeout; create_all + idempotent _ensure_column (no destructive ALTER)
├── config.py            # Env settings (KIRA_* prefix)
├── settings_store.py    # DB key-value settings access helpers
├── net.py               # Shared httpx client; IPv4 patch; transport kwargs
├── url_guard.py         # SSRF outbound-URL guard (is_safe_outbound_url)
├── download_guard.py    # fetch_capped (size cap + SSRF + opt-in redirects) + sniff_image + error-page guard
├── tasks.py             # spawn_tracked — detached, strong-ref'd, exception-logged background tasks
├── activity.py          # In-memory progress/activity registry (the live "pill" surface)
├── log.py               # Logging config + secret-scrubbing filter (masks api_key / token / password)
├── notify.py            # Discord / generic-webhook fan-out
├── watcher.py           # Watched-folder auto-scan daemon (awatch + debounce + poll fallback)
├── ffmpeg_setup.py      # Managed ffmpeg resolve + one-click install
├── posters.py           # Persist anime poster URLs onto matches (warm-up)
├── xattr_store.py       # Provider-ID stamping (xattr + JSON index fallback for exFAT)
├── scanner.py           # Directory walker — media discovery + exclusions (no parsing)
├── cli.py               # CLI entrypoint (scan / rename / …)
│
├── api/                 # 🟢 ROUTING LAYER  (endpoints only — heavy logic lives in the feature modules below)
│   ├── scans.py         # POST/GET /scans — scan lifecycle + clustering + match orchestration + enrich + reconcile
│   ├── rename.py        # POST /rename — rename + ordered post-hook (subtitles → media-server → Sonarr → notify)
│   ├── matches.py       # match read / select / rematch / bulk-match
│   ├── match_cleanup.py # match cleanup operations
│   ├── files.py         # GET /files (+ subtitle-coverage attach)
│   ├── subtitles.py     # coverage / backfill / upgrade / history / candidates / pick / pack-extract / pack-harvest
│   ├── images.py        # GET /img — image proxy + on-disk LRU cache (auth-exempt, SSRF-guarded)
│   ├── search.py        # manual search; anidb/picture/{aid}
│   ├── series.py        # series episode lists (CoverPopup)
│   ├── history.py       # rename history / undo / CSV export
│   ├── settings.py      # settings CRUD + provider test
│   ├── providers.py     # provider test endpoints
│   ├── integrations.py  # Sonarr / Plex / Jellyfin config + Sonarr queue
│   ├── webhooks.py      # inbound *arr webhooks (token-gated, path-traversal-guarded)
│   ├── auth.py          # status / setup / check / backdrop; password hashing
│   ├── system.py        # system resets + notifications router
│   ├── cleanup.py       # cleanup endpoints
│   ├── trash.py         # trash list / restore / purge
│   └── health.py        # container health probe (auth-exempt)
│
├── parser/              # 🟢 PARSING
│   ├── parser.py        # Orchestrator: path → ParsedFile (type/title/SxE/absolute/tokens)
│   ├── patterns.py      # Regex rules: SxE, absolute numbers, year
│   ├── format_stripper.py  # Strip release tokens ("1080p", "x265", group, HDR …)
│   ├── scene_rules.py   # Scene-naming rules + sample/extras detection
│   └── mediainfo.py     # Cracks files open to read real resolution / codec / HDR / audio / duration / embedded title
│
├── matcher/             # 🟢 MATCHING ENGINE
│   ├── engine.py        # Provider orchestration, query ladder, retries, anime guardrails, series_group_id
│   ├── cour_routing.py  # The complex AniDB multi-cour logic isolated here (table / route / precise + umbrella & franchise-absolute remaps)
│   ├── bipartite.py     # File→episode pairing (6 passes; Season-0 isolation)
│   ├── episode_validation.py  # Episode-count/list validation + rerank
│   ├── folder_lock.py   # Folder series-lock (pins a confidently-identified series folder)
│   ├── similarity.py    # normalize + trigram_similarity
│   ├── text_distance.py # levenshtein / lcs / numeric / runtime ratios
│   ├── acronyms.py      # Curated acronym table + shape detection
│   ├── cluster_signal.py# Cluster longest-shared-word-sequence (feeds ctx.cluster_signal)
│   ├── keys.py          # series_key / variant_key computation
│   ├── media_type.py    # media-type classification helpers
│   ├── strict_mode.py   # strict-match gating
│   └── cascade/         # 🟢 THE SCORING SYSTEM
│       ├── runner.py    # Runs the cascade — tier aggregation, veto, ambiguity, Observer-Mode shadow funnel
│       ├── types.py     # Metric protocol, tier bands, MetricResult, CascadeTrace, CascadeContext
│       └── metrics/     # Every single metric gets its OWN file (no 2000-line god-file)
│           ├── substring.py            # T1 · word-boundary substring-of-alias
│           ├── folder_identity.py      # T1 · parent-folder-name identity
│           ├── fribb_authority.py      # T1 · AID promotion via Fribb tvdb_season
│           ├── fribb_aid_filter.py     # veto · drop non-anime TVDB/TMDB results
│           ├── episode_count_sanity.py # veto · candidate too small for the cluster (summed cours)
│           ├── cluster_signal.py       # T2 · cluster common-sequence title (the "One Pace" fix)
│           ├── trigram.py              # T2 · char-trigram similarity
│           ├── text_metrics.py         # T2 · Levenshtein / LCS / NumericDistance
│           ├── acronym.py              # T2 · acronym-expansion match
│           ├── episode_title.py        # T2 · episode-title series boost (cache-only, NEVER fetches)
│           ├── anime_season_ordinal.py # T2 · "Nth Season" ordinal match
│           ├── anime_tvdb_jp.py        # T2 · JP-origin enrichment (tvdb)
│           ├── year_rank.py            # T3 · Year + Rank metrics
│           └── corroboration.py        # T3 · runtime corroboration (Labs, never fetches)
│
├── providers/           # 🟢 EXTERNAL APIs
│   ├── base.py          # Provider protocol, result/error types, ProviderConfig
│   ├── factory.py       # build_provider, KEYLESS_PROVIDERS
│   ├── anidb.py         # AniDB logic + 5s rate-limit gate + circuit breaker + disk ban + all caches
│   ├── anime_mappings.py# Fribb cross-reference downloads (AID ↔ TVDB/TMDB), weekly
│   ├── anime_lists.py   # ScudLee per-episode cour XML resolver (24h cache)
│   ├── tmdb.py          # TMDB metadata / episode / artwork
│   ├── tvdb.py          # TVDB metadata / episode / extended / artwork
│   ├── fanarttv.py      # fanart.tv artwork
│   ├── opensubtitles.py # OpenSubtitles.com client (search/download/token)
│   └── _osdbhash.py     # OpenSubtitles byte-hash ("Identify by content")
│
├── renamer/             # 🟢 FILE OPERATIONS
│   ├── operations.py    # Safe move/copy/symlink/hardlink; disk-space preflight; cross-device verify; authoritative undo; folder cleanup
│   ├── templates.py     # Sandboxed Jinja template rendering + 255-byte-per-component truncation
│   └── nfo.py           # Kodi/Emby .nfo + artwork output
│
├── integrations/        # 🟢 AUTOMATION (media servers / *arr)
│   ├── media_server.py  # Plex/Jellyfin library refresh (SSRF-guarded)
│   ├── sonarr.py        # Sonarr API: queue, rescan-by-tvdb, send-missing
│   └── health_monitor.py # 5-min poll of configured integrations → OK→broken Notification (§9.16)
│
└── subtitles/           # 🟢 SUBTITLE PIPELINE
    ├── aggregate.py     # Overall fetch + score logic; download_and_save / manual_pick / save_pack_entry
    ├── subcache.py      # Undo reuse-cache (.kira-subcache/): park undone .srt by OSDb hash+lang, reuse before re-download (§9.17)
    ├── scoring.py       # 0–100 candidate score + sync verdict + plain-English reasons
    ├── pack.py          # Zip/7z/rar extraction + season-pack entry ranker + byte cache
    ├── coverage.py      # wanted-vs-present coverage (present / missing languages)
    ├── prefs.py         # Settings loader (per-type langs, min-score, upgrade)
    ├── backfill.py      # Background quota-aware sweeps + upgrade + pack harvest
    ├── store.py         # SubtitleAsset history (record / remove / blacklist)
    ├── embedded.py      # ffmpeg embedded-track extraction + language normalize
    ├── _common.py       # save_sidecar / find_sidecar / zip helpers / size caps
    ├── naming.py        # <stem>.<lang>.<ext> sidecar naming
    ├── errors.py        # AuthRejected / QuotaExceeded / PackEpisodeMissing
    ├── model.py         # SearchContext / SubtitleCandidate / SubtitleFetchResult
    ├── subdl.py         # SubDL-specific API
    ├── subsource.py     # SubSource-specific API
    ├── podnapisi.py     # Podnapisi-specific API
    ├── animetosho.py    # AnimeTosho (experimental)
    └── yifysubtitles.py # YIFY movie-subtitle scraper
```

### 6.2 Frontend — `frontend/src/`

```text
frontend/src/
├── App.tsx              # Root shell — routing, toasts, modal hosts, global events
├── main.tsx             # Vite entry
├── index.css            # Global tokens / motion / base styles
│
├── pages/               # 🟢 SCREENS
│   ├── DashboardPage.tsx    # Coverage tiles / overview
│   ├── ReviewPage.tsx       # Library grid + CoverPopup host + cluster actions + popup re-sync
│   ├── HistoryPage.tsx      # Renames / Subtitles timeline (segmented toggle)
│   ├── SettingsPage.tsx     # Settings shell
│   ├── SubtitleHistory.tsx  # Subtitle ledger (score dial, delete / blacklist)
│   └── settings/
│       ├── SubtitlesCard.tsx       # Sources / variants / advanced (min-score, per-type langs, upgrade)
│       ├── IntegrationsSection.tsx # Sonarr / Plex / Jellyfin / webhooks
│       ├── PathsSection.tsx        # Library root / watch folders
│       ├── AdvancedSection.tsx     # Parsing / history / Labs / reset
│       └── helpers.ts              # SaveKeyFn + setting helpers
│
├── components/          # 🟢 UI
│   ├── LibraryGrid.tsx          # Cover cards + lazy poster fetch
│   ├── CoverPopup.tsx           # Title popup shell
│   ├── CoverPopup/              # Hero · MovieBody · SeriesBody · rows · dupeModals · ForceImportModal
│   │                            #   · MarqueeText · format · quality · types · useSonarrQueuePopup
│   ├── SubtitleBrowseModal.tsx  # Manual candidate browse + ambiguous-pack entry picker
│   ├── ActivityIndicator.tsx    # The live pill (progress / errors)
│   ├── ScanProgress.tsx         # Scan progress banner
│   ├── NotificationsBell.tsx    # Notifications dropdown
│   ├── Onboarding.tsx           # First-run wizard
│   ├── LoginGate.tsx            # Auth gate (when Basic auth on)
│   ├── FfmpegStatus.tsx         # ffmpeg health + one-click install row
│   ├── FolderPickerModal.tsx    # Server-side folder browser
│   ├── modals.tsx               # Manual-search / file-details modals
│   ├── settings-blocks.tsx      # SectionCard / FieldRow / NestedBox
│   ├── ui.tsx                   # Sidebar / Topbar / Toast / Select
│   └── base/                    # Untitled-UI kit: alert · badges · buttons · featured-icons
│                                #   · input · progress-indicators · segmented · toggle
│
├── lib/                 # 🟢 DATA / CLIENT
│   ├── api.ts           # Typed API client (+ posterSrc image-proxy helper)
│   ├── adapters.ts      # API shape → UI model (buildLibraryItems, apiToMediaFile, posterSrc wiring)
│   ├── types.ts         # Shared TS types (LibFile, LibraryItem, …)
│   ├── posters.ts       # Lazy AniDB poster cache (routes through the proxy)
│   ├── episodes.ts      # Episode-list helpers
│   ├── data.ts          # Poster / initials placeholder generator
│   ├── confBands.ts     # Confidence → label / colour bands
│   ├── format.ts        # Display formatters (bytes, etc.)
│   ├── cache.ts         # Frontend files-snapshot cache
│   ├── icons.tsx        # Icon set
│   └── utils.ts         # cn() + misc
│
├── utils/               # cx (classnames), is-react-component
└── styles/              # globals.css · theme.css · typography.css
```

### 6.3 Build / Infra & Tests
- **Infra:** `Dockerfile`, `docker-compose.yml`, `backend/pyproject.toml` (fastapi · uvicorn · sqlalchemy[asyncio] · aiosqlite · alembic · httpx[http2] · pymediainfo · watchfiles · py7zr · rarfile), `frontend/package.json` + Vite config.
- **Tests** (`backend/tests/`, ~115 files) by area: **matching**, **cascade/metrics**, **anime/cour**, **providers**, **parsing**, **rename/ops**, **subtitles**, **self-heal**, **infra/security**, **integrations**, **CLI**. Notable locks: `test_absolute_cour_routing`, `test_cour_routing_precise`, `test_bipartite_*`, `test_funnel_observer`, `test_subtitles_pack`, `test_image_cache_evict`, `test_disk_space_preflight`, `test_log_scrub`, `test_rename_e2e`, `test_scan_reconcile`, `test_url_guard`.

---

## 7. Reviewer's Notes — common misconceptions (read before auditing)

These are the exact traps a reviewer working from the data flow alone tends to fall into. Each is settled by the code:

1. **Title-rescue is PRE-match, not background.** `_maybe_rescue_title_from_mediainfo` runs inside `_match_phase` *before* a cluster is scored (§1.5.1, [scans.py:1716](backend/kira/api/scans.py)). There is **no** "rescued title sits dormant until manual rematch" gap. The §1.6 MediaInfo pass adds tech tags only and never re-matches.
2. **`auto_approve` never renames.** It flips `status → approved` ([scans.py:117](backend/kira/api/scans.py)); the scan worker performs **no file I/O**. Renaming is a separate, explicit `POST /api/v1/rename` (§1.8).
3. **SQLite is WAL + `busy_timeout`.** Under WAL, UI reads (`/files`, `/scans`) never block on the scan worker's writes, and a competing UI *write* **waits** (up to the timeout) rather than throwing `database is locked`. `SCAN_COMMIT_EVERY = 5` is a tunable; raising it (or moving live progress onto the `activity.py` in-memory surface) is an optimization, not a correctness fix.
4. **`EpisodeCountSanity` does not naively veto on `cluster > count`.** It applies a `_COUNT_MARGIN = 0.9`, **abstains** when sibling/franchise counts are uncached, and rescues via **Fribb sibling** and **whole-franchise** aggregates before vetoing. The off-by-one "airing show" case (13 files vs cached 12) **passes**. (Known narrow edge: a single-AID airing show that gained ≫10% beyond a stale cache — see §8.)
5. **xattr fragility is handled.** `xattr_store.write_ids` catches ENOTSUP/EACCES/ENOENT and returns `False` → caller falls back to normal matching; a **path-keyed JSON index fallback** persists IDs on exFAT/no-xattr filesystems; `setxattr` stamps the **target** (correct for symlink mode).
6. **Rename → media-server ordering (fixed).** Subtitles are written **before** Plex/Jellyfin/Sonarr are told to re-scan (§1.8 ORDERING INVARIANT), so the media server indexes the file and its `.srt` in one pass.
7. **Ghost files ARE pruned.** `_prune_missing_files` (§1.6.1) deletes `MediaFile`+`Match` rows for files confirmed gone (`FileNotFoundError`) under a walked root. Deleted-on-NAS and `move`-d-out rows don't accumulate. NAS/permission errors never trigger a delete.
8. **Crash mid-rename IS recovered.** `PendingRename` is reconciled in the **startup lifespan hook** ([main.py:136](backend/kira/main.py)) — it checks the filesystem and finalizes or discards each intent (§1.7). Not a black hole.
9. **Watched-folder is not an infinite loop.** A rename into a watched `library_root` re-triggers a scan, but the file is already a known `MediaFile` (`file_path` unique, `status=renamed`) so it's recognized — no new row, no re-rename. Debounce (30s) + skip-if-scan-running guard it; the watcher is opt-in. (A mute-on-rename optimization is listed in §8, but there's no correctness loop.)
10. **Sidecars/NFO go to the TARGET, not the source** (§1.8) — correct for `symlink`/`hardlink` mode.
11. **Subtitle backfill is metered + stops on quota.** The post-scan backfill runs SEQUENTIALLY and `break`s the whole batch on `QuotaExceeded`/`AuthRejected` ([backfill.py](backend/kira/subtitles/backfill.py)) — it does NOT fire N blind parallel requests. (OpenSubtitles "quota" is a per-account/day soft refusal, not a 12h IP ban like AniDB.) The rename-time auto-fetch is the one path that's bounded-parallel without early-stop — see §8.
12. **Sonarr rescan assumes aligned roots** (§1.8) — works when Kira's library root == Sonarr's root; a silent no-op otherwise, by design.
13. **Image proxy cache is bounded.** `api/images.py` now runs a throttled, off-loop **LRU eviction** (`_evict_lru`, 2 GiB cap → trim to 80% by mtime, every 200 writes) — `.cache/images/` can't silently fill the disk on a 5k-series library.
14. **Pack extraction runs OFF the event loop.** `choose_from_pack`/`extract_entry` (sync zip/7z/rar decompress + the RAR backend subprocess) are wrapped in `asyncio.to_thread` in `aggregate.py`, so a pack extract can't freeze the app. (OOM isn't a real risk anyway — the archive download is capped at 64 MiB by `MAX_ZIP_BYTES`, and only subtitle entries are decompressed, never bundled fonts; 7z extracts to a temp dir, not memory.)
15. **Season-0 isolation in bipartite.** A Special (`S00E05`) is excluded from the season-agnostic passes (Pass 3 / 3.5), so it can't overwrite main-run episode 5; it pairs only via the exact `(0, e)` lookup. Also fixed `_ep_key` coercing a real season 0 → 1.
16. **MAX_PATH:** per-component truncation to 255 bytes IS done (`templates.py`); the **total** Windows-260 path length is NOT yet bounded — see §8.
17. **Startup boot order is correct.** Lifespan reconciles (orphaned scans → pending renames) **before** `watcher.start()` ([main.py:126/137 → 213](backend/kira/main.py)), and the watcher records a baseline without firing on first arm — so it can't scan a half-moved file before recovery completes.
18. **Partial/still-copying files don't get moved.** The watcher debounce is keyed on the **last** event, so a growing 10 GB copy keeps resetting the timer and the scan fires only ~30s after the copy settles; temp extensions (`.part`/`.crdownload`/`.!qb`/…) are ignored ([watcher.py](backend/kira/watcher.py)). And the watcher only triggers **discovery/match, never an auto-rename** — so a locked/incomplete file is never moved (a partial MediaInfo read just degrades and is re-read on the next scan). A per-file size-settle check is an optional hardening (§8).
19. **File mode IS preserved on rename; ownership is the only gap.** The cross-FS copy does `shutil.copy2` + `shutil.copystat` ([operations.py](backend/kira/renamer/operations.py)) — the source's mode/mtime carry over, so there's no "chmod 600" regression. Only uid/gid ownership isn't copied, which matters only in mixed-UID Docker/NAS setups (moot on Windows/NTFS, which inherits parent ACLs). PUID/PGID/umask setting is a candidate (§8).
20. **SQLite migrations sidestep the `ALTER` trap.** Schema evolves via `create_all` (tables) + an idempotent `_ensure_column` (`ADD COLUMN`-if-missing — natively supported by SQLite), all in one transaction ([database.py](backend/kira/database.py)). Kira **never** issues a destructive `ALTER` (drop/alter column), so the "SQLite can't ALTER" problem doesn't arise. Alembic baseline-stamps (`migrations/`) for forward-compat; any future destructive revision must use `op.batch_alter_table()`.
21. **The scan lock can't dangle forever.** A detached scan worker's `finally` releases the DB scan lock + rolls back the session on cancellation; even on a hard SIGKILL, the startup `reconcile_orphaned_scans` releases the dangling lock and resets stuck `matching` files at next boot (§1.7). A proactive SIGTERM cancellation token is a candidate (§8), not load-bearing.
22. **Disk space is checked BEFORE a copy.** `execute_op` calls `_ensure_space` (`shutil.disk_usage` vs source size + 16 MiB margin) on the COPY path and the cross-device-move stream copy ([operations.py](backend/kira/renamer/operations.py)) — a too-small destination fails clean with a clear error and **writes nothing**, instead of crashing mid-write with ENOSPC and leaving a multi-GB partial. (The cross-device path additionally rolls back any partial on failure.)
23. **API keys are scrubbed from logs.** `log.py` installs a root-handler `_SecretScrubFilter` (`scrub_secrets`) that masks `api_key`/`apikey`/`token`/`password`/etc. `=`/`:` values from every log line — so a TMDB `?api_key=…` URL inside an httpx error repr can't leak when a user pastes logs into a GitHub issue. TVDB uses a Bearer header (not in the URL) anyway.
24. **Media-server refresh is per-rename-request, not per-file.** `refresh_all` is called **once** after the whole batch loop ([rename.py:1889](backend/kira/api/rename.py)) — a 300-file bulk rename fires one Plex/Jellyfin refresh, not 300, so no scan-thrashing. (A cross-*request* debounce for rapid successive renames is an optional extra — §8.)

---

## 8. Known limitations / candidate follow-ups
- **`EpisodeCountSanity` + fast-airing single-AID show:** if a not-yet-cached number of new episodes exceeds the 0.9 margin and the show has no Fribb sibling cours, a stale count could false-veto the correct AID. Candidate fix: when `own_count` is short but the on-disk cluster is larger, re-fetch that AID's count (ban-gated) before vetoing.
- **Scan progress write pressure:** `SCAN_COMMIT_EVERY = 5` commits `current_path`/`file_count` frequently during large scans; could be raised or routed through `activity.py` to further reduce write-lock contention.
- **xattr write failure is not surfaced:** `write_ids` returning `False` is silent (matching still works without the stamp); could be logged/notified.
- **Rename-time subtitle quota:** the post-rename auto-fetch (§1.8 step 6) runs bounded-parallel (`rename.concurrency`) and swallows per-file errors (`return_exceptions=True`), so it doesn't early-stop when a provider quota is spent — a huge one-shot rename wastes calls after the cap (failures, not a ban). Candidate: share the backfill's stop-on-`QuotaExceeded` discipline here too.
- **Watcher mute on self-writes:** a rename into a watched `library_root` causes one redundant (harmless) rescan; muting the watcher for the rename target — or excluding `library_root` from event listening — would remove the redundant pass.
- **Sonarr path divergence:** if a user's Kira library root and Sonarr root differ, the `RescanSeries` hook is a silent no-op. Candidate: detect the mismatch and warn (don't auto-repath Sonarr).
- **Orphaned sidecars on symlink delete:** deleting a library symlink leaves its `.srt`/artwork behind; the cleanup endpoints could sweep sidecars whose video no longer exists.
- **Windows MAX_PATH (total length):** per-component is truncated to 255 bytes, but the cumulative `library_root + rendered template` can still exceed Windows' 260-char limit on a deep NAS share + long light-novel titles → `OSError` on the move (recovered at next boot by the PendingRename reconcile, but the rename fails). Candidate fix: budget-aware truncation of `{{title}}`/`{{episode_title}}` against `260 − len(root) − fixed parts`, or emit the `\\?\` long-path prefix on Windows. Deliberately deferred — naive truncation risks target collisions, so it needs care.

- **Watcher per-file size-settle:** the debounce handles growing files at the folder level, but an explicit "size unchanged across two stats before queueing" check would harden the rare single-event-then-silent-write copy. (Low marginal value — rename is manual, so a partial file is never moved.)
- **UID/GID ownership on rename (Docker/NAS):** mode is preserved but ownership isn't; a `PUID`/`PGID`/`umask` setting (applied post-rename) would keep a mixed-UID media server readable. Linux/Docker-only; needs root for `chown`.
- **Proactive shutdown cancellation:** a `SIGTERM` → `asyncio.Event` token checked by `_scan_worker` between files would let a graceful restart stop the scan cleanly, rather than relying on task-cancel `finally` + next-boot reconcile (which already prevent a permanently dangling lock).
- **Future destructive SQLite migration:** the day a column must be dropped/altered (not just added), the Alembic revision must wrap it in `op.batch_alter_table(render_as_batch=True)` — the `create_all` + `_ensure_column` path only ever ADDs.

- **Cross-request media-server refresh debounce:** refresh is already once-per-rename-request (§7 note 24), but rapid *successive* renames each fire one; a dirty-flag + 15–30s coalesce before issuing a single library scan would smooth that. Low priority (per-request is already non-thrashing).
- **Show-root poster is first-cour-wins (§9.21):** per-season anime posters now ship, but the series-level `poster.jpg` is still whichever cour renamed first (write-if-absent), not guaranteed to be the earliest/franchise cour's art. Candidate: have `_anime_group_members` also return `poster_url` so the show poster can prefer the earliest member.
- **Settings nav guard misses hash back/forward (§9.19):** leaving Settings via the sidebar/topbar confirms unsaved edits, and `beforeunload` covers refresh/close, but a browser **back/forward** (hashchange) bypasses the guard and drops the draft. Candidate: intercept `popstate`/`hashchange` when dirty.
- **Settings-gating needs two backend signals (§9.22):** the prerequisite-gating pass can't fully gate `parsing.read_mediainfo` (no-op without the native MediaInfo lib) or `set_permissions` uid/gid (Unix-only `os.chown`) because neither capability is exposed to the frontend. Candidate: add `mediainfo_available` + `posix` to a `GET /system` payload, then SOFT-disable/hint those. (Today `read_mediainfo` self-narrates via a warning Notification on toggle.) Also: AcoustID's whole card configures a not-yet-implemented provider (`IMPLEMENTED_PROVIDERS` = tmdb/tvdb/anidb) — its gated toggle is harmless but vacuous until the provider lands.

### Resolved in the latest hardening pass
- ✅ **Image proxy cache eviction** (`api/images.py` LRU GC, §7 note 13).
- ✅ **Pack extraction off the event loop** (`asyncio.to_thread`, §7 note 14).
- ✅ **Season-0 bipartite isolation** + `_ep_key` season-0 fix (§7 note 15).
- ✅ **Rename → media-server ordering** (subs before refresh, §1.8).
- ✅ **Disk-space preflight on copy** (`_ensure_space`, §7 note 22).
- ✅ **Credential scrubbing in logs** (`log.scrub_secrets` filter, §7 note 23).
- ✅ **Undo leaves no orphans + is recoverable** (§9.17): alias-aware (Z:↔UNC) asset cleanup, `cleanup-orphans` endpoint/button, an xattr-stamp identity check that refuses undo on a *replaced* file (greyed in History), trash-on-undo, and a subtitle reuse-cache.
- ✅ **Tech-tag pass is visible + dependable** (§9.18): folded into the scan popup as a 3rd line; a rescan backfills files missing tags.
- ✅ **Settings can't be silently corrupted** (§9.19): explicit Save/Cancel draft replaces auto-save (kills the browser-autofill-overwrites-a-key bug); credential inputs set `autocomplete="off"`.
- ✅ **NFO/artwork name the unified show** (§9.20–9.21): `<showtitle>`/tvshow `<title>` use the franchise `library_title`; anime cours get per-season `Season NN/poster.jpg`.

---

## 9. Settings & Configurability Audit (provider choice + IA)

A standing audit of what the user *can* configure, what they *can't but should*, and where the Settings UI puts it. Direction agreed with the owner: **provider selection should be modular and settings-driven** (today it's single-select), and the Settings IA should be reorganised around the user's mental model. Findings below cite `file:line`; a multi-agent sweep with an adversarial verifier ran to completion — only **confirmed** gaps are recorded. The deep sweep's additional finds are triaged in **9.7**.

### 9.0 Status — §9.8 build plan COMPLETE (2026-06-16)
All four tiers shipped + verified (backend suite ~1030 passing, `tsc` + `vite build` clean):
- **Tier 1** ✅ confidence-band wiring (Review / Dashboard / bulk) + embedded extract honors `subtitles.forced`.
- **Tier 2** ✅ reorderable per-type provider order, TMDB-for-anime, `matching.anime_crossref_order`; + the dropdown-portal & two-independent-column UI fixes.
- **Tier 3** ✅ Settings IA reorg — banded rail (Sources & library / Identification / Output / System), **Matching** home, **Subtitles** promoted, Connections keys-only.
- **Tier 4** ✅ `providers.tvdb.language`, `rename.symlink_relative`, `rename.set_permissions`, `rename.on_conflict`.

**Dropped by deliberate, verified choice:** the §9.7 Low cosmetic tail (misattributed / marginal / intentional-safety — see §9.8 Batch 2) and `on_conflict`'s `suffix` mode.
**Deferred (new backend, not recomposition):** Account/Security (needs a password-change endpoint).
**Not yet built — new features beyond settings-exposure (§9.3 "High"):** `naming.title_language` (anime EN / romaji / native output), notifications depth (failure events + per-event toggles), media-server targeted / `refresh_on_rename`. **Per-media-type config (the TMM question): RESOLVED** — shipped per-type subtitle **sources + min_score** (the validated win, §9.10) with discoverable dropdown+chips pickers; NFO/artwork per-type deferred. See §9.9/§9.10.

### 9.1 Already user-configurable (do NOT re-flag)
Verified exposed (backend reads **and** frontend renders a control): `matching.auto_approve`/`auto_threshold` (Confidence), `matching.high_threshold`/`mid_threshold`, `matching.provider_order.*` (the *key* is wired; only the UI shape is a gap — see 9.2#1), all `naming.*` (profile + custom templates, `anime_numbering`, `write_nfo`+`nfo_fields`, `download_artwork`+`artwork_types`), all `rename.*` (mode, default_op, concurrency, stamp_ids, cleanup_*, trash_dir, trash_retention_days), `history.retention_days`, `parsing.read_mediainfo`/`mediainfo_authoritative`, every `subtitles.*` knob (per-type langs, min_score, upgrade/upgrade_below, source enable/disable toggles), `paths.*` incl. `scanning.ignore_patterns`, `watch.config` (auto_scan, debounce, poll, per-folder mode+threshold), Sonarr per-type config, `labs.*`. **No phantom settings** were found (every backend-read key has a control).

### 9.2 Provider-choice gaps (the modular-provider work)
| # | Gap | `file:line` | Fix | Pri |
|---|-----|-------------|-----|-----|
| 1 | Provider order is **single-select**; backend reads a full ordered list but UI writes a 1-element list | UI `SettingsPage.tsx:280`; reader `engine.py:128` | reorderable enable/disable list per type — **zero backend change** | High |
| 2 | **Anime can't add TMDB** (picker = `['anidb','tvdb']`) | `SettingsPage.tsx:417` | add `tmdb` to anime candidates | High |
| 3 | **Episode-title fallback provider hardcoded** TVDB→TMDB (the live "Episode 1166" pain) | `series.py:247-259` | `matching.episode_title_fallback.anime` ordered list | High |
| 4 | NFO metadata cross-ref hardcoded TVDB→TMDB | `engine.py:1246` | fold into #3's setting | Med |
| 5 | Subtitle source **order** hardcoded (enable/disable already exists) | `subtitles/aggregate.py:46` | `subtitles.source_order` (drag) | Low |
| 6 | OpenSubtitles implicitly on whenever a key exists (no toggle) | `subtitles/prefs.py:85` | `subtitles.opensubtitles` bool | Low |
| 7 | Artwork source order hardcoded (fanart.tv→provider) | `api/rename.py:730` | `naming.artwork_source_order` | Low |

**Status: ✅ #1–#4 shipped (Tier 2).** Provider preference is now a reorderable per-type list that writes the **full** ordered list (#1); anime gains TMDB as a candidate (#2). Episode-title + NFO cross-ref order are unified under one new setting **`matching.anime_crossref_order`** (default `["tvdb","tmdb"]`, soft) read by `resolve_anime_crossref_order()` and honored at `series.py` (`_anidb_episodes_via_cross_ref`) + `engine.py` (`_anime_metadata_via_cross_ref`) — #3 + #4 folded together. #5–#7 (subtitle/artwork source order) remain — Low, deferred.

### 9.3 Other configurability gaps (by subsystem)
- **Notifications** (`notify.py`) — only **2 hardcoded events** (rename-done `api/rename.py:1846`, scan-found `api/scans.py:2307`), both success/info; **no failure/error notifications**, no per-event toggles, no Discord username/avatar/template, no generic-webhook auth header. → `notifications.events`{...}, `notifications.discord.*`, `notifications.webhook.headers`. **High** (event selection + failures).
- **Media servers** (`integrations/media_server.py:46,71`) — Plex/Jellyfin refresh **all** libraries on every rename; no per-library selection, no `refresh_on_rename` toggle. → `integrations.{plex.section_ids,jellyfin.library_ids,*.refresh_on_rename}`. **High**.
- **Naming title language** (`engine.py`/`templates.py`) — no English/romaji/native choice for anime output names. → `naming.title_language`. **High** (top anime-renamer ask).
- **Sonarr** (`sonarr.py`) — `rescan_after_rename` always on (`:137`); `search_on_add` hardcoded false (`:271`); no persistent `import_mode` default (`:1284`). **Med/Low**.
- **Watcher** (`watcher.py`) — no quiet-hours window; no per-folder op/profile (`:457`); temp-suffix ignore list hardcoded (`:70`). **Med**.
- **Subtitles depth** — no sync-confidence gate (`subtitles.min_sync`), no max-age/min-downloads filters, no global blacklist UI; embedded extract ignores `subtitles.forced` (see 9.4). **Med/Low**.
- **Parsing/naming output** — keep-release-group toggle, multi-ep render style, separator/case, `quality` fallback "1080p" can write a wrong tag (`templates.py:378`), TMDB language dropdown only 5 options (`engine.py:1610`). **Med/Low**.
- **Matching** — strict-mode has no user toggle (`strict_mode.py`/`watcher.py:445`); heal cutoff hardcoded 0.50 (`api/integrations.py:776`). **Med/Low**.
- **Auth/security** — no in-app Account surface (change password / sign-out / toggle); `/img` pre-auth exposure is an accepted trade-off but could be a `auth.protect_images` toggle. **Low**.
- **Display** — no timezone/date-format/sort-order; history fixed `created_at DESC`. **Low–Med**.
- **Scanner** — video/audio extension sets hardcoded (`scanner.py:15`), no `min_file_size`, NAS-prefix list not extendable. **Low**.

### 9.4 Bugs surfaced by the audit (not just missing knobs)
- **Confidence-band sliders silently ignored** on the surfaces users look at most: `ReviewPage.tsx:68`, `DashboardPage.tsx:382`, and bulk approve/rename `App.tsx:959` hardcode `85`/`50`/`90` instead of routing through `confBands.confLevel()`. Fix = wiring, no new setting. **High**. — **✅ FIXED:** Review (filter + counts + select-high-conf), Dashboard (matched/low-conf cards), and the bulk approve/rename hotkeys now route through `confLevel()`/`getConfBands()`. The Dashboard 4-bin donut (strong/likely/review/low @ 90/75/50) keeps its own finer split by design.
- **`subtitles.embedded.extract` ignores `subtitles.forced`** (`subtitles/embedded.py:130,156`) — a `forced=only` user still gets the non-forced embedded track. The pref applies on the external-search path but not the embedded path. — **✅ FIXED:** `extract()` now takes a `forced` arg (`only`→prefer forced w/ soft fallback, `exclude`→never a forced track, `''`/`include`→unchanged), wired from `ctx.forced` at `aggregate.py:117`; +3 regression tests.

### 9.5 Settings IA — reorg
**Current:** 8 flat sections. Problems: provider-order lives in **Connections** (credentials) while the rest of matching (Confidence, Labs) is elsewhere → **no "Matching" home**; **Subtitles is a full section crammed into a card under Naming**; `scanning.ignore_patterns` buried in the per-type-destinations card; duplicate nav icons (Connections/Integrations, Labs/Advanced); **no Account/Security surface** (login-gate only).

**Proposed** (group the rail into bands; create the missing Matching home so the modular-provider feature lands cleanly):
```
Sources & Library:  Connections (keys only) · Library & paths (+ Scan inputs) · Integrations
Identification:     ★ Matching  ← provider-order + Confidence + Labs folded in
Output:             Naming · ★ Subtitles (promoted to top-level) · Folder cleanup
System:             Advanced (+ ★ Account/Security)
```
Reorg is mostly *recomposition* of existing `settings-blocks.tsx` primitives (`SettingsLayout`/`SectionCard`/`GroupLabel`/`SettingsFilter`); state hooks already live in `SettingsPage.tsx`. **Keep as-is** (already exemplary): Folder cleanup (master→nested disclosure + transparency), Advanced→Danger zone (escalating reset), the Connections `ProviderCard` pattern, Sonarr per-type flavor split.

**✅ Shipped** (see §9.8 Tier 3): banded rail, Matching home (provider-order + Confidence + Labs), Subtitles promoted to top-level. Account/Security is the one piece **deferred** — needs a backend password-change endpoint, so it's a follow-up, not part of this recomposition.

### 9.6 Intentionally internal — never expose
Image cache cap/eviction (2 GiB, `images.py`), subtitle/zip size caps + zip-bomb guards (`pack.py`, `_common.py`), AniDB rate-limit gate + circuit breaker (`anidb.py` — loosening risks the 12h ban), 255-byte component truncation (OS limit), cascade `TIER_BANDS` + per-metric raw scores (the scoring math), PBKDF2 iterations, provider connect-backoff/timeouts. These are correctness/security/abuse guards, not preferences.

### 9.7 Verified deep-hunt — additional confirmed gaps
A 6-finder sweep + adversarial verifier returned **57 hardcoded-and-unexposed points, 0 refuted**. That 0-refuted is *not* a clean bill of health: the finders surfaced factually-true "hardcoded + no control" items and the verifier confirmed the **facts** (each value really is hardcoded and really has no setting) — but it did **not** rule on the *judgment* of "is this worth a knob?". That triage is the owner's, applied below. Two high-value claims were spot-checked against source before recording — both real: TVDB search hardcodes `"language": "eng"` (`tvdb.py:180,199`) and symlinks always write an absolute target (`os.symlink(str(src), str(dst))`, `operations.py:517`). Items already captured in 9.1–9.6 are **not** re-listed (`naming.episode_title_fallback`→9.2#3, embedded-ignores-`forced`→9.4, per-type `subtitles.min_score`→already global-exposed per 9.1, TMDB language→9.3).

**A — worth exposing (new, survive the "is it a real preference?" test):**
| Gap | `file:line` | Setting | Pri |
|-----|-------------|---------|-----|
| Symlinks always absolute-target → break if the library root remounts/moves (e.g. `Z:`↔UNC, Docker bind path) | `operations.py:517` | `rename.symlink_relative` (bool) | **✅ shipped** |
| No post-rename ownership/perms — files land owned by the container UID, wrong group on NAS | `operations.py:478-519` | `rename.set_permissions`{uid,gid,dir_mode,file_mode} (ties to §8 UID/GID note) | Med |
| TVDB search hardcodes language `"eng"` — non-English users get English-biased results (mirrors TMDB-lang 9.3) | `tvdb.py:180,199` | `providers.tvdb.language` | **✅ shipped** |
| Conflict policy on an existing target is fixed in code | `operations.py` | `rename.on_conflict` (skip/suffix/overwrite) | Med |
| Dual-audio variant language tags hardcoded (the overwrite footgun — two audio variants collide on one name) | `templates.py` | `naming.variant_languages` | Med |
| Sidecar extension set hardcoded (what travels with the video) | `operations.py` | `rename.sidecar_extensions` | Low |
| Specials/extras folder name fixed ("Specials"/"Season 00") | `templates.py` | `naming.specials_folder` | Low |
| Disc/volume marker render fixed | `templates.py` | `naming.disc_marker` | Low |
| Default season when none parsed = 1 | `templates.py` | `naming.default_season` | Low |
| Default extension fallback fixed | `templates.py` | `naming.default_extension` | Low |
| File-size unit (GiB vs GB) fixed in the size token | `templates.py` | `naming.size_unit` | Low |
| Per-episode subfolder layout fixed | `templates.py` | `naming.subfolders` | Low |
| `follow_symlinks` during scan hardcoded | `scanner.py` | `scanning.follow_symlinks` | Low |
| Startup catch-up scan always-on (no opt-out for big libraries) | `watcher.py` | `watch.startup_catchup` | Low |
| Fansub-implies-anime heuristic not toggleable | `parser.py` | `parsing.fansub_implies_anime` | Low |
| Trust-embedded-provider-IDs not toggleable | `parser.py` | `parsing.trust_embedded_ids` | Low |
| Min password length hardcoded | `auth.py` | `auth.min_password_length` | Low |
| Frontend poll intervals + page sizes hardcoded | frontend pages/hooks | `display.poll_interval`, `display.page_size` | Low |

**B — confirmed hardcoded but lean KEEP-INTERNAL (the verifier rubber-stamped these as "user-facing"; they are tuning, not preferences):** episode-coverage floors / promote / margin (`episode_validation.py`), bipartite thresholds + min-cluster (`bipartite.py`), `folder_identity_threshold` / `folder_lock_min_agree`, `anidb_search_floor`, episode-title match floors, `subtitles.scoring_weights` / `candidates_per_source` / `backfill_concurrency` / `upgrade_min_gain`, custom-acronyms list, `anime_mappings` cache TTL. Exposing matcher floors invites silent mis-matches the user can't diagnose — same rationale as 9.6. If ever surfaced, gate behind a **default-off Labs "power tuning"** group, never the main rail. `matching.tvdb_episode_order` (default/dvd/absolute) is the one borderline member — genuinely useful (Plex exposes it) but changes numbering *wholesale*, so it belongs in that same Advanced-only group, not Naming.

### 9.8 Phase 1 build plan (prioritized — review before I build)
A consolidated, ordered plan derived from 9.2 / 9.4 / 9.5 / 9.7, highest-value-lowest-risk first. **Nothing here is built yet** — this is the review surface so the owner can see the whole shape before any code lands. Each tier is independently shippable; later tiers assume earlier ones exist.

**Tier 1 — Honesty bugs (pure wiring · no new setting · no migration).** Make "what we show" equal "what we'd act on". Ship first: risk-free and fixes a live trust issue.
| # | Bug | `file:line` | Fix | Risk |
|---|-----|-------------|-----|------|
| 1a | Confidence-band sliders silently ignored on the surfaces users look at most — Review / Dashboard / bulk-bar hardcode `85`/`50`/`90` instead of routing through `confBands.confLevel()` | `ReviewPage.tsx:68`, `DashboardPage.tsx:382`, `App.tsx:959` | route the three call sites through `confBands.confLevel()`; delete the literals | Low — frontend-only, reads a setting that already exists |
| 1b | `subtitles.embedded.extract` ignores `subtitles.forced` — a `forced=only` user still gets the non-forced embedded track | `subtitles/embedded.py:130,156` | apply the same forced filter the external-search path already uses | Low — one guard mirroring existing logic |

**Status: ✅ Tier 1 shipped** — confidence-band wiring across App/Dashboard/Review; embedded `forced` arg + aggregate wiring; +3 regression tests. Verified: backend full suite **1012 passed**, frontend `tsc --noEmit` clean, `vite build` clean.

**Tier 2 — Modular provider selection (the agreed headline feature).** The backend already *reads* an ordered list; only the UI *writes* a 1-element array — so most of this is frontend.
| # | Gap | `file:line` | Fix | Backend change? |
|---|-----|-------------|-----|-----------------|
| 2a | Provider order is single-select | UI `SettingsPage.tsx:280`; reader `engine.py:128` | reorderable enable/disable list per media type | none — UI only |
| 2b | Anime can't add TMDB (picker = `['anidb','tvdb']`) | `SettingsPage.tsx:417` | add `tmdb` to the anime candidate set | none |
| 2c | Episode-title fallback hardcoded TVDB→TMDB (the live "Episode 1166" pain); NFO cross-ref hardcoded the same way | `series.py:247-259`, `engine.py:1246` | new `matching.episode_title_fallback.anime` ordered list; fold the NFO cross-ref into it | new key + reader |

**Status: ✅ Tier 2 shipped** — reorderable per-type provider lists (all 3 types, writes the full order) + TMDB-for-anime + a unified anime cross-ref order setting (**`matching.anime_crossref_order`** — named for its dual episode-title + NFO use, not `episode_title_fallback`) with a `resolve_anime_crossref_order()` reader and both call sites refactored. UI landed in the existing Connections card per the owner's "build into current Settings now" call. Verified: backend **1016 passed** (+4 cross-ref tests), `tsc` + `vite build` clean. The browser screenshot was blocked by the onboarding gate (frontend-only preview, no backend) — **not** bypassed, per the no-onboarding-against-real-backend rule.

**Tier 3 — Settings IA reorg (recomposition · no new backend · see 9.5).** Create the missing **Matching** home so 2a–2c land cleanly, promote **Subtitles** to top-level, add an **Account/Security** surface. Pure recomposition of existing `settings-blocks.tsx` primitives; state hooks already live in `SettingsPage.tsx`.

**Status: ✅ Tier 3 shipped (recomposition parts).** Nav is now banded (Sources & library / Identification / Output / System) via a `group` field on `settingsSub` rendered through a `flatMap`. New **Matching** section (`#/settings/matching`) folds in the provider-order lists + anime cross-ref (moved out of Connections, now keys-only) + Confidence (auto-approve, badge cutoffs) + the old Labs cards under an "Experimental boosts" group; the standalone `confidence`/`labs` sections are gone. **Subtitles** promoted to its own top-level Output section (was a card under Naming). Verified: `tsc` + `vite build` clean; no stray refs to the removed section keys. **Account/Security DEFERRED** — it needs a new backend password-change endpoint (sensitive auth work), which is out of scope for a pure-recomposition pass; tracked as a follow-up rather than bundled here.

**Tier 4 — 9.7-A knobs (additive · do *after* the IA exists so they have a home).** Land worth-exposing settings High→Low: `rename.set_permissions` (Docker/NAS ownership) and `rename.symlink_relative` (portable symlinks) → `providers.tvdb.language` → `rename.on_conflict`, `naming.variant_languages` → the Low batch (`naming.specials_folder`/`disc_marker`/`default_season`/`default_extension`/`size_unit`/`subfolders`, `rename.sidecar_extensions`, `scanning.follow_symlinks`, `watch.startup_catchup`, `parsing.fansub_implies_anime`/`trust_embedded_ids`, `auth.min_password_length`, `display.poll_interval`/`page_size`). **9.7-B stays internal** unless a default-off Labs "power tuning" group is later added.

**Status: ⏳ Tier 4 in progress.** Batch 1 ✅ shipped: **`providers.tvdb.language`** (TVDB search language — mirrors TMDB's `self.language`; `_tvdb_language_code` reader + factory wiring + TVDB Connections "Search language" select; 3 tests) and **`rename.symlink_relative`** (relative symlink targets — `execute_op` gains the flag, idempotency check fixed to resolve the relative target against dst's dir; Advanced toggle; 3 tests, skipped on Windows / run on Linux). Verified: backend **1019 passed**, `tsc` + `vite build` clean. **Remaining (next batches):** `rename.set_permissions` (chmod/chown — platform-nuanced), `rename.on_conflict` (entangled with the existing dup-target guard — needs care), `naming.variant_languages` + the Low cosmetic naming batch (`specials_folder`/`disc_marker`/`default_season`/`default_extension`/`size_unit`/`subfolders`), `rename.sidecar_extensions`, `scanning.follow_symlinks`, `watch.startup_catchup`, `parsing.fansub_implies_anime`/`trust_embedded_ids`, `auth.min_password_length`, `display.poll_interval`/`page_size`.

**Batch 2 — verification outcome (no code shipped, by design):** a hands-on pass found the cosmetic-naming batch does **not** justify the work, which vindicates the §9.7 "0-refuted = padded list" skepticism:
- `naming.specials_folder` + `naming.size_unit` — **misattributed**: no such strings in `templates.py` (it renders `Season {{s2}}` → `Season 00`, and has no size token there). The finders flagged values that live elsewhere or not at all.
- `naming.quality_fallback` (the real `"1080p"` footgun, `templates.py:378`) + `naming.disc_marker` (`:485`) — real, but `_build_ctx` has no settings access; wiring them means threading a param through the widely-called `format_target_path` + every caller, for medium-low value.
- `naming.variant_languages` + `naming.subfolders` — genuinely complex (variant-suffix logic / layout), not "cosmetic."
- `auth.min_password_length` (`auth.py:183`, hardcoded `6`) — coupled to the **deferred Account/Security** surface; no UI home until that's built.
- `scanning.follow_symlinks` — **intentional safety**: not following directory symlinks is the fix for the unbounded-recursion → OOM bug (`scanner.py:156`). Exposing it would reintroduce that. **Reclassified to 9.6 (intentionally internal) — do NOT expose.**

**Recommendation: stop the Low long tail.** The only genuinely-valuable Tier-4 remainder is `rename.set_permissions` (Docker/NAS ownership) and `rename.on_conflict` (skip/suffix/overwrite) — both touch the rename core and deserve their own careful, tested passes, not a quick batch.

**Batch 3 — ✅ `rename.set_permissions` shipped:** best-effort post-rename chmod/chown (octal `rename.file_mode`/`dir_mode` + `rename.owner_uid`/`owner_gid`) via `_apply_permissions` in `operations.py`, threaded through `execute_op`, resolved by `_resolve_permissions` in `rename.py`; Advanced UI = master toggle + revealed fields. chown is Unix-only; every step swallows errors so a perms failure never fails the rename. **7 tests** (platform-independent via monkeypatched chmod/chown). **Batch 4 — ✅ `rename.on_conflict` shipped:** `execute_op` raises a new `RenameSkipped` at the genuine-conflict point for policy `skip` (the rename endpoint records it as an *unchanged* no-op, `ok=True` old==new, not a failure); `overwrite` folds into the overwrite flag (caller passes `overwrite=True`, existing replace path); `error` stays the default (FileExistsError surfaces as a failed item). All idempotent re-runs (same file already in place) stay a no-op regardless of policy. **`suffix` intentionally dropped** — auto-"(2)" duplicates aren't wanted in a media library and would need intent re-journaling. Advanced UI = a 3-option select; **4 tests**; resolved via `_resolve_str_setting` in `rename.py`, threaded to both video + sidecar call sites. **Net: Tier 4's worthwhile knobs are all done** (`tvdb.language`, `symlink_relative`, `set_permissions`, `on_conflict`); only the verified-marginal Low tail + Account/Security (Tier 3, needs a backend auth endpoint) remain unbuilt by choice.

### 9.9 Per-media-type configurability — open question (TinyMediaManager comparison)
TMM treats Movies / TV as separate modules with fully independent NFO / subtitle / artwork / scraper / renamer settings. Kira already does per-type where it matters most: provider order (`matching.provider_order.<type>`), subtitle *languages* (per-type), per-type destination roots (`type_target_root`), Sonarr per-type. Question: which remaining **global** settings genuinely diverge by media type (movie / tv / anime) enough to warrant a per-type override?

**Initial take (being grounded by a multi-agent hunt):**
- **Strongest — Subtitles.** Anime (JP audio) nearly always needs subs + embedded extraction is the best source + fansub providers (SubDL/animetosho); English live-action often needs none or OpenSubtitles only. Per-type *languages* already exist → per-type *sources / enable / min-score* would genuinely help.
- **Moderate — NFO / artwork enable + fields.** Want Kodi NFO on movies but not anime, or different fields (anime: studio / original title; movie: collection / director).
- **Keep global (no real divergence):** rename concurrency / permissions / conflict policy / cleanup / auth / display. The naming *template* is already per-type.
- **Cost:** TMM affords blanket per-type via separate module UIs; Kira's single Settings area would balloon (the clutter we just reorganized away). If pursued, the right pattern is **global default + optional per-type override**, added ONLY where divergence is real — not blanket per-everything.

A grounded hunt (per-type analysis + adversarial regression review of the Tier 1–4 work + real-gap sweep) is evaluating this; results fold into §9.10.

### 9.10 Verified hunt v2 — outcome (2026-06-16)
A 16-agent grounded hunt (per-type-config + adversarial regression review of Tier 1–4 + real-gap sweep, refute-first verify) returned **2 confirmed, 10 refuted** — the healthy refute ratio §9.7 lacked. The verifier correctly killed the noise: per-type items as already-tracked-here-in-§9.9, `quality_fallback` as dropped-by-choice (§9.8 Batch 2), anime-crossref + `symlink_relative` + subtitle-source *visibility* as already-shipped.

**Confirmed (2):**
1. **Duplicate `_resolve_str_setting`** (`rename.py`) — a duplicate def introduced in the on_conflict pass shadowed the pre-existing anime-numbering one. Dead code, *not* a live bug (both behave identically for real inputs; `_resolve_permissions` never used it). **✅ FIXED** — removed the duplicate, kept the original; on_conflict + rename suites green.
2. **Subtitle auto-fetch doesn't stop on OpenSubtitles quota** (`rename.py` post-rename step 6) — **pre-existing**, already documented in §8. `asyncio.gather(return_exceptions=True)` swallows `QuotaExceeded`, so a large bulk rename keeps calling the exhausted API for every remaining file (backfill.py breaks on quota; auto-fetch doesn't). **Med, low blast-radius** (best-effort, bounded by `rename.concurrency`). Fix = mirror backfill's stop-on-quota (a shared flag checked before each `_fetch_one`). **Open — not built.**

**Per-media-type verdict (answers §9.9):** do **NOT** pursue TMM-style blanket per-type — Kira already does per-type where divergence is real (provider order, subtitle languages, destination roots, Sonarr). The **one high-confidence, low-risk win**: per-type **subtitle sources (enable/disable) + `min_score`** — both global today (`prefs.py:79,151`) while *languages* is already per-type, so the "global default + per-type override" shape is already proven by `languages_for()` (`prefs.py:53-57`) and extends mechanically. NFO/artwork per-type = defensible follow-up, not now (moderate value, doubles the Output UI). Everything else stays global.

**✅ Shipped — per-type subtitle prefs (the validated win).** `SubtitlePrefs.min_score_for()` + `sources_for()` mirror `languages_for()` (global default + per-type override); loader reads `subtitles.min_score.{mt}` / `subtitles.sources.{mt}`. `enabled_sources` was refactored into an availability-vs-toggle split (`_source_available`) so a per-type source list still respects API-key / install gating (a per-type list can't enable SubDL without its key). Wired at the `build_context` min_score choke point + all 5 `enabled`-source fetch sites via `ctx.media_type` (backfill ×2, rename auto-fetch, the 3 `/subtitles` handlers). UI: a unified **"Per-type overrides"** group in Settings → Subtitles → Advanced — Languages + Sources are **discoverable dropdown+chips pickers** (shared `PerTypeChips`; friendly labels, not free-text keys), Minimum score a number; each empty state reads "Same as global — pick to override", and the intro names the global controls it inherits (an early free-text draft was replaced after a UX review flagged it undiscoverable). Per-type languages was also upgraded from a text box to the same picker. 7 tests; backend **1037 green**, `tsc` + `vite build` clean. **Still open:** NFO/artwork per-type (deferred follow-up). *(The rename auto-fetch quota/auth-stop + the min_score gap were fixed in §9.11.)*

### 9.11 Verified hunt v3 — outcome (2026-06-16)
A 16-agent grounded hunt (regression review of the per-type subtitle work + per-type correctness + real-gap sweep, refute-first) returned **7 confirmed, 5 refuted** — and the synthesis correctly deduped: **4 of the 7 collapse to one `rename.py:1970-1983` block** (the post-rename subtitle auto-fetch diverging from backfill). Refuted noise correctly killed: dashboard coverage-by-design, series-cache-by-design, episode-count abstains, phantom-UNIQUE (already task #80), and the quota item re-flagged as already-tracked.

**✅ Fixed — rename auto-fetch parity with backfill (`rename.py`):**
- **1a (High — a NEW regression in the just-shipped per-type feature):** the auto-fetch SearchContext omitted `min_score`, so the per-type (and global) floor was silently ignored on the *automatic* path — an anime floor of 75 still saved a 30% sub. Added `min_score=prefs.min_score_for(...)`. The genuine miss: I wired `sources_for` there but not its sibling `min_score_for`.
- **1b/1c (Med — resolves the previously-open quota-stop + a new auth case):** a shared stop-flag now halts the batch on the first `QuotaExceeded` / `AuthRejected` (no more hammering a dead / 429 API) and surfaces ONE bell notification ("quota exhausted → backfill finishes it" / "key rejected → replace it"), mirroring backfill. Previously a 100-file rename on a bad key failed all 100 silently.

**✅ Fixed — UI honesty (#2, `SubtitlesCard`):** the per-type Sources picker now labels a key-gated source that lacks its key "· needs key" (matching the global Sources section), so you can't unknowingly "enable" SubDL-without-a-key for a type.

**Deferred (Med, marginal — noted, not built):** **#5** per-type sources lacks a persistent "none" (empty reverts to global, unlike min_score's empty-is-meaningful) — but "embedded-only" is already expressible by picking just `embedded`, so the gap is narrow; **#6** symlink idempotency ignores absolute-vs-relative target format, so flipping `symlink_relative` on doesn't rewrite *existing* absolute links (a migration concern, not a rename bug — the link still resolves correctly).

Verified: backend **1037 passed**, `tsc` + `vite build` clean.

**Sequencing rationale:** Tier 1 is correctness with zero schema risk → ship now. Tier 2 is the greenlit feature and is mostly UI because the backend already supports ordered lists. Tier 3 gives the new + existing matching settings a coherent home. Tier 4 fills that home. Tiers can be separate commits/PRs.

### 9.12 Per-episode NFO + filename enrichment (2026-06-16)
**Trigger:** a real One Piece rename produced `One Piece - S23E01 - Episode 01.nfo` containing only `<showtitle>/<season>/<episode>/<runtime>` + streamdetails — "barely info." Root cause: AniDB (the matched provider for One Piece, AID 69 → TVDB S23) carries **no per-episode titles** of its own, so `Match.episode_title` was empty → the NFO had no `<title>` and the filename `{t}` token fell back to the placeholder "Episode NN". The earlier lean-NFO choice (deliberately no `<plot>`/`<aired>`) was justified *only* because the sole plot on hand was the series blurb; with genuine per-episode data that justification is gone.

**✅ Shipped — resolve real per-episode metadata via the Fribb cross-ref (the same path the popup already uses):**
- **`series.resolve_episode_meta()`** — best-effort, cached per `(provider, id, season)` so a whole-series batch shares one provider fetch. AniDB → `_anidb_episodes_via_cross_ref` (Fribb AID→TVDB/TMDB, then that provider's episode list); direct TVDB/TMDB → `get_episodes`. Picks the season-relative episode, falls back to the absolute number (covers a not-yet-renamed file whose `parsed.episode` is still absolute). Returns the `EpisodeResult` (title / overview / air_date) or `None`.
- **NFO (`nfo.build_episode_nfo`)** now emits real `<plot>` (honors the field toggle) + `<aired>` (always when present). Wired into `_write_nfo_files` (post-move, best-effort — a miss leaves the lean NFO, never breaks the rename).
- **Filename (`rename.py` main loop)** resolves the title up front so BOTH the in-place root and `format_target_path` render `{t}` with the real title — "Episode 01" → "Egghead". Only fires when `episode_title` is empty (a TVDB-titled episode keeps its stored title, no extra fetch); no DB mutation (local var). The Settings *template* preview (synthetic samples) was intentionally left untouched.

Why episode `<uniqueid>` was NOT added: `EpisodeResult` carries no episode-level provider id (only the series id), and stamping the *show* id as the episode uniqueid would be wrong — title+plot+aired is the high-value payload media servers key on. 7 tests (`test_episode_nfo_meta.py`); backend **1044 passed, 3 skipped**.

**NOTE — the rich fields aren't missing, they're elsewhere.** The Settings NFO toggles (genres/cast/studio/country/status/artwork/originaltitle/collection) feed the **movie `.nfo`** and **series `tvshow.nfo`** — NOT the episode `.nfo`, which is lean by Kodi/Jellyfin schema (no per-episode genres/cast/studio). `build_tvshow_nfo` is rich and write-if-absent, so it defers to a media server's existing `tvshow.nfo` (this user's One Piece `tvshow.nfo` was Jellyfin-authored — `<lockdata>`, `/data/...` paths). The Settings copy was reworded to say so (an episode `.nfo` looking sparse is correct, not a bug).

### 9.13 Undo orphaned everything — Z:\ ↔ UNC alias gap + untracked auto-fetched subs (2026-06-16)
**Trigger:** undoing the One Piece rename moved the videos back but left every `-poster.jpg`, `.en.srt`, `.nfo` AND the empty `Season 23` folder behind. The DB confirmed `created_assets` are persisted **UNC**-spelled (`\\192.168.0.63\Data\…` — because `format_target_path` calls `.resolve()`) while `paths.library_root` is `Z:\`.

**Two root causes:**
1. **Alias-blind containment gate.** `path_under_roots` is purely lexical, so it judged every UNC asset "outside" the `Z:\` root → `_remove_recorded_assets` skipped them all and `_cleanup_undo_vacated_folders` found no containing root (`stop_at=None` → bailed). The NFO/poster *were* recorded correctly; the gate rejected them. (Same gap the scan prune fixed for itself — undo/rename were still on raw roots.)
2. **Auto-fetched subtitles were never tracked.** The post-rename auto-fetch wrote `.srt` via `fetch_subtitles` but recorded them in neither `created_assets` nor a sidecar `RenameHistory` row → undo had no way to know about them.

**✅ Fixed:**
- **`files._managed_roots_aliased()`** — resolves each managed root once (`Path("Z:\\").resolve()` → UNC, verified on host) and appends the differing spelling. Wired into undo's `_cleanup_entry_assets` + `_cleanup_undo_vacated_folders` and the rename forward-orphan sweep. With both spellings present, the lexical `path_under_roots` matches either.
- **Auto-fetched subs → `created_assets`.** The auto-fetch loop collects each `SubtitleFetchResult.path` and appends it to the rename's `RenameHistory` row (best-effort + `flag_modified`), so undo deletes the `.srt` exactly like the NFO/artwork. Asset cleanup empties the Season folder → the now-alias-aware folder sweep removes it.

4 tests (`test_undo_alias_cleanup.py`); backend **1046 passed, 3 skipped**, `tsc`+`vite build` clean. Corrects the earlier belief that "new renames store `Z:`" — they store the resolved UNC form, so EVERY containment check in the rename/undo path must be alias-aware.

### 9.14 CORRECTION — AniDB *does* have episode titles; the bug was its "Episode N" placeholder winning (2026-06-16)
**§9.12 above stated "AniDB carries no per-episode titles" — that is WRONG.** AniDB has them, and `AniDBProvider.get_episodes` parses them (anidb.py — en → x-jat → ja). The popup ALSO already uses AniDB-native first (cross-ref only as ban/error fallback). The real symptom (One Piece ep 1166 showing "Episode 1166" in the popup despite AniDB having the title):
- AniDB **auto-fills the ENGLISH title with the literal "Episode <absolute-number>"** for a not-yet-localized episode — a zero-info placeholder. For a freshly-aired episode the English title is just "Episode 1166" while the real romaji/Japanese title already exists.
- The old picker took `en` unconditionally → the placeholder beat the real romaji/native title. The tell: the displayed string uses the **absolute** number (1166), AniDB's style — TVDB's untitled placeholder would be the season-relative "Episode 11", and series.py:181 *drops* truly-untitled episodes, so it had to be AniDB's non-empty placeholder string.

**✅ Fixed:**
- **`anidb._select_episode_title(ep, num)`** (extracted + unit-tested) — prefers a REAL English title, but when `en` is exactly this episode's "Episode <num>" placeholder it falls through to romaji (x-jat) → native (ja); the placeholder/first title is the last resort so the episode is never left untitled (which would drop it from the popup). Older episodes with a real English title are unaffected.
- **`series.resolve_episode_meta` now AniDB-first** (was using ONLY the cross-ref — inconsistent with the popup), with a picker that matches on relative OR absolute number against either `episode`/`absolute_number` (AniDB numbers absolutely; TVDB/TMDB season-local). So the rename/NFO get the SAME title the popup shows.

5 title-picker tests added to `test_episode_nfo_meta.py`.

**…then the screenshot showed AniDB has the real ENGLISH title** ("Encountering Loki - Gunko of the Knights of God"). So the placeholder fix, while valid, was NOT the cause — the old code already preferred real English. Verified the ban file: `anidb-banned-until.txt` expired ~16 days ago and `anidb-last-call.txt` was 3 min ago → Kira can reach AniDB. But `anidb-episode-counts.json` (written by `get_episodes(69)`) was stamped **Jun 14** — One Piece's list hadn't been successfully fetched since *before* ep 1166 got its title.

**REAL root cause — frontend, the episode-list cache never revalidated.** Two compounding bugs froze the Jun-14 list forever:
1. `lib/episodes.ts` `fetchSeriesEpisodes` returned the localStorage copy on a hit (`return stored`) — the persisted entry has **no TTL**, so a list cached before a provider added a brand-new episode's title was served indefinitely.
2. `CoverPopup` effect bailed with `if (providerEpisodes || …) return` — since `getCachedEpisodes` already populated state from that stale cache, it **never even called** `fetchSeriesEpisodes`.

**✅ Fixed — real stale-while-revalidate:** `getCachedEpisodes` still paints instantly; `fetchSeriesEpisodes` now ALWAYS revalidates once per session, gated by a `revalidated` set (NOT Map presence, which `getCachedEpisodes` pollutes with the stale copy) and falling back to last-known only on empty/error; the popup effect always fetches (dropped the `providerEpisodes` guard + dep). So a page reload now repaints stale → fetches fresh → shows the real title; no manual cache-clear needed. `tsc`+`vite build` clean. (The backend placeholder + AniDB-first fixes stay as correctness wins for the adjacent "AniDB only has it in romaji" case.) **Lesson: a localStorage SWR cache with no TTL MUST actually revalidate — instant-paint that suppresses the refetch is a freshness bug, not an optimization.**

### 9.15 One Piece NFO — wrong episode + wrong season (2026-06-16)
**Trigger:** a re-rendered NFO read `<season>1</season> <episode>11</episode>`, title "Expose the Plot! …Captain Kuro" + `<aired>2000-01-26</aired>` — episode 11's REAL 2000 metadata — on a file that is the 2026 episode (absolute 1166). DB-verified the matches: `anidb:69, season_number=1, episode_number=1156…1166` (absolute), `parsed.episode` cour-rewritten to 1…11.

**Two bugs (one mine, one stale-data):**
1. **`resolve_episode_meta` matched the cour-LOCAL number against AniDB's ABSOLUTE list** — a regression in my own §9.14 AniDB-first change. `wanted={11, 1166}` hit AniDB's absolute episode 11 first → "Captain Kuro" (2000). Fixed: match the authoritative absolute `episode_number` (1166) against `e.episode` (AniDB) / `e.absolute_number` (TVDB) ONLY; the cour-local number is used solely as a season-guarded tuple fallback when there's no absolute. New test has BOTH episode 11 and 1166 present.
2. **`_write_nfo_files` copied the stale `Match.season_number=1`** instead of the cour-mapped season the filename uses. Verified the matches were scanned while AniDB was BANNED (`anidb-banned-until.txt` expired ~16 days ago), so `resolve_canonical_season` couldn't stamp S23 → `season_number` stuck at AniDB's degenerate 1. The rename's filename logic re-derives S23 live (`resolve_anidb_to_tvdb(69, 1156…1166)` → S23E01…E11, all verified present), but the NFO used the raw `season_number` → file said S23E11 while NFO said `<season>1`. Fixed: thread the rendered `season_override` into `_write_nfo_files` so `<season>` mirrors the filename exactly.

Net: after restart + re-rename, One Piece → `S23E11 - Encountering Loki…` with NFO season 23 / real title / 2026 aired, no re-scan needed (the cour mapping is live at rename). The stale `S01E1157` files were ban-era renders. Backend **1052 passed**. **Lesson: query the actual match/parse data BEFORE theorizing — two wrong diagnoses (no-titles, then placeholder) burned trust; one DB query settled it.**

### 9.16 "Fix them all" — multi-agent batch (2026-06-17)
User opted into multi-agent orchestration. Agents diagnosed + implemented file-disjoint fixes; I verified each + handled the sensitive `rename.py` work. All from live-usage reports this session. Backend **1072 passed, 3 skipped**; `tsc`+`vite build` clean.
- **Scan stuck/failing (critical, diagnosis agent reproduced vs a DB copy):** NOT data/NAS/lock — **SQLite write-lock contention**. The boot `_auto_heal_stale_matches` sweep holds the single writer while a scan's status commit hits `busy_timeout` (5s) → `OperationalError: database is locked` → poisons the worker session → the in-handler status write raises `PendingRollbackError` → escapes → bare "failed" (~5s duration == the timeout). Self-resolves once heal finishes. Fixed: inner `_scan_worker` handler rolls back + re-raises (never touches the poisoned session); `_scan_worker_locked` records `failed: <reason>` on its already-fresh session; `_reparse_worker` got the same guard; `busy_timeout` 5s→15s so the scan rides out heal's gap-separated commits. `test_scan_failure_status.py`.
- **Sonarr 302:** httpx dropped the `url_base` (`/nickflix`) on absolute request paths. Agent → relative paths across all 17 call sites + base_url trailing slash; `test_sonarr_url_base.py` (6).
- **Sonarr key "keeps changing":** masked secret was the input VALUE → editing left bullets → backend `_looks_like_mask` rejected the save. Agent → mask becomes a placeholder, value empty, bullets never sent.
- **Subtitles fetched despite embedded track:** `languages_needing_fetch` only checked sidecars; agent added embedded-track awareness (`ParsedFile.sub_langs` + existing `normalize_lang`).
- **Poster per episode:** episode artwork now writes ONCE to the series root (`poster.jpg`/`fanart.jpg`), not `<stem>-poster.jpg` per episode; excluded from per-episode `created_assets` (shared like `tvshow.nfo`, swept with the folder). Updated `test_artwork_download`.
- **NFO clarity:** per-field **M·S·E** legend (which of movie/series/episode `.nfo` each field lands in).
- **Background health checks:** new `integrations/health_monitor.py` (5-min poll of configured Sonarr/Jellyfin/Plex, started in the lifespan), `GET /integrations/health`, OK→broken Notification, frontend status dots; `test_health_monitor.py` (9).
- **Self-caught:** the `busy_timeout` bump broke `test_db_pragmas` (asserts 5000) — the health agent flagged it; updated to 15000.

**Not done by design:** artwork/NFO narration — those run inline in the rename (covered by its progress); only the post-rename subtitle hook was silent, fixed in §9.12-adjacent work. All changes uncommitted (user-initiated commits).

### 9.17 Undo/history hardening + subtitle reuse-cache (2026-06-17)
"Fix all of them, run agents to verify too." Agents built the separable pieces against fixed contracts (sub-cache `subtitles/subcache.py` + reuse-on-fetch; History UI: cleanup button / stale-undo greying / undone-row selection exclude); the data-loss-sensitive undo core (`history.py`/`operations.py`) was hand-written, then a review agent went over the whole diff.
- **cleanup-orphans:** `POST /history/cleanup-orphans`→`{removed}` deletes undone rows' `created_assets` (alias-aware, recoverable) — clears leftovers an old undo orphaned; UI "Clean undo leftovers" button.
- **Stale-undo guard:** `POST /history/verify-undoable` + an identity check (xattr stamp) in undo_entry/undo_bulk that refuses (409 / skip) a PRESENT-mismatched stamp; History greys the Undo button with a reason. (Plus the earlier occupied-old_path data-loss guard + bulk-undo alias fix.)
- **Trash-on-undo:** `_remove_recorded_assets` routes deletions to the recoverable trash (honors `rename.cleanup_trash`); subtitle sidecars route to the reuse-cache instead.
- **Subtitle reuse-cache:** undone `.srt` → `.kira-subcache/` keyed by OSDb content-hash + lang; `fetch_subtitles` reuses before downloading; retention `subtitles.cache_retention_days` (default 30); swept on scan-tail.
- **Ledger desync:** removed subs flip `subtitle_assets.active=False`. Already-undone rows are no longer selectable in History.

**Review agent caught (all fixed):** HIGH — the identity check false-refused LEGIT undos on an ABSENT stamp (pre-stamping renames / stamping-off / ext4-on-Docker) → now refuses ONLY a present-but-mismatched stamp (the physical move guards cover the data-loss case); LOW×3 — `.sub` mis-cached, forced/SDH subs cached under the wrong lang key, cleanup-orphans keyed the sub off the empty `new_path`. Regression tests in `test_undo_identity_check.py`. **Lesson: an over-strict safety check that blocks the legitimate action is as harmful as the bug it guards — the adversarial review pass is what surfaced it.**

Verified: backend **1098 passed, 3 skipped**; `tsc`+`vite build` clean. All uncommitted.

### 9.18 Tech tags folded into the scan popup as a 3rd line (2026-06-17)
"RESETED RESCANNED, the tech-tags scan isn't working — it did not start. When the feature is on, include it in the normal scan instead of a different popup; add a 3rd line in the scan popup for tech tags." Two real causes:
1. **Didn't run:** the scan-tail enrich (`_spawn_mediainfo_enrich`) only covered `all_new`, so a rescan with nothing new (or after a lighter reset) handed it an empty set → silent no-op.
2. **Couldn't be seen:** the tech-tag pass narrated through a *separate* `ActivityPill` (`mediainfo_enrich`) that is even SUPPRESSED while the scan popup is up — invisible during/after a scan, or a disconnected second popup.

- **Backend** (`scans.py`): new `_ids_missing_tech_tags(session, limit=20000)` (files with no `mi_stamp`, via `json_extract`); the scan-tail enrich now unions `all_new` + missing-tag files **when `read_mediainfo` is on**, so a plain rescan dependably backfills tech tags library-wide. Self-limiting (the pass stamps `mi_stamp` on every file it inspects → read at most once). `test_mediainfo_rescan_backfill.py` (2 tests).
- **Frontend:** `ScanProgress` gains a **3rd "Tech tags" line** (violet bar; `queued… → N/M reading… → done ✓`; header reads "Reading tech tags" + spinner while it runs, ✓ only when fully done). `TechProgress` type + `AppState.scanTech` in `types.ts`. The scan tracker (`runScan` AND `runReparse`) owns the popup: a module-level `narrateTechTail()` polls the enrich job in the tracker's `finally`, keeps the popup open until it finishes, and **dismisses the finished job** so the standalone pill doesn't ALSO fire a "Done" beat. The Settings-toggle enrich (no scan) never calls `narrateTechTail`, so its own pill is intact. "queued…" previews from scan start when the feature is on.

**Design calls:** the enrich stays DETACHED on the backend (the scan still completes immediately — never blocks on slow NAS reads, per [[project_deliberate_tradeoffs]]); the frontend just keeps the popup mounted to narrate the tail. **Tracker-owns-popup beat render-time derivations** — the imperative async tail (poll → set → dismiss) was far easier to verify than the 3–4 interacting React effects/latches needed to tell a scan-tail enrich from a Settings-toggle one in render (I built that version first, found its edge cases, and threw it out). **Note:** a *factory* reset wipes settings → `read_mediainfo` defaults OFF (a *database* reset keeps it); the feature must be ON for the line to appear.
Verified: backend **1100 passed, 3 skipped**; `tsc`+`vite build` clean. All uncommitted.

### 9.19 Settings: explicit Save/Cancel, replacing auto-save (2026-06-17)
User diagnosis: settings auto-saved on every change, so **browser autofill silently overwrote API keys/tokens** (the recurring "Sonarr key keeps changing") with no confirmation — and it generalizes to every credential field. Chosen fix (user picked "everything waits for Save"): the whole Settings page is now a **draft**; nothing persists until Save.
- **`SettingsPage.tsx` is the hub.** `rawSettings` is the editable draft; a `baseline` snapshot is the last-saved state. `dirtyKeys` = a stable deep-diff (`stableStringify`, sorts object keys) of draft vs baseline. **Every** writer became draft-only (`saveKey`, the NFO/artwork toggles, provider-order, anime cross-ref, custom-template editor) and the debounced top-level PUT effect became a **mirror-into-draft** effect (profile/op/auto-approve/thresholds live in their own state, so they're copied into the draft for the diff). **Save** PUTs only `dirtyKeys` then advances baseline + fires `kira:settings-saved`; **Cancel** restores draft (and the six mirrored controls) from baseline. A floating bottom-centre **"N unsaved changes · Cancel · Save"** bar shows whenever dirty.
- **Sub-sections:** inherit draft via `saveKey`. `PathsSection`'s three direct `api.putSettings` (ignore-patterns / watch-folders / watch.config) became `setRawSettings`-only (draft); its now-unused `pushToast` prop was dropped. `AdvancedSection`'s only PUT is the **Import** action (bulk + reload) — left immediate by design.
- **Navigation guard:** `SettingsPage` reports dirty up via `onDirtyChange`; `App.setActive` `confirm()`s before leaving Settings with unsaved edits (section switches stay mounted → never guarded). Plus a `beforeunload` warning. (Back/forward hash nav is unguarded — known small gap.)
- **Autofill hardening (complementary):** `ProviderField` text inputs now set `autoComplete="off"` for ALL credential fields, not just passwords (the Connections API-key `text` inputs had none — the autofill victims).

**`SIX_DEFAULTS` seed (subtle):** the six control-backed keys aren't persisted on a fresh DB, so baseline lacked them → the mirror-into-draft made them diff against `undefined` and the Save bar showed **phantom "6 unsaved changes" on load**. Fixed by seeding BOTH draft and baseline with their effective (loaded-or-default) values on hydrate (and in the backend-down `.catch`).
**Verification gap:** live preview is gated by the user's opt-in HTTP Basic auth (sign-in screen); I did not authenticate against their real backend, so this rests on `tsc`+`vite build` (both clean) + logic review, not a live capture. All uncommitted.

### 9.20 Episode NFO `<showtitle>` uses the unified show, not the cour title (2026-06-17)
User: AoT S2 episode NFO had `<showtitle>Attack on Titan Season 2</showtitle>` (the `<season>2</season>` was already correct). Cause: `_write_nfo_files` set `<showtitle>` (and the tvshow `<title>`) from `Match.series_name` — which for an AniDB cour match is the per-cour title ("Attack on Titan Season 2"). But the rename loop unifies the FOLDER to the franchise via `library_title` (earliest-cour title, [rename.py:1289](backend/kira/api/rename.py)), so the NFO disagreed with its own folder and Plex/Jellyfin would split the cour off as a separate show.
- Fix: `_write_nfo_files` gained `series_name_override`; the loop passes `library_title`, and `show_name = series_name_override or selected.series_name or title` feeds BOTH `build_episode_nfo(series_name=…)` and `build_tvshow_nfo(…)`. Invariant: **the NFO names the same show the folder does.** Holds for plain TV too (`library_title` = `selected.title` = bare show title; year is a separate template token, so no year leaks into `<showtitle>`).
- `test_episode_nfo_unified_showtitle.py` (2 tests): override → unified showtitle + tvshow title, "Season 2" absent; no override → documents the `series_name` fallback. (Offline — `registry_from_settings`/`resolve_episode_meta` stubbed.)
**Backend-only** → deploy = restart backend; existing wrong NFOs are rewritten on the next re-rename (the shared `tvshow.nfo` is write-if-absent, so an already-written one must be deleted to refresh). Verified: 47 NFO + 190 rename/anime-cour/artwork tests green (incl. the cour-unification E2E). Uncommitted.

### 9.21 Per-cour season posters (anime) (2026-06-17)
User: each anime cour has a different poster, ours doesn't. (NB: posters are FILES — Plex/Jellyfin/Kodi read `Season NN/poster.jpg` — not embedded in the `.nfo`.) Cause: for episodes, `_download_artwork_with_client` wrote artwork ONCE to the show root (`series_root_for(target)/poster.jpg`) **write-if-absent** — so across cours unified into one show, only whichever cour renamed FIRST set the single show poster; every other cour's distinct AniDB cover was never written anywhere.
- Fix: for **anime** episodes in **seasonal** layout (there's a real `Season NN` folder ⇒ `target.parent != _show_root`), also enqueue the cour's own poster — `provider_poster` = `Match.poster_url`, the cour-specific cover, NOT the fanart.tv show poster — as `Season NN/poster.jpg`, marked `_shared=True` (like the show poster + tvshow.nfo: excluded from the file's `created_assets`, reclaimed by the empty-folder sweep when the whole season is undone). Gated to anime (regular-TV seasons share one show poster → a per-season copy would just duplicate it) and skipped for absolute/flat numbering (no Season folder).
- `test_artwork_download.py` (+2): anime writes `Season 02/poster.jpg` + the show-root poster; regular TV writes only the show poster, no per-season copy.
- **NFO season `<thumb>`s (mass-audience completeness, same session):** the `Season NN/poster.jpg` FILE covers Plex/Jellyfin/Emby; Kodi reads season art from `tvshow.nfo`. So `build_tvshow_nfo` now also emits a `<thumb aspect="poster" type="season" season="N">URL</thumb>` per cour (`_season_thumb_lines`, gated under the `artwork` field). The set comes from a new `_anime_group_season_posters(session, group_id)` → `{season_number: poster_url}` across the franchise's selected AniDB cours (each Match already carries its ScudLee `season_number` + own `poster_url`), gathered in `_rename_one_file` beside the `library_title` unification and threaded `_write_nfo_files(season_posters=…)` → `build_tvshow_nfo`. Anime-franchise only; `tvshow.nfo` is write-if-absent so it's the snapshot at first-episode rename. `test_tvshow_nfo_season_posters.py` (3) + the two artwork-file tests.
- **Own NFO toggle** (Settings → Naming → Write .nfo files): the season `<thumb>`s are gated by a dedicated `seasonposters` field — added to `NFO_TOGGLEABLE` (default-on) and the frontend `NFO_FIELDS` (label "Season posters", series-target). Decoupled from the `artwork` field, so a user can keep show poster/fanart URLs out of the NFO while still shipping season covers (or vice-versa). The test proves both directions of independence.
**Not changed (possible follow-up):** the show-root `poster.jpg` is still first-cour-wins rather than guaranteed to be the earliest/franchise cour's poster (would need the earliest member's `poster_url`, which `_anime_group_members` doesn't return). **Backend-only** → restart backend; re-rename a season to write both its poster file and (delete the stale `tvshow.nfo` to refresh) its NFO `<thumb>`. Verified: NFO + artwork suites green.

### 9.22 Settings prerequisite-gating — disable controls whose requirements aren't met (2026-06-17)
"Disable settings that can't be turned on — there should be logic behind all settings (key not configured → grey it out). Run multiple agents." Six parallel read-only audit agents (one per section) cross-referenced each control against its BACKEND consumer to confirm each dependency is REAL (the setting truly no-ops without its prereq); then an adversarial review agent went over the diff.
- **Mechanism:** `ProviderField` gained `disabled` + `disabledReason` (forwarded to its inner Toggle `isDisabled` / Select `disabled` / Input `disabled`; dimmed label + amber reason line) — the audit found it didn't forward `disabled`, the blocker for in-place gating.
- **Gates added** (all frontend-expressible, backend-confirmed inert-without-prereq): **SubDL / SubSource** source toggles → disabled when `providers.<x>.api_key` unset (`search()` returns `[]`); **fanart-only artwork kinds** (clearlogo/clearart/banner/landscape/disc/characterart) → disabled when `!fanartKeySet` (poster/fanart fall back to the matched provider, stay enabled); **Runtime corroboration** boost → disabled when `parsing.read_mediainfo` off (metric abstains without the container duration); **AcoustID auto-fingerprint** → disabled when its key unset, plus a latent fix (no `value` binding → toggle always rendered ON; now bound to the saved bool).
- **Adversarial review caught + I fixed — OVER-gating:** I'd disabled **Relative symlinks** when `rename.default_op !== 'symlink'`, but the rename-preview modal has its OWN per-batch op picker, so a hardlink-default user who picks Symlink for one rename DOES get relative symlinks. Reverted to always-enabled (backend no-ops it under other ops; the desc already explains the condition). **Lesson: gating on the global default ignored the per-request override path — over-gating blocks a legitimately-effective control, the exact failure the user warned against.**
- **Deliberately NOT gated** (preferences / soft / per-title-runtime): provider-order reorder + anime cross-ref (soft-append fallbacks never strand a title); HI/Forced subtitle variants + episode-title boost (filename-derived, feed multiple paths); language selects + auto-fetch/backfill (work once configured; embedded's ffmpeg state isn't in `rawSettings`); `streamdetails` NFO (filename tokens still feed it). Already-correct gates confirmed: auto-approve threshold, cleanup master→sweep→trash, authoritative tech tags, NFO/artwork picker visibility, watch.config sub-settings.
- **Needs a backend signal (deferred):** `parsing.read_mediainfo` no-ops without the native MediaInfo lib, and `set_permissions` uid/gid are Unix-only (no `os.chown` on Windows) — neither is exposed to the frontend (candidate: `/system` `mediainfo_available` / `posix`). Until then those stay un-gated (the backend already posts a warning Notification when read_mediainfo is toggled with no lib).
- **Gates read the DRAFT** (`rawSettings`) — correct: typing a key live-enables the dependent toggle, and the backend no-ops a stale value regardless.
- **`backdrop.jpg` artifact-sweep gap (2026-06-17):** user renamed Loki (Move) and the source folder survived with a `backdrop.jpg` inside. Cause: `_ARTIFACT_FILENAMES` ([operations.py](backend/kira/renamer/operations.py)) had `fanart.jpg` + `background.jpg` but NOT `backdrop.jpg` — the Jellyfin/TMDB name for the same background art (Kira writes `fanart.jpg`, so a `backdrop.jpg` is always a foreign scraper's). Unrecognised → the folder never reached artifacts-only → `rmdir` refused → folder + file left behind. Added `backdrop.{jpg,png,jpeg}` to the set (+ test). NB: only affects FUTURE moves; the existing Loki leftover must be removed by hand (or re-run cleanup). The Jellyfin multi-backdrop `backdrops/` FOLDER was already swept.

- **Off-when-blocked (UX fix):** a prerequisite-blocked toggle now reads **OFF**, not its saved/default-on state. Surfaced by Clear logo — it's fanart-only AND `dflt: true`, so with no fanart.tv key it rendered "ON but greyed" (looks active, does nothing, can't change). Fixed uniformly: artwork kinds `isSelected={artworkKindOn(a.key) && !blocked}` (+ dimmed row); `ProviderField` toggle `isSelected={on && !disabled}` (AcoustID); SubDL/SubSource `isSelected={src && keySet}`. The saved value is untouched — when the key arrives the toggle returns to its real state.
Verified: `tsc` + `vite build` clean; adversarial review confirmed every remaining gate correct + no over-gating. Live preview blocked by the opt-in Basic-auth sign-in (not bypassed) → rests on type-check, build, multi-agent audit + review. Uncommitted.

### 9.23 Configurable folder cleanup — custom lists + aggressive "delete non-video" mode (2026-06-17)
"Let the user add custom filenames/extensions to delete, and an option to delete anything besides the video files." Three new `rename.*` settings, all sub-options of the artifact sweep (Settings → Folder cleanup), Move-only, honoring the existing Trash toggle:
- `rename.cleanup_extra_filenames` (list) + `rename.cleanup_extra_extensions` (list) → merged into `_is_artifact_file(name, extra_names, extra_exts)`, so the sweep also removes user-named files (e.g. `backdrop.jpg`, `.DS_Store`) / extensions (`.txt`, `.nzb`).
- `rename.cleanup_nonvideo` ∈ {`off`, `keep_subs`, `all`} (user wanted BOTH carve-outs): when a source folder has NO media left after a Move, delete the other files — `keep_subs` spares subtitle sidecars, `all` deletes everything non-media. New `_is_removable_file` (per-file delete decision) + `_folder_cleanable` (may-we-touch-this-folder gate, replaces `_is_artifacts_only` which is now a shim) + mode-aware `_cleanup_media_server_artifacts`, threaded settings → `execute_op` → `_cleanup_empty_source_parents`. Gated: forced `off` when the sweep is off; whole walk only runs under the master toggle.
- **Frontend:** Cleanup section gains two `CommaListField`s + a 3-way `SegmentedControl` (Off / Keep subtitles / Everything), dimmed under the sweep toggle, with a live warning that names the consequence + whether it's recoverable (Trash on/off). Arrays write the draft via `setRawSettings` (saveKey is scalar-typed).

**Adversarial data-loss review caught 2 real bugs (both fixed):** (1) HIGH — `_is_removable_file` checked the artifact/extras list BEFORE the media guard, so a user who put `.mkv`/`Movie.mkv` in the custom lists could **delete a video**; (2) MEDIUM — the protected class was video-only, so an un-renamed **audio track** in a music album folder would be nuked. Fix: a single `_is_media_file` (VIDEO|AUDIO via `scanner.MEDIA_EXTENSIONS`) that runs **FIRST** in `_is_removable_file` and is the block in `_folder_cleanable` — **a real media file is never deletable in any mode, regardless of the custom lists.** Regression tests pin both. **Lesson: on a destructive feature, order the invariant (never delete content) BEFORE the permissive user-config check — and protect the library's actual content type, which for music is audio, not just video.** Verified safe by the review: user-content dirs block in every mode, Trash routing honored for the broader set, empty/`.`-only extension can't match-all (`if ext and …` guard). `test_cleanup_custom_and_nonvideo.py` (13). **Residual (noted):** a symlinked DIR in a nuke-mode folder has its link (not target) removed — minor.
Verified: cleanup suites green (40); full suite re-run after the fix. Backend + frontend → restart backend + rebuild frontend. Uncommitted.

### 9.24 Rename made instant — post-rename network tail backgrounded (2026-06-18)
"Renamed nana, why is this so painfully slow?" A full-season rename blocked the `/rename` response for **minutes** — the per-episode subtitle auto-fetch ran inline inside the request (bounded to `rename.concurrency`, multiplied across the season). Fix: steps 1–5 (move → NFO → artwork → cleanup → xattr) stay awaited inline; the network tail (notify fan-out → subtitle auto-fetch → media-server refresh → Sonarr rescan) is extracted into a nested `_post_rename_hooks()` and scheduled via `tasks.spawn_tracked` **after** the per-file history is committed, so `/rename` returns the instant files are on disk. The hook opens its own `SessionLocal` and **re-loads** the renamed files there (the request session is closed by the time it runs → its instances are detached). New `tasks.drain_background_tasks()` lets tests/shutdown await in-flight hooks. Ordering invariant (§1.8) preserved — the task runs sequentially. `ActivityIndicator` now refreshes the files list when a `subtitles` job ends (not just `subtitle_backfill`), so the missing-sub chips flip once the backgrounded fetch lands. **Accepted tradeoff:** an undo fired in the tiny window before the hook records auto-fetched `.srt` can orphan one subtitle (recoverable via cleanup-orphans); video/NFO/artwork undo is unaffected (recorded inline). `test_post_rename_network_tail_is_backgrounded`. Full suite 1121 green.

### 9.25 Anime franchise split on a per-file filename year + rename loop hardened (2026-06-18)
"WTF did it do?" — one show landed in TWO folders, `Gachiakuta` AND `Gachiakuta (2025)`, and the batch 500'd partway. Diagnosis from the live DB: every file matched the SAME entry (`anidb:18686`, `year=None`, one group) — the matching was correct. **Two pre-existing bugs (NOT the §9.24 speed change):**
- **Year split.** Folder year = `selected.year if not None else parsed.year` ([rename.py:1361](backend/kira/api/rename.py)). The match year is None, so it fell back to each file's **filename-parsed** year — `2025` for `…2025.WEB-DL…` rips, `None` for `Erai-raws`/`REPACK` rips. The franchise unifier fixed the *title* but only unified the year `if members[0][2] is not None`, and the canonical member's year IS None. **Fix:** unify the year across the group **unconditionally** (`next((y for …members if y is not None), None)`) AND pin `parsed.year = library_year` — because `templates._build_ctx` resolves `{{y}}` as `library_year if not None else parsed.year`, so without pinning the per-file filename year still leaked through. Now every file in a franchise renders the SAME year (or none). `test_yearless_franchise_never_splits_on_per_file_filename_year`.
- **Partial batch / 500.** Only 4 of ~24 files renamed (rest stuck `approved`) → `perform_rename` aborted mid-loop on an unhandled exception in `_rename_one_file` → 500, while the activity pill's `finally` still flashed "done". **Fix:** wrap each file in the loop in try/except — roll back any half-applied per-file tx, record a per-file failure (so the user sees WHICH file + why), and continue. One file's surprise error can no longer abort the batch or blank-500 the response. (The specific original raiser is now contained + surfaced per-file; exact line awaited a traceback the console-only logs didn't capture.)
Verified: rename e2e green (23). **Recovery for the live split:** undo the 3 files already in `Gachiakuta (2025)/` (History → Undo), re-run — with the fix all land in `Gachiakuta/`. Backend-only → restart backend. Uncommitted.

### 9.26 In-place / same-folder junk sweep (2026-06-18)
"Useless files don't get deleted if the files are renamed in the same folder." The artifact sweep ([operations.py](backend/kira/renamer/operations.py) `_cleanup_empty_source_parents`) only walks UP from the source parent and only acts on a folder it can *remove* — so it bails the instant a folder still holds media. A file renamed **in place** (target folder == source folder), or any destination folder that keeps its media, therefore kept its leftover junk. New `sweep_destination_junk(folder, *, mode, extra_names, extra_exts, trash_root, protected)`: after the loop, each folder a file landed in is swept, honoring the **same** `cleanup_nonvideo` mode + custom lists, **without** removing the folder and **without** recursing into subdirs. Triple protection so it can never eat the library or Kira's own output:
1. **media** (video/audio) — `_is_removable_file` guards first;
2. **this batch's output** — `inplace_protected` (renamed videos + every NFO / artwork / co-renamed sidecar Kira wrote), accumulated in `_rename_one_file`, normalized at compare;
3. **Kira's artifact output NAMES** (poster.jpg, tvshow.nfo, `<stem>-thumb.jpg`, any `.nfo`, season art) — protected even when a PRIOR run wrote them and they're not in (2) — UNLESS the user explicitly listed that name/ext (their stated intent wins).
Gated by the same master + artifact toggles as the source walk; honors Trash; dry-run-safe; best-effort (never fails the rename). Net effect by mode in a media-holding folder: **off** → deletes only the user's custom list; **keep_subs** → strips non-video junk, keeps subtitles + Kira output; **all** → strips all non-video leftovers except subtitles-if-keep / Kira output. `test_cleanup_custom_and_nonvideo.py` (+6, 19 total). Backend-only → restart backend. Uncommitted.

### 9.27 Franchise shelf persists once formed (frontend) (2026-06-18)
"When I rename one of the last 2 [in a collection], the survivor merges back into single-season covers." [LibraryGrid.tsx](frontend/src/components/LibraryGrid.tsx) `renderSectionBody` only rendered a franchise SHELF at `members.length >= 2`; renaming all-but-one member dropped the survivor into the flat solo grid mid-session. Fix: a `everShelvedRef` (survives the post-rename re-render) remembers each group id that has had ≥2 members; the partition now shelves a group when `members.length >= 2 || shelfGroupIds.has(gid)`, so a collection stays a collection down to its last pending member, then disappears when it fully empties (the ref prunes a group with 0 members). Session-scoped — a hard reload with a half-done franchise re-collapses (a refresh-proof version would need the backend to report franchise totals incl. already-renamed members). Type-clean (`tsc`); ships via dev server now / production build once the unrelated subtitle-coverage WIP in `adapters.ts`/`data.ts`/`modals.tsx` compiles. Frontend-only. Uncommitted.

### 9.28 Settings/UI "angry-boss" audit — fixes (2026-06-18)
Four parallel read-only review agents (settings backend / settings UX / cross-app UI consistency / settings↔backend wiring) produced a findings list; I verified each before acting (one false positive dropped: the reviewer claimed `App.tsx` had a broken `#\settings\` hash — it's correct forward-slashes; and `matching.high/mid_threshold` are NOT dead — `App.tsx` feeds them to `setConfBands`, driving the confidence badges, so they were left intact). Shipped:

**Backend** (`test_settings_hardening.py`, +8): MediaInfo readers (`scans.py`) + the OFF→ON backfill comparison route through `settings_store.unwrap` — `bool({"value":False})` was True, so a wrapped OFF read as ON and could clobber correct tags / trigger a full re-read; `network.force_ipv4` live-apply (`settings.py`) now coerces the string-toggle shape, not just a literal bool, so the "no restart" promise holds; the GET /settings secret mask no longer exposes a 4-char fingerprint for `auth.password_hash` (`fingerprint=False`); `_resolve_rename_mode` (`rename.py`) clamps to the `{in-place, move-to-library}` enum (no garbage into the data-loss-historied mode selector); `integrations.py` `_resolve_setting` uses the canonical `unwrap`, and `_load_sonarr_config` validates the URL is `http(s)://host` before it becomes the base URL.

**Frontend** (type-clean; dev-server now / prod build once the WIP compiles): the four **dead** Music/AcoustID credential fields are hidden behind `MUSIC_PROVIDERS_ENABLED=false` (no backend reads them); the Cleanup **"Everything"** mode now `window.confirm`-arms when Trash is OFF (it was a permanent mass-delete guarded only by muted text); the Settings filter effect dropped `rawSettings`/`providers` from its deps (it re-walked the DOM on every keystroke anywhere); `Select` gained an `aria-label` prop, wired at every previously-unlabeled call site (Subtitles HI/Forced, Sonarr Type/Quality/Audio/Folder/Monitor, Advanced retention/on-conflict); the CoverPopup download bar is now a real `role="progressbar"` with live `aria-valuenow`.

**Deferred (deliberately):** `discard()` full-revert remount of `ProviderField`/`NamingTemplateTabs` (real edge-case bug — needs careful remount keying); the **🟣 design-system consolidation** (dual violet/emerald button systems, ~41 off-token hexes, 3 focus-ring colors, 2 radius scales) — appearance-changing, and can't be visually verified headless (no preview + the prod build is red from the subtitle-coverage WIP), so it needs a deliberate pass with the dev server open. Uncommitted.

**Round-2 review (4 more agents) + 2 self-audit regression fixes (2026-06-18).** A second fan-out (core flows / backend-correctness / resilience / adversarial self-audit) surfaced more — and the self-audit caught **two regressions in this session's own code**, both fixed + tested:
- **Shutdown didn't drain background tasks** — `main.py` lifespan closed `net.aclose_shared()` but never called `drain_background_tasks()` (the helper §9.24 added for exactly this), so a `docker stop` mid-tail killed the post-rename subtitle/Sonarr/refresh hooks with the HTTP client yanked. Fixed: bounded `await asyncio.wait_for(drain_background_tasks(), 10s)` before the client closes.
- **In-place sweep collateral-delete** — `sweep_destination_junk` (§9.26) had no "is this a neighbour's file" guard, so an aggressive `all`/`keep_subs` sweep would delete a *different, un-renamed* episode's `.srt`/`.nfo` sharing the folder. Fixed: compute the folder's media stems and treat any file a present-media stem prefixes as that content's sidecar — spared absolutely (loose junk matching no media stem still goes). `test_inplace_spares_unrenamed_neighbour_episode_sidecars`.
The remaining round-2 findings were **pre-existing** (not this session). The high-value + medium batch is now **fixed + tested**:
- **Preview-rename now routes through the serialization chain** — `App.handleApply` calls `renameFilesDirectly(ids, {profile, op})` (which gained an optional per-batch override) instead of a direct `api.rename`, killing the wrong-target/double-record race the chain exists to prevent.
- **FileDetails "Approve" now approves + renames** (App.tsx) — was the last path stranding files in approved-limbo.
- **MovieBody fake "Will rename to" removed** — it hardcoded `/media/library/Movies/… [1080p]` (wrong root/profile/op); the Rename-preview modal shows the real dry-run.
- **Dashboard "Organized" KPI** uses `historyCounts().all` (authoritative), not the capped `history.length`.
- **SubtitleHistory** toasts a load failure instead of showing a misleading "empty" state.
- **`system.reset_matches`** detaches `rename_history.match_id` before deleting (no FK-500 on legacy DBs); **`history_counts`** uses three `COUNT`s, not a full-table materialize; **`undo_bulk`** dedups + caps at 2000; **NFO writes** are atomic (temp + `os.replace`). Tests: `test_api_hardening_round2.py` (+2), plus the rename-e2e suite covers the NFO path.

**Then the rest:** all three now fixed —
- **CoverPopup bulk-approve double-path** — the "Approve all" button now SKIPS the per-file status PATCH storm (`handleApproveAll`/`applyFilePatch` → `onUpdateItem`) when it's going to rename; the rename flips status server-side + the parent refetches, so the PATCHes that raced the refetch are gone. Optimistic flip kept only for the no-rename fallback.
- **Dashboard "matched %"** — "matched" now means "has a real provider match" (`f.match?.provider && f.match?.providerId`), not `confidence >= high`, so a fully-matched-at-mid library no longer reads a scary-low %; the band split stays in `buckets`/`lowConf`.
- **`discard()` full revert** — a `discardNonce` (bumped in discard) keys the Connections providers wrapper + `NamingTemplateTabs`, remounting exactly the seed-once subtrees (API-key inputs / template editor) so Cancel reverts their local text instead of leaving stale edits to re-commit. (ProviderCard takes `status` as a prop → no network re-fire on remount.)

**Genuinely deferred (not blind-fixable):** the **🟣 unvirtualized 100k-file grid** (scale cliff) — a real project (windowing a grid with interspersed franchise shelves + `/files` pagination), and the **design-system consolidation** (dual violet/emerald buttons, ~41 off-token hexes, focus-ring/radius drift) — appearance-changing, can't be verified headless (no preview + prod build red from the subtitle-coverage WIP). Both want a dev-server-open session; not safe to do blind.

### 9.29 Anime show folder uses the franchise ROOT title, not the present cour (2026-06-18)
"Renamed Haikyu — why did it become `Haikyu!! 2nd Season/Season 2/Haikyu!! 2nd Season - S02E24`?" Live DB confirmed: the file matched `anidb:10981` ("Haikyu!! 2nd Season", S2) with group `anidb:10145` (root = "Haikyu!!", S1) — but S1 isn't in the library, so `_anime_group_members` returned only the S2 cour, and `library_title = members[0][1]` picked up AniDB's per-cour title *with* the "2nd Season" qualifier → redundant folder + filename. Fix ([rename.py](backend/kira/api/rename.py) franchise-unify block), TWO tiers:
1. Resolve the franchise **root** aid encoded in the group_id (`anidb:<root>`) via the **offline** AniDB title dump (`AniDBProvider._pick_display_title`) — authoritative, handles ordinal AND subtitle sequels ("To the Top", "Mushishi Zoku Shou").
2. When the dump ISN'T in memory — the **common restart → re-rename path runs no AniDB op, so nothing lazy-loads the 30 MB dump** (the v1 dump-only fix fell back to the cour title and STILL produced "Haikyu!! 2nd Season") — strip AniDB's trailing per-cour qualifier offline (`\d+(st|nd|rd|th) Season | Season \d+ | Part \d+`) so "Haikyu!! 2nd Season" → "Haikyu!!". Ordinal/Part forms only; a subtitle sequel needs tier 1 (any scan/match loads the dump).

Tests: `test_anime_show_folder_uses_franchise_root_not_present_cour` (strip path, dump empty — the real failure) + `test_anime_show_folder_resolves_root_title_from_loaded_dump` (dump path, subtitle sequel). **Recovery:** the already-renamed file sits in `Haikyu!! 2nd Season/` — restart backend, undo it (History → Undo), re-run → lands in `Haikyu!!/Season 02/Haikyu!! - S02E24…`. Backend-only. Uncommitted.
