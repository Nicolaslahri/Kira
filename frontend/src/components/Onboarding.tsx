import { useState, useEffect, useRef, type CSSProperties, type ReactNode } from 'react';
import type { ContentTypes } from '../lib/types';
import { api } from '../lib/api';
import {
  IcLogoMark, IcScan, IcSparkles, IcShieldCheck, IcLink,
  IcCheck, IcX, IcAlertTri, IcSpin, IcFolder, IcKey, IcArrowRight,
  IcFilm, IcTv, IcAnime, IcMusic, IcTag,
} from '../lib/icons';
import { FolderPickerModal } from './FolderPickerModal';

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
  | { state: 'success'; latencyMs?: number | null }
  | { state: 'error'; error: string };

async function validateTmdbKey(key: string): Promise<{ ok: true; latencyMs: number | null } | { ok: false; error: string }> {
  const k = (key || '').trim();
  if (k.length < 16) return { ok: false, error: 'Key is too short. TMDB keys are 32 characters.' };
  // Persist the key first so the backend's test endpoint uses it.
  try {
    await api.putSettings({ 'providers.tmdb.api_key': k });
    const res = await api.testProvider('tmdb');
    if (res.ok) return { ok: true, latencyMs: res.latency_ms ?? null };
    return { ok: false, error: res.detail || 'TMDB rejected the key.' };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

const idx = (i: number): CSSProperties => ({ ['--i' as never]: i });

interface OnboardingProps {
  onComplete: (data: { apiKey: string; folder: string; profile: string; contentTypes: ContentTypes }) => void;
}

function WelcomeStep() {
  return (
    <div className="onboarding-hero">
      <div className="mark" style={idx(0)}><IcLogoMark /></div>
      <h1 style={idx(1)}>Welcome to Kira</h1>
      <div className="tag" style={idx(2)}>Rename, organize, done.</div>
      <div className="bullets" style={idx(3)}>
        <div><IcScan /><span>Scans your media for messy filenames</span></div>
        <div><IcSparkles /><span>Matches against TMDB metadata</span></div>
        <div><IcShieldCheck /><span>Nothing renamed without your approval</span></div>
        <div><IcLink /><span>Hardlinks keep disk usage flat</span></div>
      </div>
    </div>
  );
}

interface TmdbStepProps {
  value: string;
  setValue: (v: string) => void;
  validation: ValidationState;
  setValidation: (v: ValidationState) => void;
}

interface ContentTypeStepProps {
  types: ContentTypes;
  setTypes: (t: ContentTypes) => void;
}

function ContentTypeStep({ types, setTypes }: ContentTypeStepProps) {
  const items: { key: keyof ContentTypes; icon: ReactNode; label: string; desc: string; ex: string; color: string }[] = [
    { key: 'movies', icon: <IcFilm />,  label: 'Movies',
      desc: 'Film files in any common format.',
      ex: 'Dune · Past Lives · Oppenheimer', color: 'var(--ink-2)' },
    { key: 'tv',     icon: <IcTv />,    label: 'TV Shows',
      desc: 'Series with seasons and episodes.',
      ex: 'Severance · The Bear · Shōgun', color: 'var(--info)' },
    { key: 'anime',  icon: <IcAnime />, label: 'Anime',
      desc: 'Uses release groups and absolute episode numbers.',
      ex: 'Frieren · Jujutsu Kaisen · Spy × Family', color: 'var(--media-anime)' },
    { key: 'music',  icon: <IcMusic />, label: 'Music',
      desc: 'Albums and individual tracks.',
      ex: 'Radiohead · Pink Floyd · Kendrick Lamar', color: 'var(--media-music)' },
  ];
  return (
    <>
      <div className="onboarding-eyebrow" style={idx(0)}>
        <span className="step-n">Step 1 of 5</span>
        <span>Required</span>
      </div>
      <div className="onboarding-title" style={idx(1)}>What kind of media do you have?</div>
      <div className="onboarding-sub" style={idx(2)}>
        Pick all that apply. We'll only set up the providers you actually need.
      </div>

      <div className="ct-grid" style={idx(3)} role="group" aria-label="Content types">
        {items.map(it => {
          const on = !!types[it.key];
          return (
            <button key={it.key}
                 type="button"
                 role="checkbox"
                 aria-checked={on}
                 aria-label={`${it.label} — ${it.desc}`}
                 className={`ct-card ${on ? 'selected' : ''}`}
                 onClick={() => setTypes({ ...types, [it.key]: !on })}>
              <div className="ct-card-head">
                <div className="ct-icon" style={{ color: it.color }}>
                  <span style={{ display: 'inline-flex', width: 18, height: 18 }}>{it.icon}</span>
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="ct-name">{it.label}</div>
                  <div className="ct-desc">{it.desc}</div>
                </div>
                <div className={`ct-check ${on ? 'on' : ''}`}>{on ? <IcCheck /> : null}</div>
              </div>
              <div className="ct-ex">{it.ex}</div>
            </button>
          );
        })}
      </div>

      <div className="text-xs text-muted" style={idx(4)}>
        Don't see what you have? Movies + TV covers most libraries — you can enable more later in Settings.
      </div>
    </>
  );
}

function TmdbStep({ value, setValue, validation, setValidation }: TmdbStepProps) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { ref.current?.focus(); }, []);

  useEffect(() => {
    const k = (value || '').trim();
    if (!k) { setValidation({ state: 'idle' }); return; }
    setValidation({ state: 'checking' });
    const handle = setTimeout(async () => {
      const result = await validateTmdbKey(k);
      setValidation(
        result.ok
          ? { state: 'success', latencyMs: result.latencyMs }
          : { state: 'error', error: result.error }
      );
    }, 350);
    return () => clearTimeout(handle);
  }, [value, setValidation]);

  const state = validation.state;

  return (
    <>
      <div className="onboarding-eyebrow" style={idx(0)}>
        <span className="step-n">Step 2 of 5</span>
        <span>Required</span>
      </div>
      <div className="onboarding-title" style={idx(1)}>Connect to TMDB</div>
      <div className="onboarding-sub" style={idx(2)}>
        Kira needs a free TMDB API key to look up titles, posters, and episode metadata.{' '}
        <a href="https://www.themoviedb.org/settings/api" target="_blank" rel="noreferrer">Get one here →</a>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 4, ...idx(3) }}>
        <div className="onboarding-input-wrap">
          <input
            ref={ref}
            className="input mono"
            placeholder="Paste your TMDB API key…"
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
          <div className="onboarding-state success">
            <IcCheck />
            <span>
              <b>Key verified.</b>
              {validation.state === 'success' && typeof validation.latencyMs === 'number'
                ? <> TMDB responded in {validation.latencyMs}&nbsp;ms.</>
                : null}
              {' '}You're good to go.
            </span>
          </div>
        )}
        {state === 'error' && validation.state === 'error' && (
          <div className="onboarding-state error">
            <IcAlertTri /><span>{validation.error}</span>
          </div>
        )}
        {state === 'checking' && (
          <div className="onboarding-state checking"><IcSpin /><span>Verifying with TMDB…</span></div>
        )}
        {state === 'idle' && (
          <div className="text-xs text-muted" style={{ padding: '4px 2px' }}>
            Your key stays on this server — Kira never sends it anywhere else.
          </div>
        )}
      </div>
    </>
  );
}

interface MediaFolderStepProps {
  folder: string;
  setFolder: (s: string) => void;
  watchFolder: boolean;
  setWatchFolder: (v: boolean) => void;
}

function MediaFolderStep({ folder, setFolder, watchFolder, setWatchFolder }: MediaFolderStepProps) {
  // Real folder picker — clicking Browse opens the FolderPickerModal,
  // which calls /api/v1/folders to walk the filesystem. Previously the
  // button had no onClick and the breakdown numbers (342 files, 128
  // movies, etc.) were hardcoded — onboarding was lying to users about
  // what they were about to scan.
  const [pickerOpen, setPickerOpen] = useState(false);

  return (
    <>
      <div className="onboarding-eyebrow" style={idx(0)}>
        <span className="step-n">Step 3 of 5</span>
        <span>Required</span>
      </div>
      <div className="onboarding-title" style={idx(1)}>Where's your media?</div>
      <div className="onboarding-sub" style={idx(2)}>
        Kira will scan this folder for video files. The first scan after onboarding
        tells you exactly how many movies / TV / anime were found.
      </div>

      <div className="onboarding-folder-card found" style={idx(3)}>
        <div className="onboarding-folder-icon"><IcFolder /></div>
        <div className="onboarding-folder-info">
          <div className="path">{folder || '(no folder selected)'}</div>
          <div className="meta" style={{ color: 'var(--ink-3)', fontSize: 11 }}>
            Click Browse to pick a different folder
          </div>
        </div>
        <button className="btn btn-sm" onClick={() => setPickerOpen(true)}>
          <IcFolder /> Browse…
        </button>
      </div>

      <label className="flex items-center gap-2" style={{ marginTop: 8, fontSize: 12, color: 'var(--ink-2)', cursor: 'pointer', ...idx(4) }}>
        <input
          type="checkbox"
          checked={watchFolder}
          onChange={e => setWatchFolder(e.target.checked)}
          style={{ accentColor: 'var(--accent)' }}
        />
        Watch this folder for new files automatically
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

interface NamingStepProps {
  profile: string;
  setProfile: (p: string) => void;
}

function NamingStep({ profile, setProfile }: NamingStepProps) {
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
      <div className="onboarding-eyebrow" style={idx(0)}>
        <span className="step-n">Step 4 of 5</span>
        <span>Optional</span>
      </div>
      <div className="onboarding-title" style={idx(1)}>Pick a naming style</div>
      <div className="onboarding-sub" style={idx(2)}>
        Kira will rename files into folders that your media server understands. You can change this anytime.
      </div>

      <div className="onboarding-naming" style={idx(3)} role="radiogroup" aria-label="Naming style">
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

      <div className="text-xs text-muted" style={{ marginTop: 4, ...idx(4) }}>
        Need something else? Pick either now — you can switch to a custom template in Settings later.
      </div>
    </>
  );
}

interface ReadyStepProps {
  data: { apiKey: string; folder: string; profile: string; contentTypes: ContentTypes };
  gotoStep: (n: number) => void;
}

function ReadyStep({ data, gotoStep }: ReadyStepProps) {
  return (
    <>
      <div className="onboarding-eyebrow" style={idx(0)}>
        <span className="step-n">Step 5 of 5</span>
        <span>Ready</span>
      </div>
      <div className="onboarding-title" style={idx(1)}>You're all set.</div>
      <div className="onboarding-sub" style={idx(2)}>
        Review your setup below. Kira will run its first scan as soon as you click start —
        nothing will be renamed without your approval.
      </div>

      <div className="onboarding-summary" style={idx(3)}>
        <div className="onboarding-summary-row">
          <div className="onboarding-summary-icon"><IcSparkles /></div>
          <div>
            <div className="lbl">Content types</div>
            <div className="val" style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
              {Object.entries(data.contentTypes).filter(([, v]) => v).map(([k]) => (
                <span key={k} style={{ padding: '1px 8px', borderRadius: 5, background: 'rgba(255,255,255,0.06)', fontSize: 11.5, textTransform: 'capitalize', fontFamily: 'var(--font-ui)', fontWeight: 500 }}>
                  {k === 'tv' ? 'TV Shows' : k.charAt(0).toUpperCase() + k.slice(1)}
                </span>
              ))}
            </div>
          </div>
          <button className="edit" onClick={() => gotoStep(1)}>Edit</button>
        </div>
        <div className="onboarding-summary-row">
          <div className="onboarding-summary-icon"><IcKey /></div>
          <div>
            <div className="lbl">TMDB API</div>
            <div className="val" style={{ color: 'var(--accent)' }}>Connected · key ending {data.apiKey.slice(-4)}</div>
          </div>
          <button className="edit" onClick={() => gotoStep(2)}>Edit</button>
        </div>
        <div className="onboarding-summary-row">
          <div className="onboarding-summary-icon"><IcFolder /></div>
          <div>
            <div className="lbl">Media folder</div>
            <div className="val">{data.folder}</div>
          </div>
          <button className="edit" onClick={() => gotoStep(3)}>Edit</button>
        </div>
        <div className="onboarding-summary-row">
          <div className="onboarding-summary-icon"><IcTag /></div>
          <div>
            <div className="lbl">Naming profile</div>
            <div className="val">{data.profile || 'Custom — configure later'}</div>
          </div>
          <button className="edit" onClick={() => gotoStep(4)}>Edit</button>
        </div>
      </div>
    </>
  );
}

export function Onboarding({ onComplete }: OnboardingProps) {
  // 0=Welcome · 1=Content types · 2=TMDB · 3=Folder · 4=Naming · 5=Ready
  const [step, setStep] = useState(0);
  const [contentTypes, setContentTypes] = useState<ContentTypes>({ movies: true, tv: true, anime: false, music: false });
  const [apiKey, setApiKey] = useState('');
  const [validation, setValidation] = useState<ValidationState>({ state: 'idle' });
  const [folder, setFolder] = useState('/media');
  const [watchFolder, setWatchFolder] = useState(true);
  const [profile, setProfile] = useState('Plex');

  const totalSteps = 5;

  const anyContentSelected = Object.values(contentTypes).some(Boolean);

  const canContinue = () => {
    if (step === 0) return true;
    if (step === 1) return anyContentSelected;
    if (step === 2) return validation.state === 'success';
    if (step === 3) return folder.length > 0;
    if (step === 4) return true;
    if (step === 5) return true;
    return false;
  };
  const isOptional = step === 4;

  const next = () => {
    if (step < 5) setStep(step + 1);
    else complete();
  };
  const skip = () => {
    if (step === 4) { setProfile(''); setStep(5); }
  };
  const back = () => { if (step > 0) setStep(step - 1); };
  const complete = async () => {
    // Persist the user's folder + watch choice so the first scan + future
    // auto-rescans pick them up. Without this, the data was collected and
    // thrown on the floor.
    try {
      await api.putSettings({
        'paths.library_root': folder,
        'paths.watch_enabled': watchFolder,
        'naming.profile': profile,
      });
    } catch { /* user can fix in Settings later */ }
    setOnboarded(true);
    onComplete({ apiKey, folder, profile, contentTypes });
  };

  return (
    <div className="onboarding-root">
      <div className="backdrop" style={{ position: 'absolute' }} />
      <div className="onboarding-card">
        {step > 0 ? (
          <div className="onboarding-progress">
            {Array.from({ length: totalSteps }).map((_, i) => {
              const stepIdx = i + 1;
              const cls = step === 5 ? 'done'
                : stepIdx < step ? 'done'
                : stepIdx === step ? 'active' : '';
              return <div key={i} className={`onboarding-step-dot ${cls}`} />;
            })}
          </div>
        ) : null}

        <div className="onboarding-body" key={step}>
          {step === 0 && <WelcomeStep />}
          {step === 1 && <ContentTypeStep types={contentTypes} setTypes={setContentTypes} />}
          {step === 2 && <TmdbStep value={apiKey} setValue={setApiKey} validation={validation} setValidation={setValidation} />}
          {step === 3 && (
            <MediaFolderStep
              folder={folder}
              setFolder={setFolder}
              watchFolder={watchFolder}
              setWatchFolder={setWatchFolder}
            />
          )}
          {step === 4 && <NamingStep profile={profile} setProfile={setProfile} />}
          {step === 5 && <ReadyStep data={{ apiKey, folder, profile, contentTypes }} gotoStep={setStep} />}
        </div>

        <div className="onboarding-foot">
          {step === 0 ? (
            <>
              <div className="text-xs text-muted">Self-hosted · v0.5.0 · Docker</div>
              <button className="btn btn-brand" style={{ padding: '12px 24px', fontSize: 14 }} onClick={next}>
                Get started <IcArrowRight />
              </button>
            </>
          ) : (
            <>
              <button className="btn btn-ghost" onClick={back}>← Back</button>
              <div className="right">
                {isOptional && <button className="onboarding-skip" onClick={skip}>Skip — set up later</button>}
                {step === 5 ? (
                  <button className="btn btn-primary" style={{ padding: '11px 22px' }} onClick={complete}>
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
      </div>
    </div>
  );
}
