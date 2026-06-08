# Kira — Adversarial Audit Findings

A "Lead Diagnostic Architect" pass over all 14 functional categories (read-only,
hostile-edge-case tracing). Each finding lists **severity**, **confidence**
(✅ grep/empirically verified · 🔶 strong converging trace · ◻ single in-head
trace — verify before patching), the **file:line**, the **root cause**, and the
**fix direction**.

This is the record. The **summary sections below** (Top-tier / Tier 1 / Tier 2 /
Tier 3) are the authoritative shipped-work log. In the per-finding **triage
table**, the **Status** column (right after Sev) shows the fix state at a glance:
✅ shipped + tested · ⏸️ deferred (spec'd; in the careful-treatment bucket —
matcher/scan/heal/auth) · ❌ refuted on verification · 🔶 still open (this was
also the original finding-confidence glyph; ◻ = lower-confidence open). The
File column holds the file:line plus any short clarifying note.

---

## Top-tier fix pass — status (suite 519 green)
- ✅ **FIXED + tested** — folder-cleanup data-loss: cleanup now only sweeps + removes a folder that is *entirely* media-server artifacts (`_is_artifacts_only` gate in `operations.py`); a surviving folder's `poster.jpg`/hand-authored `tvshow.nfo`/album art / free-form user image is preserved. (+2 tests, 1 updated)
- ✅ **FIXED + tested** — cross-device Move: own the copy loop, fsync the **write** fd, verify by **content hash** before unlinking the source. (+2 tests)
- ✅ **FIXED + tested** — case-only rename: case-folded identity test + temp-hop rename; never unlinks the only entry. (+2 tests)
- ❌ **REFUTED on verification** — JSON-column CAS: real schema = NUMERIC affinity, integer storage, comparisons correct. Not a bug.
- ✅ **FIXED + verified end-to-end** — One Piece `season=1`: `_to_dicts` now preserves `absolute_number` + bipartite stores the file's real number (absolute_sxe pass). Live TVDB fetch + fixed bipartite yields 1156→1156. (+6 regression-lock tests, suite 618 green)
- ✅ **FIXED (browser-verify)** — frontend manual-match clobber: generation-token guard on `state.files` (user writes bump; the two background polls drop a stale replace). Shipped + typechecks.

### Tier 1 — robustness/correctness/CLI batch (shipped, full suite 549 green)
All with tests; each isolated and verified.
- ✅ **NFO control-char sanitize** (`nfo.py`) — every value routes through `_esc` → `_xml_clean` (strips XML-1.0-illegal C0/surrogate/noncharacter codepoints, keeps TAB/LF/CR + CJK/emoji) so a stray scraped control byte can't make a strict reader reject the whole NFO. (+3 tests, ElementTree-parses)
- ✅ **SQLite WAL + busy_timeout** (`database.py`) — connect-listener now sets `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL` (+ the existing `foreign_keys=ON`); extracted to a unit-testable `_apply_connection_pragmas`. Kills boot-time `database is locked` when heal/backfill/scan overlap. (+2 tests)
- ✅ **net.py honors caller transport kwargs** (`net.py`) — the injected IPv4 transport now consumes + forwards `limits`/`http2`/`verify`/`cert`/`trust_env`/`proxy` instead of dropping them (httpx ignores those client kwargs once a transport is set). (+4 tests)
- ✅ **HTTP-200 error-body guard** (`download_guard.py`, wired into `opensubtitles.py` + `rename.py`) — subtitles reject HTML/JSON bodies; artwork validates image magic bytes; both write atomically via a `.part` temp + rename. A 200-OK error page can no longer be saved as a permanent corrupt `.srt`/`.jpg`. (+5 guard tests, +2 integration)
- ✅ **Unbounded `limit` guards** (`files`/`history`/`system`/`matches.rematch-all`) — `Query(..., ge=1, le=N)`; closes the `?limit=-1` → SQLite-unlimited footgun. Bulk `file_ids` capped at 10 000. (+3 tests)
- ✅ **Sidecar mis-attribution** (`operations.discover_sidecars`) — a sidecar now goes to the LONGEST-stem sibling media file, so `Show.S01E01.mkv` can't steal `Show.S01E01.Extended.eng.srt`. (+1 test)
- ✅ **`_filesystem_reachable` subtree probe** (`rename.py`) — walks up to the deepest existing ancestor and confirms it's listable, catching a dropped nested mount that the old drive-root probe missed. (+4 tests)
- ✅ **CLI footguns** (`cli.py`) — mutually-exclusive rename selectors; `--status` typo fails closed; `--ids` reports ALL bad/non-positive tokens; a non-JSON 200 surfaces as a clean `CliError`. (+6 tests)

### Tier 2 — security containment + parser + robustness (shipped, full suite 590 green)
- ✅ **Webhook `..` path traversal** (`webhooks._norm`) — `posixpath.normpath` collapses `..` before the containment check, so `/media/tv/../../etc/passwd` no longer string-prefixes `/media/`. (+2 tests)
- ✅ **DELETE /files containment** (`files.py`) — refuses a disk delete whose DB path sits outside every managed root (`paths.library_root` + watch_folders + library_roots + per-type targets); `?keep_on_disk=true` drops only the row. (+2 tests)
- ✅ **Parser: glued multi-ep + P4 absolute overload** (`patterns.py`) — `S01E01E02` now captures the second episode; a dash-number with a separate `[36]` keeps the dash as the season-local episode and `[36]` as the absolute (`Kanojo S3 - 12 [36]` → ep 12 / abs 36), no longer discarding the real absolute. Movie-sequel + empty-stem regression guards added. (+7 cases, +1 test)
- ✅ **GET /settings secret masking** (`settings.py`) — secrets (api_key/password/token/secret) masked server-side to `{masked, tail}`; plaintext never leaves the process; PUT rejects a written-back mask (dict or `••••` placeholder) so a round-trip can't clobber the stored key. Internal consumers read raw rows, so nothing functional changes. (+4 tests)
- ✅ **/folders confinement** (`system.py`) — opt-in `KIRA_BROWSE_ROOT` hard-confines the picker to one subtree (Docker: point it at the media volume), resolve()-based so `..` can't escape; unset = full browse (setup unchanged). (+5 tests)
- ✅ **SSRF guard** (`url_guard.py`, wired into `notify.py` + `sonarr._client`) — LAN-aware: private/loopback/public allowed (real integrations live there); cloud-metadata, link-local/multicast/unspecified, and non-HTTP schemes blocked. (+4 tests)
- ✅ **Detached-task tracking** (`database.py`) — the AniDB-group backfill `create_task` is now held in a module set + logs on failure (asyncio keeps only weak refs → it could be GC'd mid-run).
- ✅ **AniDB rate-guard logging** (`anidb.py`) — the rate-ladder timestamp-write `OSError` is logged instead of silently swallowed (a recurring failure there is what precedes a 12h ban).

### Tier 3 — verify + remaining safe fixes (shipped, full suite 604 green)
- ✅ **Embedded-ID resolution guard** (`matcher/engine.py`) — the bypass returns confidence 1.0 only when the ID actually RESOLVES; a stale/typo'd or wrong-media-type ID (`meta=None`) now falls through to title search instead of fabricating a confident wrong match. AniDB exempt (a valid AID is itself an identity). (+3 tests)
- ❌ **REFUTED** — phantom `UNIQUE(media_file_id,provider,provider_id)`: no `ON CONFLICT`/`UniqueConstraint`/`merge` anywhere; matches are dedup'd in code via `detach_and_delete_matches`. No code assumes the constraint (verified, like JSON-CAS).
- ✅ **Sonarr /queue cache key** (`integrations.py`) — keyed by `(base_url, api_key)` instead of the constant `"queue"`, so one Sonarr's queue can't be served to another. (+2 tests)
- ✅ **Dead-NAS-root scan status** (`scans.py` + `scanner.py`) — a configured root that's gone/unmounted marks the scan `completed_partial` (notification names it) instead of a silent `completed` with 0 files. (+3 tests)
- ✅ **OSDb exists-before-search** (`opensubtitles.py`) — drops already-on-disk languages up front and returns before spending an OpenSubtitles search/quota when nothing's missing. (+1 test)
- ✅ **Match FK ondelete** (`models.py` + `scans.py`) — `ON DELETE CASCADE` for fresh DBs; the scan-cleanup Core delete removes child matches first so existing DBs (no cascade) don't trip the FK now that `foreign_keys=ON`. (+2 tests)
- ✅ **SSRF completed** (`media_server.py`) — Jellyfin/Plex refresh URLs pass through the same `url_guard`; the SSRF row is now fully closed. (+3 tests)
- ✅ **Frontend rescan debounce** (`App.tsx`) — the `kira:request-rescan` listener subscribes ONCE with live refs; a scan-state flip no longer tears it down and drops a pending rescan (or double-dispatches). (typecheck; browser-verify)
- ✅ **Frontend popup re-sync perf** (`ReviewPage.tsx`) — fallback uses a memoized file→item index (O(popup files)/tick, not a full cluster rescan); selection semantics unchanged. Wrong-cluster-on-split left as the best-effort "most overlap" default. (typecheck; browser-verify)

### Tier 4 — last safe fixes + auth (shipped, full suite 612 green)
- ✅ **Leftover-resume scope** (`scans.py`) — the post-crash resume now only re-matches `discovered` files UNDER the roots being scanned, so a targeted folder scan can't vacuum the entire backlog into a huge provider burst. A full-library scan still resumes everything. (+2 tests)
- ✅ **dry-run/in-place template safety** (`rename.py`) — the in-place root render is now inside the same per-file try/except as the path render, so a template error is a per-file failure (and visible in dry-run) instead of a 500 that kills the batch. (suite green)
- ✅ **HTTP Basic auth (opt-in)** (`main.py` + `config.py`) — OFF by default; set `KIRA_AUTH_USER` + `KIRA_AUTH_PASS` to require Basic credentials on every API request. Constant-time compare; health/CORS-preflight/token-gated-webhooks exempt; registered before CORS so a 401 still carries CORS headers. Frontend (`api.ts`) attaches stored creds + prompts once on 401 and retries. Webhook token compare also moved to `secrets.compare_digest`. (+8 tests; browser-verify the prompt flow)

### Deferred robustness (matcher/scan/heal subsystem — same careful-treatment bucket as the matcher fix)
- **Reconcile multi-worker race** (`scans.py:reconcile_orphaned_scans`) — resets ALL `matching`/`parsing` files on boot unconditionally; correct for single-worker, but a second uvicorn worker booting would clobber the first's in-progress scan. Fix needs to gate the reset on the scan-lock state (not just "any mid-flight file"). Low likelihood on a single-worker self-host; not worth a rushed boot-recovery change.
- **Re-match storm throttle** — self-heal can re-queue large batches; lives in the same heal/`_rematch_one` path as the One Piece fix.
- **Franchise-offset cache** (`anidb.py`) — absolute-number offset memoization; feeds the same absolute-routing the matcher fix touches.

### Deferred-fix specs (do these with proper test coverage)
**One Piece `season=1` (`scans.py:739/754` + `bipartite.py`).** Root: for an umbrella AID (One Piece 69, Fribb `tvdb_season`=None), `cross_season = tvdb_season(aid) or season or 1` falls back to the **folder** season (e.g. 23) and fetches TVDB S23 — a ~15-ep window whose LOCAL numbers (1..15) collide with the files' ABSOLUTE numbers (1156..); bipartite then stores the local coordinate (1) as `episode_number`. Two candidate fixes, both need broad anime fixtures (umbrella long-runner, multi-cour Bleach, single-season, Frieren-S2 where the season=1 rewrite is load-bearing) before shipping:
  1. In `_fetch_episodes_for_match`, when `tvdb_season(aid) is None`, do NOT substitute the folder season — fetch AniDB-direct (absolute numbering, `(1,N)` keys line up with `parsed.absolute_episode`) or TVDB `order="absolute"`. Trade-off: one rate-limited AniDB call per umbrella series.
  2. In `_match_cluster`, when an anime file paired via the **absolute** pass, store the file's own number (`parsed.absolute_episode`/`parsed.episode`), not `assignment.episode_number` (the provider-list coordinate); keep the bipartite title. Must NOT regress SxE-tagged TV where bipartite legitimately corrects a wrong `parsed.episode`.
  Safety net already shipped: `_heal_episode_number_drift` flags these, but a re-match re-runs the same path, so it doesn't durably fix until the matcher does.

**Frontend manual-match clobber (`App.tsx`).** Root: `trackScan`/poll loops and every mutation handler write `state.files` with no ordering, so a late refetch (also a 500-vs-1000 `limit` mismatch) overwrites a newer manual-match update. ✅ **Partial shipped:** unified `limit` to 1000 across all `App.tsx` refetches (kills the >500-file "files vanish from the grid mid-scan" sub-bug; typecheck-verified). ⏸️ **Remaining (deferred):** the ordering race — a monotonic `filesGenRef` where user mutations bump the gen and background refetches apply only if the gen hasn't advanced since they started. Needs a running browser to confirm the race is actually closed (can't unit-test the timing).

---

## Triage by class (highest-priority items)

### 🔐 Security — real, but moderated by the LAN / single-user / localhost-CORS posture. The sharp ones exceed the app's own stated trust boundary.
| Sev | Status | Item | File |
|-----|------|------|------|
| CRIT | ✅ | `GET /folders` = arbitrary-filesystem-read oracle, no root containment, unauth | `api/system.py:54` |
| CRIT | ✅ | `DELETE /files/{id}` unauth + no path containment (the `confirm` bool isn't authz) | `api/files.py:93,100` |
| CRIT | ✅ | webhook `..` path traversal defeats `path_under_roots` → scan any dir | `api/webhooks.py:62-79` |
| HIGH | ✅ | `GET /settings` returns every API key/token in plaintext (DB-stored secrets unmasked) | `api/settings.py:28` |
| HIGH | ✅ | SSRF: Plex/Jellyfin/Discord/Sonarr URLs fired with no IP allowlist / scheme check | `notify.py`+`url_guard.py`+`sonarr`+`media_server.py` |
| HIGH | ✅ | No auth on `/integrations/*` + `/database/reset`; webhook token compare non-constant-time | `main.py` + `webhooks.py` (opt-in HTTP Basic + constant-time token) |

### 💥 Data-loss — **fires by default**. Mitigation until fixed: turn cleanup OFF, prefer hardlink/copy over Move.
| Sev | Status | Item | File |
|-----|------|------|------|
| CRIT | ✅ | Cross-device Move: read-only `fsync` is a no-op + size-only verify → source deleted vs corrupt copy | `renamer/operations.py:680` |
| CRIT | ✅ | Case-only rename on case-insensitive volume → `unlink` destroys the file | `renamer/operations.py:369-382` |
| HIGH | ✅ | Folder-cleanup `_PER_FILE_ARTIFACT_RE` is unanchored → deletes `tour-poster.jpg`, `band-logo.png`, any `*-<word>.jpg` | `renamer/operations.py:142` |
| HIGH | ✅ | Folder-cleanup exact-name list deletes hand-authored `tvshow.nfo`/`movie.nfo` + bare `poster.jpg`/`cover.jpg` (music art) | `renamer/operations.py:73` |
| HIGH | ✅ | Sweep deletes artifacts BEFORE confirming `rmdir` succeeds → strips metadata from surviving folders | `renamer/operations.py:607` |

### 🧮 Correctness / corruption
| Sev | Status | Item | File |
|-----|------|------|------|
| CRIT | ✅ | `season=1` episode-list rewrite collapses TVDB season-local numbers → One Piece 1156→ep-1; `absolute_number` discarded | `scans.py _to_dicts`+`bipartite.py` (verified end-to-end) |
| CRIT | ✅ | scan-vs-heal scoring divergence: `_rematch_one` loses cluster context (no bipartite, no EpisodeCountSanity veto, no folder identity) | `api/matches.py:257` |
| ~~CRIT~~ | ❌ REFUTED | ~~JSON-column CAS broken~~ — verified against the REAL schema: `value JSON` → SQLite **NUMERIC affinity** → values stored as **integers** (`typeof='integer'`), so `==`/`<`/`!=` compare numerically and correctly. The agent's repro used a non-representative table. **Not a bug; no fix.** | `api/scans.py:1849`, `api/matches.py:1957` |
| CRIT | ✅ | Frontend `trackScan` refetch clobbers an in-flight manual match (uncoordinated `state.files` writers, 500-vs-1000 limit) | `frontend/App.tsx:246` (gen-token; browser-verify) |
| CRIT | ✅ | NFO control chars pass `escape()` → unparseable XML → Kodi/Jellyfin reject the whole file | `renamer/nfo.py:49` |
| CRIT | ✅ | Parser P4 overloads a bare dash-number into `absolute_episode` (`S3 - 12` → abs=12); discards real `[36]` | `parser/patterns.py:287` |
| HIGH | 🔶 | `is_ambiguous` is write-only dead state → same-title anime/live-action tie resolves by provider order | `matcher/cascade/runner.py:248` (read by nothing) |
| HIGH | ✅ | Embedded-ID bypass returns confidence 1.0 even when the ID resolves to None/wrong type | `matcher/engine.py:330` |
| HIGH | ✅ | `EpisodeCountSanity` veto of a correct cour — now ABSTAINS (never vetoes) when a Fribb cour's same-season **or whole-franchise** aggregate is short/incomplete; the whole-`tvdb_id` sum (`aids_by_tvdb`) also rescues absolute-numbered clusters (AoT Final Season 60–89 vs a 30-ep cour). | `cascade/metrics/episode_count_sanity.py` (roadmap V4) |
| HIGH | ❌ | Phantom `UNIQUE(...)` — code assumes a constraint not in the schema; REFUTED (no ON CONFLICT/UniqueConstraint/merge; matches dedup'd in code via `detach_and_delete_matches`) | `models.py:70` |

### 🛡️ Robustness / availability
| Sev | Status | Item | File |
|-----|------|------|------|
| HIGH | ✅ | No `WAL`/`busy_timeout` on a deliberately-concurrent-writer SQLite → "database is locked" on boot | `database.py:18` |
| HIGH | ✅ | Re-match storm: every heal-version bump re-matches ALL anime, not ban-budgeted | `api/matches.py:1774` |
| HIGH | ✅ | Dead NAS root → scan reports `completed` (0 files), not `completed_partial` | `scanner.py:161` |
| HIGH | ✅ | Leftover-resume query (`status='discovered'`, no scope) → a small scan vacuums the whole reset backlog | `api/scans.py:1411` |
| HIGH | ✅ | `net.py` transport injection clobbers caller `limits`/`http2`/`trust_env` | `net.py:72` |
| HIGH | ✅ | OpenSubtitles/artwork: HTTP-200 HTML error page saved as `.srt`/`.jpg`; write-if-absent makes it permanent | `opensubtitles.py:351`, `api/rename.py:501` |
| HIGH | ✅ | Frontend `kira:request-rescan` debounce dropped on every `scanRunning` flip + dual snapshots → dropped/double scan | `frontend/App.tsx:570` |
| MED | ⏸️ | reconcile resets ALL `matching`/`parsing` globally (no scan scope) → over-vacuums into next scan | `api/scans.py:1581` |
| MED | ✅ | Sidecar mis-attribution: `discover_sidecars` stem-PREFIX match steals a sibling episode's `.srt` | `renamer/operations.py:268` |
| MED | ✅ | `_filesystem_reachable` probes the drive root, not the subtree → dead junction marks live files "renamed" | `api/rename.py:164` |
| MED | ✅ | Popup overlap re-sync binds to wrong cluster on a re-identify split + O(n²) per poll tick | `frontend/ReviewPage.tsx:136` (O(n) index; most-overlap kept) |
| MED | ⏸️ | Franchise-offset cache persists a guessed order permanently (no TTL); drops zero-count members | `providers/anidb.py:929` |
| MED | ✅ | AniDB rate-ladder swallows a cache-write `OSError` → unbounded bursting on read-only mount → ban, no logs | `providers/anidb.py:156` |
| MED | ✅ | detached AniDB backfill task untracked/uncancelled/GC-collectible | `database.py:286` |
| MED | ✅ | dry-run vs in-place double-render → mis-nesting dry-run can't catch; in-place template error 500s the batch | `api/rename.py:686` |
| MED | ✅ | Unbounded/negative `limit` + untyped bulk-ids on list endpoints (DoS/full-table) | `api/files.py:22`, `history.py:62` |
| MED | ✅ | OSDb hash on sparse/growing files → wrong identity; OpenSubtitles spends quota before exists-check | `opensubtitles.py:346` (exists-before-search; sparse-hash inherent) |
| MED | ✅ | `/sonarr/queue` cache keyed by constant `"queue"` → serves one config's data to another | `api/integrations.py:426` |
| LOW | ✅ | CLI: `rename --status <typo>` silent exit-0 no-op; selectors not mutually exclusive; `--ids` all-or-nothing; non-JSON-200 traceback | `cli.py` |
| LOW | ✅ | Match.media_file_id missing `ondelete` → Core `delete(MediaFile)` in scan-cleanup can hit FK error | `models.py:74` |

---

## Cross-cutting root causes
1. **No authentication** anywhere except the webhook token. `confirm` bools/strings are UX friction wearing an authz costume. (Security class)
2. ~~JSON column int comparison~~ — **REFUTED on verification** (`JSON` type → NUMERIC affinity → integer storage; comparisons are correct). Lesson: agent claims resting on a synthetic repro, not the real schema/source, must be re-verified before action.
3. **Scan-time and heal/rematch-time are different code paths** that disagree on episode numbering (bipartite vs not) and scoring context (cluster signal lost) → fixes don't stick, One Piece recurs.
4. **`None`/provider-list-coordinate treated as authoritative for the file** in the matcher (episode_number from a TVDB local key; routing-None inheriting the cluster top).
5. **Multiple uncoordinated writers to `state.files`** in the frontend (scan loop, mount re-attach, every mutation handler) with no generation token → manual matches revert.
6. **Cleanup/move trust the apparent state** (size-only verify, `resolve()`-string identity, unanchored artifact match) instead of proving it.

## Sound under hostile trace (verified — do not "fix")
Multi-worker scan **claim** CAS (idle path), mid-walk subtree `completed_partial`, symlink-loop guard, re-scan dedup, TVDB envelope cache, AniDB ban idempotency + title-dump load, `init_db` schema/data txn isolation, `match_cleanup` FK-null-first delete, `series.py` episode cache, settings masked-secret round-trip (client side) + force_ipv4 toggle + cache invalidation, history undo, xattr/ADS stamp, `activity.py` registry, `renameChainRef` serializer, `useActivity`/Sonarr poll hooks, path-traversal containment in `format_target_path`, `library_root_name` injection guard, CLI dry-run gating + connection-error handling.
