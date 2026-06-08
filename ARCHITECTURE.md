# Kira — Architecture

> "Rename, organize, done."
> Self-hosted, Docker-native media metadata fixer.

This doc reflects what's **actually built** right now.

---

## Status snapshot

| Area | State | Notes |
|---|---|---|
| Scanner | ✅ working | Recursive walk, video + audio extensions, persists to SQLite |
| Parser | ✅ working | Format stripping, 6 SxE patterns, anime/movie/tv/music routing, 16+ tests |
| Matcher | ✅ working | Trigram similarity, weighted scoring, provider fallback ladder |
| TVDB provider | ✅ working | v4 login flow, English title/overview preference, search + episodes |
| TMDB provider | ✅ implemented | Code complete, only enabled if user adds an API key |
| AniDB | ✅ implemented | Offline title-dump search (keyless), episodes, relations + franchise offsets, per-cour routing, ban-aware rate limiting — the core anime path |
| MusicBrainz / AcoustID | 🚧 wired, inactive | Present in factory + registry, but the music match path is **cut** (see roadmap) — not used by the engine |
| Rename engine | ✅ working | Move / copy / hardlink / symlink, template-driven, undoable |
| Onboarding | ✅ working | 5-step flow, real TMDB key validation against backend |
| Review queue | ✅ working | Live data, approve/reject/manual-search/pick-candidate all persist |
| History | ✅ working | Real list, filters, undo, CSV export |
| Settings | ✅ working | Provider keys + **per-type provider preference**, paths (browse), naming (+ **anime absolute/seasonal** toggle), confidence, advanced |
| Notifications | ✅ working | Bell + popover, polls every 15s, derived from rename events |
| Kira Cloud | 🚧 hook only | Provider factory has `ProviderMode.CLOUD` branch; no actual cloud server |

---

## Tech stack

### Backend — Python 3.12+ / FastAPI

| Purpose | Library |
|---|---|
| Web framework | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 (async) |
| Database | SQLite via aiosqlite |
| Validation | Pydantic v2 + pydantic-settings |
| HTTP client | httpx |
| Templating | string `.replace()` (no Jinja yet — simple is fine here) |

Migrations: none yet. Schema is created via `Base.metadata.create_all()` on startup. Alembic is in `pyproject.toml` for when we need real migrations.

### Frontend — React 18 + TypeScript + Vite

| Purpose | Library |
|---|---|
| UI | React 18 |
| Build | Vite |
| Types | TypeScript (strict) |
| State | Plain `useState` / props drilling — no Redux/Zustand yet |
| Styling | Tailwind v4 + Untitled UI components (migrating from legacy hand-written CSS) |
| HTTP | native `fetch` via a small `api` client |

---

## Project layout

```
backend/
  pyproject.toml
  .env.example                         # KIRA_TMDB_API_KEY / KIRA_TVDB_API_KEY / KIRA_MEDIA_ROOT
  kira/
    __init__.py
    main.py                            # FastAPI app entry, router registration
    config.py                          # Pydantic Settings (env-driven)
    database.py                        # Async engine + session + create_all
    models.py                          # All SQLAlchemy models
    schemas.py                         # All Pydantic API schemas
    scanner.py                         # Directory walker
    parser/                            # Filename → ParsedFile
      __init__.py
      format_stripper.py               # Strip codec/quality tokens
      patterns.py                      # 6 SxE regex patterns
      parser.py                        # Orchestrator + music split
    matcher/                           # ParsedFile → ScoredMatch[]
      __init__.py
      similarity.py                    # Trigram Jaccard normalization
      engine.py                        # Provider routing, query ladder, scoring
    providers/                         # Pluggable metadata sources
      __init__.py
      base.py                          # MetadataProvider ABC, ProviderAuth, ProviderConfig, ProviderMode
      factory.py                       # build_provider() — single seam for Kira Cloud later
      tmdb.py                          # TMDB v3 implementation
      tvdb.py                          # TVDB v4 — lazy login, English-preferred titles
    renamer/                           # File ops
      __init__.py
      templates.py                     # Python port of formatPath() — naming profiles
      operations.py                    # FileOp enum + execute_op + undo_op
    api/                               # Route modules — one per resource
      __init__.py
      scans.py                         # POST /scans, GET /scans/[id]
      files.py                         # GET /files, PATCH /files/{id}, bulk-status
      matches.py                       # rematch, select existing, select manual
      search.py                        # GET /search/{provider}?q=...
      rename.py                        # POST /rename (the destructive endpoint)
      history.py                       # list/counts/undo/undo-bulk/export.csv/cleanup
      settings.py                      # GET/PUT /settings, POST /providers/{p}/test
      system.py                        # /folders, /database/reset, /notifications
  tests/
    test_parser.py                     # Table-driven parse tests

frontend/
  package.json
  vite.config.ts
  src/
    main.tsx                           # React root
    App.tsx                            # Page routing, action handlers, modal mgmt
    index.css                          # All CSS — single source of truth
    lib/
      types.ts                         # TypeScript domain types
      data.ts                          # Helpers: poster() initials, TYPE_COLOR (mock data removed)
      api.ts                           # Typed fetch client + ApiError class
      adapters.ts                      # ApiMediaFile → MediaFile (the only place shapes mix)
      icons.tsx                        # SVG icon library
    components/
      ui.tsx                           # Sidebar, Topbar, Poster, ConfidenceBadge, Modal, etc.
      modals.tsx                       # ManualSearch / RenamePreview / Shortcuts / FileDetails
      Onboarding.tsx                   # 5-step first-run flow
      LiveApiPanel.tsx                 # (now unused — kept around for debug, may remove)
      NotificationsBell.tsx            # Bell + popover
      FolderPickerModal.tsx            # Drive/folder browser modal
      settings-blocks.tsx              # ProviderBlock + ProviderField + NamingTemplateTabs
    pages/
      DashboardPage.tsx                # Stat tiles + scan banner
      ReviewPage.tsx                   # The core screen — filters, rows, bulk actions
      HistoryPage.tsx                  # Real-history list with undo
      SettingsPage.tsx                 # 5-section settings page

DESIGN.md                              # Visual / design-system spec (independent of this doc)
ARCHITECTURE.md                        # ← you are here
```

---

## Data flow

### Scan → Parse → Match → Review → Rename

```
1. SCAN     User clicks "Scan now" or hits POST /scans { root_path }
            scanner.walk() yields every video/audio file under root
            Each new path → MediaFile row (status=discovered)

2. PARSE    Same request, inline: kira.parser.parse(path) → ParsedFile dataclass
            • format_stripper drops codec/quality tokens, captures them
            • patterns.extract_sxe runs 6 cascading regexes
            • _classify routes to movie | tv | anime | music
            • Music takes a separate code path (artist/album/track parsing)
            parsed_data JSON stored on the MediaFile

3. MATCH    POST /rematch-all (or /files/{id}/rematch) → MatchEngine.match(parsed)
            • Routes by media_type via PROVIDER_PREFERENCE
            • Tries each configured provider until one returns results
            • Query ladder: full title → drop arc/subtitle → first word(s)
            • Scores each result: 0.55 trigram + 0.25 year + 0.20 rank
            • Top 5 stored as Match rows; rank-0 marked is_selected=True

4. REVIEW   Frontend fetches GET /files (eager-loads matches)
            ReviewPage renders the polished UI with real data
            Filters / search / sort are client-side
            Actions:
              ✓ approve → PATCH /files/{id} status=approved
              ✗ reject  → PATCH /files/{id} status=rejected
              📁 manual → opens ManualSearchModal which hits GET /search/{provider}
              🔁 candidate → POST /files/{id}/select/{matchId}
              💡 search override → POST /files/{id}/select-manual

5. RENAME   User opens RenamePreviewModal (Apply button), picks profile + op
            POST /rename { file_ids, profile, op }
            For each file:
              • renamer.format_target_path(parsed, library_root, profile)
              • renamer.execute_op(op, src, dst)
              • RenameHistory row written
              • MediaFile.status = 'renamed', file_path updated on move
              • Notification row written (success or error)
            Returns per-item ok/error
            Undo: POST /history/{id}/undo reverses the op via renamer.undo_op
```

### Filename parsing — what survives the pipeline

```
[ToonsHub] BLEACH Thousand-Year Blood War - S17E25 (JAP 2160p x264 AAC) [Multi-Subs].mkv
   │
   ├── format_stripper:
   │     leading [GROUP]      → release_group = "ToonsHub"
   │     extract source       → quality = "2160p"
   │     extract codec        → codec = "x264"
   │     extract audio        → audio = ["JAP", "AAC"]
   │     strip bracket noise  → "BLEACH Thousand-Year Blood War - S17E25"
   │
   ├── extract_sxe (pattern 1: SxxExx):
   │     season=17, episode=25, confidence=0.95
   │
   ├── _classify:
   │     [GROUP] tag + path hint "/anime/" → media_type = "anime"
   │
   └── _extract_title:
         cut at SxE position → title = "BLEACH Thousand-Year Blood War"

Result: ParsedFile(media_type="anime", title="BLEACH Thousand-Year Blood War",
                   season=17, episode=25, release_group="ToonsHub",
                   quality="2160p", codec="x264", confidence=0.74)
```

---

## API surface

All routes prefixed `/api/v1`.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness check |
| POST | `/scans` | Walk a folder, persist + parse every file |
| GET | `/scans` / `/scans/{id}` | List / show scans |
| GET | `/files` | List media files with eager-loaded matches |
| PATCH | `/files/{id}` | Update status (pending/approved/rejected) |
| POST | `/files/bulk-status` | Bulk status update |
| POST | `/files/{id}/rematch` | Re-run matcher for one file |
| POST | `/rematch-all` | Bulk rematch (filterable by media_type) |
| POST | `/files/{id}/select/{match_id}` | Mark an existing candidate as selected |
| POST | `/files/{id}/select-manual` | Create + select a new Match from a Manual Search pick |
| GET | `/search/{provider}` | Live provider search (TMDB/TVDB/AniDB/MusicBrainz) |
| POST | `/rename` | Execute file operations + write history |
| GET | `/history` | List with `?period=today\|week\|all&operation=...` |
| GET | `/history/counts` | Counts for the filter pills |
| POST | `/history/{id}/undo` | Reverse a single rename |
| POST | `/history/undo-bulk` | Reverse many |
| GET | `/history/export.csv` | Stream every entry as CSV |
| DELETE | `/history/cleanup?days=N` | Enforce retention |
| GET | `/settings` | Hydrate all settings on app boot |
| PUT | `/settings` | Bulk upsert |
| POST | `/settings/providers/{p}/test` | Real connection test, returns ok + latency |
| GET | `/folders?path=...` | List subdirectories — backs the picker modal |
| POST | `/database/reset?confirm=RESET` | Truncate all tables (destructive) |
| GET | `/notifications` | List recent notifications |
| POST | `/notifications/{id}/read` | Mark one read |
| POST | `/notifications/read-all` | Mark all read |

---

## Provider architecture

Three hooks ensure Kira Cloud (future hosted proxy) becomes a config flip, not a refactor:

### 1. Providers take `base_url` + `auth` in the constructor

```python
class TMDBProvider(MetadataProvider):
    def __init__(self, base_url: str, auth: ProviderAuth, client: httpx.AsyncClient):
        # No hardcoded URLs scattered through search_*/get_episodes
```

### 2. ProviderConfig stores `mode: direct | cloud`

```python
class ProviderConfig(BaseModel):
    mode: ProviderMode = ProviderMode.DIRECT
    api_key: str | None = None         # used in DIRECT mode
    cloud_token: str | None = None      # used in CLOUD mode
    cloud_base_url: str | None = None
```

### 3. `build_provider()` is the single seam

`providers/factory.py` reads the config, resolves `base_url` (real provider in DIRECT, Kira Cloud proxy in CLOUD), and constructs the right auth — so the provider class never knows whether it's hitting TMDB directly or our proxy.

When Kira Cloud ships, the desktop app doesn't change. Users flip a toggle and paste a cloud token.

---

## Frontend wiring

The Review page consumes the **MediaFile** type defined in `types.ts`. The backend returns **ApiMediaFile**. `lib/adapters.ts` is the **only** place these shapes mix.

```
GET /files → ApiMediaFile[] → adapters.apiToMediaFile → MediaFile[] → setState
```

All mutations follow the same pattern:
1. Optimistically… nothing. We wait for the backend response.
2. Call `api.xxx()` (returns the updated ApiMediaFile).
3. Run it through the adapter, swap into state.

This is why approve/reject persists across refreshes — the backend is the source of truth, the client just renders.

### Action handlers all live in App.tsx

`setFileStatus`, `setFileStatusBulk`, `pickCandidate`, `handleManualSelect`, `handleApply`. ReviewPage and FileDetailsModal receive them as props and call them. Same handlers wire to keyboard shortcuts (`a` / `r` / `⌘⇧A` / `⌘↵`).

### Why not Redux/Zustand?

App state is small enough that hoisting to `App.tsx` works. If we add more cross-cutting state (e.g. multi-pane drag-drop, real-time scan progress via WebSocket), reconsider.

---

## Data model (SQLite)

```sql
scans            (id, root_path, status, file_count, created_at, completed_at)
media_files      (id, scan_id, file_path UNIQUE, file_size, media_type, status,
                  parsed_data JSON, created_at, updated_at)
matches          (id, media_file_id, provider, provider_id, match_type, confidence,
                  title, year, series_name, season_number, episode_number,
                  episode_title, poster_url, overview, metadata JSON, is_selected)
rename_history   (id, media_file_id, match_id, old_path, new_path, operation,
                  template_used, media_type, title, poster_url,
                  created_at, undone_at)
notifications    (id, kind, title, body, read, created_at)
settings         (key PK, value JSON)
```

Status fields:
- `media_files.status`: discovered → pending → approved → rejected → renamed
- `matches.is_selected`: one per file marks the chosen candidate
- `rename_history.undone_at`: NULL = active rename, timestamp = undone

---

## Known limits / what's not built yet

| Area | What's missing |
|---|---|
| Anime matching | TVDB returns Japanese titles for some shows even with `language=eng`. Confidence scoring penalizes this. Fix: also score against `aliases` field |
| AniDB / MusicBrainz / AcoustID | Provider classes are stubs. Each needs `search_*` + auth flow |
| Real-time scan progress | Scan is synchronous; user sees a fake progress animation. Want: WebSocket streaming `scanProgress`, `scanMessage`, `scanFound` |
| arr-stack integration | No webhook receive, no Sonarr/Radarr post-processing |
| Watched folders auto-scan | Watch folder list saves but doesn't actually trigger scans |
| Settings → Naming → Custom templates | Tabs render but edits don't persist as a "Custom" profile yet |
| Subtitle co-renaming | `.srt` / `.sub` alongside `.mkv` aren't moved together |
| TMDB key flow | Onboarding saves+validates it, but Settings → Connections key input echoes a masked placeholder; first real change saves correctly |
| Auth / multi-user | Single-user app, no login |
| Docker image | No Dockerfile yet — local dev only |
| Migrations | Schema changes require deleting `kira.db` (acceptable while iterating) |

---

## How to run

```powershell
# Backend
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\python.exe -m uvicorn kira.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. Backend serves at <http://127.0.0.1:8000>; OpenAPI docs at `/docs`.

**TVDB key** lives in `backend/.env`:
```
KIRA_TVDB_API_KEY=...
KIRA_TMDB_API_KEY=...   # optional, enables TMDB provider
KIRA_MEDIA_ROOT=Z:\media
```
