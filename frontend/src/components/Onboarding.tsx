import { useState, useEffect, type CSSProperties, type ReactNode } from 'react';
import type { ContentTypes } from '../lib/types';
import { api } from '../lib/api';
import {
  IcLogoMark, IcScan, IcSparkles, IcShieldCheck, IcLink,
  IcCheck, IcX, IcAlertTri, IcSpin, IcFolder, IcKey, IcArrowRight,
  IcFilm, IcTv, IcAnime, IcMusic, IcTag, IcUndo,
} from '../lib/icons';
import { FolderPickerModal } from './FolderPickerModal';
import { useScrollLock } from './LoginGate';
import { FfmpegStatusRow } from './FfmpegStatus';

const ONBOARDING_KEY = 'kira.onboarded.v1';

export function isOnboarded(): boolean {
  try { return localStorage.getItem(ONBOARDING_KEY) === 'true'; } catch { return false; }
}
export function setOnboarded(v: boolean) {
  try { localStorage.setItem(ONBOARDING_KEY, v ? 'true' : 'false'); } catch { /* noop */ }
}

type ValidationState =
  | { state: 'idle' }
  | { state: 'checking' }
  | { state: 'success'; latencyMs?: number | null; existing?: boolean }
  | { state: 'error'; error: string };

/** Validate a provider key by saving it then hitting the test endpoint.
 *  Only called once the key LOOKS complete (length gate at the call site),
 *  so we never persist half-typed garbage over a working key. */
async function validateProviderKey(
  provider: 'tmdb' | 'tvdb', settingKey: string, key: string,
): Promise<{ ok: true; latencyMs: number | null } | { ok: false; error: string }> {
  try {
    await api.putSettings({ [settingKey]: key });
    const res = await api.testProvider(provider);
    if (res.ok) return { ok: true, latencyMs: res.latency_ms ?? null };
    return { ok: false, error: res.detail || `${provider.toUpperCase()} rejected the key.` };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

const idx = (i: number): CSSProperties => ({ ['--i' as never]: i });

interface OnboardingProps {
  onComplete: (data: { apiKey: string; folder: string; profile: string; contentTypes: ContentTypes }) => void;
}

// ─── Step 0 · Welcome ────────────────────────────────────────────────

function WelcomeStep() {
  return (
    <div className="onb-hero">
      <div className="mark" style={idx(0)}><IcLogoMark /></div>
      <h1 style={idx(1)}>Welcome to <span className="grad">Kira</span></h1>
      <div className="tag" style={idx(2)}>Rename, organize, done.</div>
      <div className="bullets" style={idx(3)}>
        <div><IcScan /><span>Scans your library for messy filenames</span></div>
        <div><IcSparkles /><span>Matches against TMDB, TVDB &amp; AniDB</span></div>
        <div><IcShieldCheck /><span>Nothing renamed without your approval</span></div>
        <div><IcUndo /><span>Every rename is one click to undo</span></div>
        <div><IcLink /><span>Hardlinks keep disk usage flat</span></div>
      </div>
    </div>
  );
}

// ─── Step 1 · Library (content types) ────────────────────────────────

function ContentTypeStep({ types, setTypes }: { types: ContentTypes; setTypes: (t: ContentTypes) => void }) {
  const items: { key: keyof ContentTypes; icon: ReactNode; label: string; desc: string; ex: string; color: string; soon?: boolean }[] = [
    { key: 'movies', icon: <IcFilm />,  label: 'Movies',
      desc: 'Film files in any common format.',
      ex: 'Dune · Past Lives · Oppenheimer', color: 'var(--ink-2)' },
    { key: 'tv',     icon: <IcTv />,    label: 'TV Shows',
      desc: 'Series with seasons and episodes.',
      ex: 'Severance · The Bear · Shōgun', color: 'var(--info)' },
    { key: 'anime',  icon: <IcAnime />, label: 'Anime',
      desc: 'Cour-aware matching, absolute numbering, franchises.',
      ex: 'Frieren · Jujutsu Kaisen · Spy × Family', color: 'var(--media-anime)' },
    { key: 'music',  icon: <IcMusic />, label: 'Music',
      desc: 'Albums and tracks — arriving in a later release.',
      ex: 'Radiohead · Pink Floyd · Kendrick Lamar', color: 'var(--media-music)', soon: true },
  ];
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 1 of 6</span>
        <span>Required</span>
      </div>
      <div className="onb-title" style={idx(1)}>What's in your library?</div>
      <div className="onb-sub" style={idx(2)}>
        Pick all that apply — it tunes the matching defaults and the first-scan summary.
      </div>

      <div className="ct-grid" style={idx(3)} role="group" aria-label="Content types">
        {items.map(it => {
          const on = !it.soon && !!types[it.key];
          return (
            <button key={it.key}
                 type="button"
                 role="checkbox"
                 aria-checked={on}
                 aria-disabled={it.soon || undefined}
                 aria-label={`${it.label} — ${it.desc}`}
                 className={`ct-card ${on ? 'selected' : ''} ${it.soon ? 'soon' : ''}`}
                 onClick={() => { if (!it.soon) setTypes({ ...types, [it.key]: !on }); }}>
              <div className="ct-card-head">
                <div className="ct-icon" style={{ color: it.color }}>
                  <span style={{ display: 'inline-flex', width: 18, height: 18 }}>{it.icon}</span>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="ct-name">
                    {it.label}
                    {it.soon ? <span className="ct-soon">Coming soon</span> : null}
                  </div>
                  <div className="ct-desc">{it.desc}</div>
                </div>
                <div className={`ct-check ${on ? 'on' : ''}`}>{on ? <IcCheck /> : null}</div>
              </div>
              <div className="ct-ex">{it.ex}</div>
            </button>
          );
        })}
      </div>

      <div className="onb-hint" style={idx(4)}>
        Movies + TV covers most libraries — everything stays switchable in Settings.
      </div>
    </>
  );
}

// ─── Step 2 · Connect (TMDB required, TVDB optional) ─────────────────

function KeyField({ label, required, placeholder, value, setValue, validation, helpHref, helpLabel }: {
  label: string;
  required: boolean;
  placeholder: string;
  value: string;
  setValue: (v: string) => void;
  validation: ValidationState;
  helpHref: string;
  helpLabel: string;
}) {
  const state = validation.state;
  return (
    <div className="onb-keyfield">
      <div className="head">
        <span className="lbl">{label}</span>
        <span className={`req ${required ? '' : 'opt'}`}>{required ? 'Required' : 'Optional'}</span>
        <a href={helpHref} target="_blank" rel="noreferrer">{helpLabel} →</a>
      </div>
      <div className="onb-input-wrap">
        <input
          className="input mono"
          placeholder={placeholder}
          value={value}
          onChange={e => setValue(e.target.value)}
          spellCheck={false}
        />
        <div className="state">
          {state === 'checking' && <span style={{ color: 'var(--ink-3)' }}><IcSpin /></span>}
          {state === 'success' && <span style={{ color: 'var(--accent)' }}><IcCheck /></span>}
          {state === 'error' && <span style={{ color: 'var(--conf-low)' }}><IcX /></span>}
        </div>
      </div>
      {state === 'success' && (
        <div className="onb-state success">
          <IcCheck />
          <span>
            {validation.state === 'success' && validation.existing
              ? <><b>Already connected</b> — a working key is on file. Paste a new one to replace it.</>
              : <><b>Key verified.</b>{validation.state === 'success' && typeof validation.latencyMs === 'number' ? <> Responded in {validation.latencyMs}&nbsp;ms.</> : null}</>}
          </span>
        </div>
      )}
      {state === 'error' && validation.state === 'error' && (
        <div className="onb-state error"><IcAlertTri /><span>{validation.error}</span></div>
      )}
      {state === 'checking' && (
        <div className="onb-state checking"><IcSpin /><span>Verifying…</span></div>
      )}
    </div>
  );
}

function ConnectStep({ types, tmdbKey, setTmdbKey, tmdbVal, setTmdbVal, tvdbKey, setTvdbKey, tvdbVal, setTvdbVal }: {
  types: ContentTypes;
  tmdbKey: string; setTmdbKey: (v: string) => void;
  tmdbVal: ValidationState; setTmdbVal: (v: ValidationState) => void;
  tvdbKey: string; setTvdbKey: (v: string) => void;
  tvdbVal: ValidationState; setTvdbVal: (v: ValidationState) => void;
}) {
  const wantsTvdb = !!types.tv || !!types.anime;

  // Detect keys that are ALREADY configured on the backend (a wiped
  // localStorage re-runs onboarding, but the server still has the keys —
  // forcing the user to dig them out again was hostile).
  useEffect(() => {
    let cancelled = false;
    void api.getSettings().then(s => {
      if (cancelled) return;
      const has = (k: string) => {
        const v = s[k];
        return (typeof v === 'object' && v !== null && (v as { set?: boolean }).set === true)
          || (typeof v === 'string' && v.length > 0);
      };
      if (tmdbVal.state === 'idle' && !tmdbKey && has('providers.tmdb.api_key')) {
        setTmdbVal({ state: 'success', existing: true });
      }
      if (tvdbVal.state === 'idle' && !tvdbKey && has('providers.tvdb.api_key')) {
        setTvdbVal({ state: 'success', existing: true });
      }
    }).catch(() => { /* backend offline — typing still validates */ });
    return () => { cancelled = true; };
    // mount-only by design
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced validation — only fires once the key LOOKS complete (≥ 26
  // chars), so we never persist a half-typed key over a working one.
  const useKeyValidation = (key: string, provider: 'tmdb' | 'tvdb', settingKey: string,
                            setVal: (v: ValidationState) => void, existing: boolean) => {
    useEffect(() => {
      const k = (key || '').trim();
      if (!k) {
        if (!existing) setVal({ state: 'idle' });
        return;
      }
      if (k.length < 26) { setVal({ state: 'idle' }); return; }
      setVal({ state: 'checking' });
      const handle = setTimeout(async () => {
        const result = await validateProviderKey(provider, settingKey, k);
        setVal(result.ok
          ? { state: 'success', latencyMs: result.latencyMs }
          : { state: 'error', error: result.error });
      }, 400);
      return () => clearTimeout(handle);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [key]);
  };
  useKeyValidation(tmdbKey, 'tmdb', 'providers.tmdb.api_key', setTmdbVal,
    tmdbVal.state === 'success' && !!tmdbVal.existing);
  useKeyValidation(tvdbKey, 'tvdb', 'providers.tvdb.api_key', setTvdbVal,
    tvdbVal.state === 'success' && !!tvdbVal.existing);

  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 2 of 6</span>
        <span>Required</span>
      </div>
      <div className="onb-title" style={idx(1)}>Connect your metadata</div>
      <div className="onb-sub" style={idx(2)}>
        Free API keys power the matching — titles, posters, episode lists.
        Keys never leave this server.
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 18, marginTop: 4, ...idx(3) }}>
        <KeyField
          label="TMDB"
          required
          placeholder="Paste your TMDB API key…"
          value={tmdbKey}
          setValue={setTmdbKey}
          validation={tmdbVal}
          helpHref="https://www.themoviedb.org/settings/api"
          helpLabel="Get a key"
        />
        {wantsTvdb ? (
          <KeyField
            label="TVDB"
            required={false}
            placeholder="Paste your TVDB v4 API key…"
            value={tvdbKey}
            setValue={setTvdbKey}
            validation={tvdbVal}
            helpHref="https://thetvdb.com/api-information"
            helpLabel="Get a key"
          />
        ) : null}
      </div>

      {wantsTvdb ? (
        <div className="onb-hint" style={idx(4)}>
          TVDB sharpens TV &amp; anime episode data{types.anime ? ' (AniDB needs no key for matching)' : ''} — add it now or later in Settings.
        </div>
      ) : null}

      {/* ffmpeg check — extras like embedded-subtitle extraction need it.
          Docker ships it (the row just shows Ready); on bare installs the
          one-click button has Kira install its own copy, nothing else needed. */}
      <div className="onb-folder-card" style={{ marginTop: 14, ...idx(5) }}>
        <FfmpegStatusRow compact />
      </div>
    </>
  );
}

// ─── Step 3 · Folder ─────────────────────────────────────────────────

function MediaFolderStep({ folder, setFolder, watchFolder, setWatchFolder }: {
  folder: string; setFolder: (s: string) => void;
  watchFolder: boolean; setWatchFolder: (v: boolean) => void;
}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 3 of 6</span>
        <span>Required</span>
      </div>
      <div className="onb-title" style={idx(1)}>Where's your media?</div>
      <div className="onb-sub" style={idx(2)}>
        Kira scans this folder for video files. The first scan tells you exactly
        how many movies / TV / anime it found — nothing is touched yet.
      </div>

      <div className="onb-folder-card" style={idx(3)}>
        <div className="icon"><IcFolder /></div>
        <div className="info">
          <div className="path">{folder || '(no folder selected)'}</div>
          <div className="meta">Click Browse to pick a different folder</div>
        </div>
        <button className="btn btn-sm" onClick={() => setPickerOpen(true)}>
          <IcFolder /> Browse…
        </button>
      </div>

      <label className="onb-checkrow" style={idx(4)}>
        <input
          type="checkbox"
          checked={watchFolder}
          onChange={e => setWatchFolder(e.target.checked)}
          style={{ accentColor: 'var(--accent)' }}
        />
        <span>
          Watch this folder and scan automatically when new files appear
          <span className="sub">Turns on the auto-scan daemon — new downloads land in Review without clicking Scan.</span>
        </span>
      </label>

      {pickerOpen ? (
        <FolderPickerModal
          initialPath={folder}
          onPick={(p) => { setFolder(p); setPickerOpen(false); }}
          onClose={() => setPickerOpen(false)}
        />
      ) : null}
    </>
  );
}

// ─── Step 4 · Handling (file operation + rename mode) ────────────────

function HandlingStep({ op, setOp, mode, setMode }: {
  op: string; setOp: (v: string) => void;
  mode: string; setMode: (v: string) => void;
}) {
  const ops = [
    { key: 'hardlink', label: 'Hardlink', rec: true,
      desc: 'Renamed copy appears in the library; the original stays put. Zero extra disk — seeding keeps working.' },
    { key: 'move', label: 'Move',
      desc: 'Relocate the file itself. The source folder is cleaned up afterwards.' },
    { key: 'copy', label: 'Copy',
      desc: 'Duplicate into the library. Safest, but uses double the space.' },
    { key: 'symlink', label: 'Symlink',
      desc: 'A pointer at the original. Light, but breaks if the source moves.' },
  ];
  const modes = [
    { key: 'in-place', label: 'In place',
      desc: 'Files stay in their current folders — only the names change.' },
    { key: 'move-to-library', label: 'Into the library',
      desc: 'Build a fresh, fully organized tree under your media root.' },
  ];
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 4 of 6</span>
        <span>Optional</span>
      </div>
      <div className="onb-title" style={idx(1)}>How should files be placed?</div>
      <div className="onb-sub" style={idx(2)}>
        What happens to the original when Kira lands the renamed copy. Hardlink is
        the safe default — switchable per-batch later.
      </div>

      <div className="ct-grid" style={idx(3)} role="radiogroup" aria-label="File operation">
        {ops.map(o => (
          <button key={o.key} type="button" role="radio" aria-checked={op === o.key}
                  className={`ct-card ${op === o.key ? 'selected' : ''}`}
                  onClick={() => setOp(o.key)}>
            <div className="ct-card-head">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="ct-name">
                  {o.label}
                  {o.rec ? <span className="ct-soon rec">Recommended</span> : null}
                </div>
                <div className="ct-desc">{o.desc}</div>
              </div>
              <div className={`ct-check ${op === o.key ? 'on' : ''}`}>{op === o.key ? <IcCheck /> : null}</div>
            </div>
          </button>
        ))}
      </div>

      <div className="onb-naming" style={idx(4)} role="radiogroup" aria-label="Rename mode">
        {modes.map(m => (
          <button key={m.key} type="button" role="radio" aria-checked={mode === m.key}
                  className={`naming-card ${mode === m.key ? 'selected' : ''}`}
                  onClick={() => setMode(m.key)}>
            <div className="naming-card-head">
              <div>
                <div className="naming-card-name">{m.label}</div>
                <div className="text-xs text-muted">{m.desc}</div>
              </div>
              <div className="naming-card-check">{mode === m.key ? <IcCheck /> : null}</div>
            </div>
          </button>
        ))}
      </div>
    </>
  );
}

// ─── Step 5 · Naming ─────────────────────────────────────────────────

function NamingStep({ profile, setProfile }: { profile: string; setProfile: (p: string) => void }) {
  const profiles = [
    {
      key: 'Plex',
      name: 'Plex',
      desc: 'The de-facto standard for most setups.',
      tree: (
        <>
          <div className="dir">Movies/</div>
          <div className="dir">  Oppenheimer (2023)/</div>
          <div className="accent-line">    Oppenheimer (2023) [2160p UHD].mkv</div>
          <div className="dir">TV/</div>
          <div className="dir">  The Bear (2022)/</div>
          <div className="dir">    Season 03/</div>
          <div className="accent-line">      The Bear - S03E01 - Tomorrow.mkv</div>
        </>
      ),
    },
    {
      key: 'Jellyfin',
      name: 'Jellyfin',
      desc: 'Cleaner names, lighter on tags.',
      tree: (
        <>
          <div className="dir">Movies/</div>
          <div className="dir">  Oppenheimer (2023)/</div>
          <div className="accent-line">    Oppenheimer (2023).mkv</div>
          <div className="dir">TV/</div>
          <div className="dir">  The Bear (2022)/</div>
          <div className="dir">    Season 03/</div>
          <div className="accent-line">      The Bear (2022) - S03E01 - Tomorrow.mkv</div>
        </>
      ),
    },
  ];

  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 5 of 6</span>
        <span>Optional</span>
      </div>
      <div className="onb-title" style={idx(1)}>Pick a naming style</div>
      <div className="onb-sub" style={idx(2)}>
        Files get renamed into folders your media server understands. Change it anytime —
        including fully custom templates.
      </div>

      <div className="onb-naming" style={idx(3)} role="radiogroup" aria-label="Naming style">
        {profiles.map(p => (
          <button
            key={p.key}
            type="button"
            role="radio"
            aria-checked={profile === p.key}
            aria-label={`${p.name} — ${p.desc}`}
            className={`naming-card ${profile === p.key ? 'selected' : ''}`}
            onClick={() => setProfile(p.key)}
          >
            <div className="naming-card-head">
              <div>
                <div className="naming-card-name">{p.name}</div>
                <div className="text-xs text-muted">{p.desc}</div>
              </div>
              <div className="naming-card-check">{profile === p.key ? <IcCheck /> : null}</div>
            </div>
            <div className="naming-card-tree">{p.tree}</div>
          </button>
        ))}
      </div>
    </>
  );
}

// ─── Step 5 · Launch ─────────────────────────────────────────────────

function ReadyStep({ data, gotoStep }: {
  data: { apiKey: string; tmdbExisting: boolean; folder: string; profile: string; contentTypes: ContentTypes; watchFolder: boolean; fileOp: string; renameMode: string };
  gotoStep: (n: number) => void;
}) {
  return (
    <>
      <div className="onb-eyebrow" style={idx(0)}>
        <span className="step-n">Step 6 of 6</span>
        <span>Ready</span>
      </div>
      <div className="onb-title" style={idx(1)}>You're all set.</div>
      <div className="onb-sub" style={idx(2)}>
        Kira runs its first scan when you hit start — every match waits for your
        approval before anything is renamed.
      </div>

      <div className="onb-summary" style={idx(3)}>
        <div className="row">
          <div className="icon"><IcSparkles /></div>
          <div>
            <div className="lbl">Library</div>
            <div className="val" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {Object.entries(data.contentTypes).filter(([, v]) => v).map(([k]) => (
                <span key={k} className="chip">
                  {k === 'tv' ? 'TV Shows' : k.charAt(0).toUpperCase() + k.slice(1)}
                </span>
              ))}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(1)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcKey /></div>
          <div>
            <div className="lbl">Metadata</div>
            <div className="val" style={{ color: 'var(--accent)' }}>
              {data.tmdbExisting && !data.apiKey
                ? 'TMDB connected · existing key'
                : <>TMDB connected · key ending {data.apiKey.slice(-4)}</>}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(2)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcFolder /></div>
          <div>
            <div className="lbl">Media folder</div>
            <div className="val">
              {data.folder}
              {data.watchFolder ? <span className="chip" style={{ marginLeft: 8 }}>auto-scan on</span> : null}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(3)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcLink /></div>
          <div>
            <div className="lbl">File handling</div>
            <div className="val" style={{ textTransform: 'capitalize' }}>
              {data.fileOp} · {data.renameMode === 'in-place' ? 'in place' : 'into the library'}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(4)}>Edit</button>
        </div>
        <div className="row">
          <div className="icon"><IcTag /></div>
          <div>
            <div className="lbl">Naming profile</div>
            <div className="val">{data.profile || 'Plex (default) — customize later'}</div>
          </div>
          <button className="edit" onClick={() => gotoStep(5)}>Edit</button>
        </div>
      </div>
    </>
  );
}

// ─── Shell ───────────────────────────────────────────────────────────

const RAIL_STEPS = [
  { n: 1, label: 'Library',  hint: 'What you have' },
  { n: 2, label: 'Connect',  hint: 'Metadata keys' },
  { n: 3, label: 'Folder',   hint: 'Where it lives' },
  { n: 4, label: 'Handling', hint: 'Move or link' },
  { n: 5, label: 'Naming',   hint: 'How it looks' },
  { n: 6, label: 'Launch',   hint: 'First scan' },
];

export function Onboarding({ onComplete }: OnboardingProps) {
  // 0=Welcome · 1=Library · 2=Connect · 3=Folder · 4=Handling · 5=Naming · 6=Launch
  const [step, setStep] = useState(0);
  const [contentTypes, setContentTypes] = useState<ContentTypes>({ movies: true, tv: true, anime: false, music: false });
  const [tmdbKey, setTmdbKey] = useState('');
  const [tmdbVal, setTmdbVal] = useState<ValidationState>({ state: 'idle' });
  const [tvdbKey, setTvdbKey] = useState('');
  const [tvdbVal, setTvdbVal] = useState<ValidationState>({ state: 'idle' });
  const [folder, setFolder] = useState('/media');
  const [watchFolder, setWatchFolder] = useState(true);
  const [profile, setProfile] = useState('Plex');
  const [fileOp, setFileOp] = useState('hardlink');
  const [renameMode, setRenameMode] = useState('in-place');
  const [version, setVersion] = useState<string | null>(null);
  // The app renders (blurred) behind this overlay — it must not scroll under it.
  useScrollLock();

  useEffect(() => {
    let cancelled = false;
    void api.health().then(h => { if (!cancelled && h.version) setVersion(h.version); }).catch(() => {});
    // Re-run protection: a wiped localStorage restarts onboarding while the
    // BACKEND still has real settings. Prefill folder + profile from the
    // saved values so completing the flow can't clobber a configured library
    // root with the '/media' default.
    void api.getSettings().then(st => {
      if (cancelled) return;
      const root = st['paths.library_root'];
      if (typeof root === 'string' && root.trim()) setFolder(root);
      const prof = st['naming.profile'];
      if (typeof prof === 'string' && prof.trim()) setProfile(prof);
      const op = st['rename.default_op'];
      if (typeof op === 'string' && op.trim()) setFileOp(op);
      const mode = st['rename.mode'];
      if (typeof mode === 'string' && mode.trim()) setRenameMode(mode);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const anyContentSelected = Object.values(contentTypes).some(Boolean);

  const canContinue = () => {
    if (step === 1) return anyContentSelected;
    if (step === 2) return tmdbVal.state === 'success';
    if (step === 3) return folder.length > 0;
    return true;
  };
  const isOptional = step === 4 || step === 5;

  const next = () => { if (step < 6) setStep(step + 1); else void complete(); };
  const skip = () => {
    if (step === 4) { setFileOp('hardlink'); setRenameMode('in-place'); setStep(5); }
    else if (step === 5) { setProfile(''); setStep(6); }
  };
  const back = () => { if (step > 0) setStep(step - 1); };

  const complete = async () => {
    // Persist everything the flow collected. The watch checkbox writes the
    // REAL auto-scan config (watch.config — the settings PUT re-arms the
    // watcher daemon); a previous version wrote a `paths.watch_enabled` key
    // that nothing read, so the checkbox silently did nothing.
    try {
      const values: Record<string, unknown> = {
        'paths.library_root': folder,
        'naming.profile': profile || 'Plex',
        'rename.default_op': fileOp,
        'rename.mode': renameMode,
        'onboarding.content_types': contentTypes,
        // Server-side completion flag — a fresh browser on a configured
        // server skips onboarding; a factory-reset server re-enters it.
        'onboarding.completed': true,
      };
      if (watchFolder) {
        values['watch.config'] = {
          auto_scan: true, debounce_seconds: 30, poll_interval_seconds: 900, folders: {},
        };
      }
      await api.putSettings(values);
    } catch { /* user can fix in Settings later */ }
    setOnboarded(true);
    onComplete({ apiKey: tmdbKey, folder, profile: profile || 'Plex', contentTypes });
  };

  const tmdbExisting = tmdbVal.state === 'success' && !!tmdbVal.existing;
  const progress = step === 0 ? 0 : (step / 6) * 100;

  return (
    <div className="onboarding-root">
      <div className="backdrop" style={{ position: 'absolute' }} />
      <div className="onb-shell">
        {/* ── Left rail: brand + step map ── */}
        <aside className="onb-rail">
          <div className="brand">
            <div className="mark"><IcLogoMark /></div>
            <div>
              <div className="name">Kira</div>
              <div className="sub">Rename, organize, done.</div>
            </div>
          </div>
          <nav className="steps" aria-label="Setup steps">
            {RAIL_STEPS.map(s => {
              const state = step === 0 ? '' : s.n < step ? 'done' : s.n === step ? 'active' : '';
              return (
                <button
                  key={s.n}
                  type="button"
                  className={`step ${state}`}
                  disabled={step === 0 || s.n > step}
                  onClick={() => { if (s.n < step) setStep(s.n); }}
                >
                  <span className="dot">{state === 'done' ? <IcCheck /> : s.n}</span>
                  <span className="txt">
                    <span className="l">{s.label}</span>
                    <span className="h">{s.hint}</span>
                  </span>
                </button>
              );
            })}
          </nav>
          <div className="foot">Self-hosted · v{version ?? '0.5.0'}</div>
        </aside>

        {/* ── Right pane: step content ── */}
        <section className="onb-pane">
          <div className="onb-progress" aria-hidden>
            <div className="bar" style={{ width: `${progress}%` }} />
          </div>
          <div className="onb-body" key={step}>
            {step === 0 && <WelcomeStep />}
            {step === 1 && <ContentTypeStep types={contentTypes} setTypes={setContentTypes} />}
            {step === 2 && (
              <ConnectStep
                types={contentTypes}
                tmdbKey={tmdbKey} setTmdbKey={setTmdbKey} tmdbVal={tmdbVal} setTmdbVal={setTmdbVal}
                tvdbKey={tvdbKey} setTvdbKey={setTvdbKey} tvdbVal={tvdbVal} setTvdbVal={setTvdbVal}
              />
            )}
            {step === 3 && (
              <MediaFolderStep folder={folder} setFolder={setFolder} watchFolder={watchFolder} setWatchFolder={setWatchFolder} />
            )}
            {step === 4 && <HandlingStep op={fileOp} setOp={setFileOp} mode={renameMode} setMode={setRenameMode} />}
            {step === 5 && <NamingStep profile={profile} setProfile={setProfile} />}
            {step === 6 && (
              <ReadyStep
                data={{ apiKey: tmdbKey, tmdbExisting, folder, profile, contentTypes, watchFolder, fileOp, renameMode }}
                gotoStep={setStep}
              />
            )}
          </div>

          <div className="onb-foot">
            {step === 0 ? (
              <>
                <div className="hint">Takes about two minutes</div>
                <button className="btn btn-brand" style={{ padding: '12px 24px', fontSize: 14 }} onClick={next}>
                  Get started <IcArrowRight />
                </button>
              </>
            ) : (
              <>
                <button className="btn btn-ghost" onClick={back}>← Back</button>
                <div className="right">
                  {isOptional && <button className="onb-skip" onClick={skip}>Skip — set up later</button>}
                  {step === 6 ? (
                    <button className="btn btn-primary" style={{ padding: '11px 22px' }} onClick={() => void complete()}>
                      <IcScan /> Start first scan
                    </button>
                  ) : (
                    <button className="btn btn-primary" disabled={!canContinue()} onClick={next}>
                      Continue <IcArrowRight />
                    </button>
                  )}
                </div>
              </>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
