import { useState, useEffect, useRef, type ReactNode, type FC } from 'react';
import type { ProviderKey, MediaType } from '../lib/types';
import { PROVIDERS, TYPE_COLOR } from '../lib/data';
import { api } from '../lib/api';
import { IcChevDown, IcRefresh, IcAlertTri, IcFilm, IcTv, IcAnime, IcMusic, IcDisc, IcWaveform, IcEye, IcEyeOff, IcSearch, IcX, IcCaption } from '../lib/icons';
import { cn } from '../lib/utils';
import { Button } from './base/buttons/button';
import { FeaturedIcon } from './base/featured-icons/featured-icon';
import { Badge, BadgeWithDot } from './base/badges/badges';
import { Input } from './base/input/input';
import { Alert } from './base/alert/alert';
import { Toggle } from './base/toggle/toggle';
import { Select } from './ui';

// ── Shared settings surface styles ──────────────────────────────────
// One source of truth so every Settings section (Connections, Paths,
// Integrations, …) uses the exact same card + nested-box treatment. Built
// on the shared elevation tokens (--surface-* / --border-* / --shadow-*) so
// the cards read as clearly raised against the canvas, matching the rest of
// the Phase 1–4 redesign. `.settings-card` adds the hover lift + entrance.
export const SETTINGS_CARD = 'settings-card rounded-2xl border border-[var(--border-2)] bg-tertiary shadow-[var(--shadow-1)]';
export const SETTINGS_NESTED = 'rounded-xl border border-[var(--border-1)] bg-[var(--surface-3)]';
export const SETTINGS_DIVIDER = 'border-[var(--border-1)]';

// ── Layout + section primitives ─────────────────────────────────────
// One source of truth for the chrome every Settings sub-page repeats:
// a width-constrained column, an intro blurb, the icon+title+desc card
// header, the divider-separated body, and the two-column label/control
// row. Before this, each of the ~20 sections hand-rolled the same
// markup, so spacing / contrast drifted between them.

/**
 * Settings page shell. Full-width (no max-width cap, no centering) so the
 * page keeps the same overall width it had before the primitives refactor —
 * sections span the available `.page` width exactly as they used to. The
 * 2-column grouping happens WITHIN this width via {@link SettingsGrid}.
 * Renders an optional intro paragraph + right-aligned actions above the
 * sections.
 *
 * `wide` is retained for call-site compatibility but no longer changes the
 * width — every section now uses the same full width.
 */
export function SettingsLayout({ intro, children, wide: _wide = false, actions, header }: {
  intro?: ReactNode;
  children: ReactNode;
  wide?: boolean;
  /** Optional right-aligned header content (e.g. a status badge). */
  actions?: ReactNode;
  /** Full section-identity banner (see {@link SectionHeader}). When set it
   *  replaces the plain intro/actions row and sits OUTSIDE the staggered
   *  stage so it lands first and the cards cascade beneath it. */
  header?: ReactNode;
}) {
  return (
    <div className="p-5">
      {/* Full width by owner decree: no centered cap — wide viewports get
          MULTI-COLUMN section layouts (provider grid, SettingsGrid pairs)
          instead of empty margins. Individual cards keep their own internal
          max-widths where line length matters. */}
      {header ? <div className="settings-header-wrap mb-4">{header}</div> : null}
      {/* `settings-stage` cascades the section cards in on each sub-nav
          change (the parent re-keys on `section`, so this re-fires). Each
          direct child gets its stagger delay from a CSS :nth-child rule, so
          no per-child --i markup is needed. */}
      <div className="settings-stage flex w-full flex-col gap-4">
        {intro || actions ? (
          <div className="flex flex-wrap items-start justify-between gap-3">
            {intro ? <p className="max-w-3xl text-[13px] leading-relaxed text-secondary">{intro}</p> : <span />}
            {actions}
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
}

/**
 * Responsive 2-column grid for grouping related cards side by side. Cards
 * collapse to a single column on narrow viewports. `items-start` keeps each
 * card sized to its own content (no stretched-to-tallest cards). Use for
 * groups of independent setting cards; keep wide controls (tables, the
 * template editor, long path inputs) outside the grid so they span full
 * width.
 */
export function SettingsGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={cn('grid grid-cols-1 items-start gap-4 lg:grid-cols-2', className)}>
      {children}
    </div>
  );
}

// ── Section identity ────────────────────────────────────────────────
// Each of the 8 Settings sections gets a strong, consistent header: a
// large featured icon, a title, a one-line purpose, and an optional live
// status summary on the right (e.g. "3 of 5 providers connected"). This is
// the "distinct room" treatment — purely presentational, no setting keys.

export type StatusTone = 'connected' | 'warning' | 'error' | 'neutral' | 'accent';

const STATUS_TONE: Record<StatusTone, { dot: string; text: string; ring: string }> = {
  connected: { dot: 'var(--color-fg-success-primary)', text: 'text-success-primary', ring: 'border-[var(--color-border-brand)]' },
  warning:   { dot: 'var(--color-fg-warning-primary)', text: 'text-warning-primary',  ring: 'border-[var(--conf-mid-32)]' },
  error:     { dot: 'var(--color-fg-error-primary)',   text: 'text-error-primary',  ring: 'border-[var(--conf-low-32)]' },
  accent:    { dot: 'var(--color-fg-brand-primary)',   text: 'text-brand-secondary',    ring: 'border-[var(--color-border-brand)]' },
  neutral:   { dot: 'var(--color-text-tertiary)',      text: 'text-tertiary',  ring: 'border-[var(--border-2)]' },
};

/**
 * A small status pill with a leading dot. When `tone` is `connected` /
 * `accent` the dot breathes (folds under reduced-motion). Used as the live
 * summary chip in {@link SectionHeader} and inline status badges.
 */
export function StatusPill({ tone, children, breathe = false }: {
  tone: StatusTone;
  children: ReactNode;
  breathe?: boolean;
}) {
  const t = STATUS_TONE[tone];
  const alive = breathe && (tone === 'connected' || tone === 'accent');
  return (
    <span className={cn(
      'settings-status-pill inline-flex shrink-0 items-center gap-2 rounded-full border bg-secondary px-3 py-1.5 text-[12px] font-semibold',
      t.ring, t.text,
    )}>
      <span
        className={cn('size-1.5 rounded-full', alive && 'settings-dot-live')}
        style={{ background: t.dot, boxShadow: `0 0 8px ${t.dot}` }}
      />
      {children}
    </span>
  );
}

/**
 * Strong, consistent section identity banner. Replaces the bare intro
 * paragraph at the top of each section with a featured icon + title +
 * one-line purpose, plus an optional live `status` summary and a `filter`
 * affordance on the right. Sections feel like distinct rooms instead of one
 * endless wall of cards.
 */
export function SectionHeader({ icon, title, purpose, status, filter, accent = false }: {
  icon: ReactNode;
  title: ReactNode;
  purpose: ReactNode;
  /** Live status summary chip (e.g. provider/path/integration state). */
  status?: ReactNode;
  /** Optional right-aligned filter input (see {@link SettingsFilter}). */
  filter?: ReactNode;
  /** Brand-tinted icon treatment for the section's flagship (Naming). */
  accent?: boolean;
}) {
  return (
    <div className="settings-section-header flex flex-wrap items-start gap-4 rounded-2xl border border-[var(--border-2)] bg-secondary px-4 py-4 shadow-[var(--shadow-1)]">
      <span className={cn('settings-section-icon grid size-11 shrink-0 place-items-center rounded-xl [&_svg]:size-[22px]', accent ? 'settings-section-icon-accent' : '')}>
        {icon}
      </span>
      <div className="min-w-0 flex-1">
        <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-primary">{title}</h2>
        <p className="mt-1 max-w-2xl text-[13px] leading-relaxed text-secondary">{purpose}</p>
      </div>
      {(status || filter) ? (
        <div className="flex shrink-0 flex-wrap items-center gap-2.5 self-center">
          {filter}
          {status}
        </div>
      ) : null}
    </div>
  );
}

/**
 * Local-state-only filter input. Highlights/hides SettingRows within the
 * current section by matching `query` against their label/desc text — see
 * the `[data-search]` attribute SettingRow stamps and the CSS filter rules.
 * Purely cosmetic: never touches the save plumbing.
 */
export function SettingsFilter({ value, onChange, placeholder = 'Filter settings…' }: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="settings-filter relative">
      <IcSearch className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-tertiary" aria-hidden="true" />
      <input
        type="text"
        value={value}
        onChange={e => onChange(e.target.value)}
        placeholder={placeholder}
        spellCheck={false}
        aria-label="Filter settings in this section"
        className="settings-filter-input h-9 w-[200px] max-w-full rounded-full border border-[var(--border-2)] bg-secondary pl-8 pr-8 text-[12.5px] text-primary outline-none placeholder:text-tertiary"
      />
      {value ? (
        <button
          type="button"
          onClick={() => onChange('')}
          aria-label="Clear filter"
          className="absolute right-2.5 top-1/2 grid size-5 -translate-y-1/2 place-items-center rounded-full text-tertiary transition-colors hover:bg-white/[0.07] hover:text-primary [&_svg]:size-3"
        >
          <IcX />
        </button>
      ) : null}
    </div>
  );
}

/**
 * A settings card with the standard header (featured icon + title +
 * description) and a divider above its body. Pass `tone="danger"` for
 * the red danger-zone treatment. `headerExtra` renders a status badge /
 * toggle on the right of the header row; `action` renders an inline
 * button to the right of the title (used by the cleanup breadcrumb).
 */
export function SectionCard({
  icon,
  title,
  desc,
  headerExtra,
  action,
  children,
  tone = 'default',
  tint,
}: {
  icon: ReactNode;
  title: ReactNode;
  desc?: ReactNode;
  headerExtra?: ReactNode;
  action?: ReactNode;
  children?: ReactNode;
  tone?: 'default' | 'danger';
  /** Optional hex/CSS-var icon tint (defaults to the neutral gray treatment). */
  tint?: string;
}) {
  const danger = tone === 'danger';
  return (
    <div className={cn(
      'p-4',
      danger
        ? 'rounded-2xl border border-[var(--conf-low-32)] bg-error-secondary'
        : SETTINGS_CARD,
    )}>
      <div className={cn(
        'flex items-start gap-3',
        children != null && 'border-b pb-3.5',
        danger ? 'border-[var(--conf-low-24)]' : SETTINGS_DIVIDER,
      )}>
        <FeaturedIcon size="md" color={danger ? 'error' : tint ? undefined : 'gray'} tint={danger ? undefined : tint} icon={icon} />
        <div className="min-w-0 flex-1">
          <div className={cn('text-[15px] font-semibold', danger ? 'text-error-primary' : 'text-primary')}>{title}</div>
          {desc ? <div className="mt-1 text-[12.5px] leading-relaxed text-secondary">{desc}</div> : null}
        </div>
        {action}
        {headerExtra}
      </div>
      {children != null ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}

/**
 * A labelled control row. Two layouts:
 *   - `inline` (default): label on the left, control on the right —
 *     for compact controls (toggle, small select, number).
 *   - `stacked`: label above, control below full width — for wide
 *     controls (segmented controls, path fields).
 */
// Flatten a ReactNode label/desc to plain lowercase text so the per-section
// filter can match against it. Best-effort: strings + nested children only
// (good enough for the human-readable label/desc copy we pass).
function nodeText(node: ReactNode): string {
  if (node == null || node === false || node === true) return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(nodeText).join(' ');
  if (typeof node === 'object' && 'props' in (node as { props?: { children?: ReactNode } })) {
    return nodeText((node as { props?: { children?: ReactNode } }).props?.children);
  }
  return '';
}

export function SettingRow({ label, desc, children, layout = 'inline', disabled = false, control, settingKeys }: {
  label: ReactNode;
  desc?: ReactNode;
  children?: ReactNode;
  /** The control element (alias for children; either works). */
  control?: ReactNode;
  layout?: 'inline' | 'stacked';
  disabled?: boolean;
  /** Settings key(s) this row's control owns (dotted, e.g. `rename.mode`).
   *  Stamped as `data-setting-keys` so the per-section filter can force the row
   *  visible while it's dirty — an unsaved control must never be hidden by a
   *  narrower query, or Save would persist a change the user can no longer see.
   *  Also lets the unsaved-changes bar name what's pending. */
  settingKeys?: string | string[];
}) {
  const node = control ?? children;
  // Stamp searchable text so SettingsFilter can scope-highlight this row. The
  // attribute is inert when no filter is active.
  const search = `${nodeText(label)} ${nodeText(desc)}`.toLowerCase().trim();
  // Space-joined owned keys (dotted ids never contain spaces) read back via
  // `dataset.settingKeys`; the plain label feeds the save bar's pending list.
  const keys = (Array.isArray(settingKeys) ? settingKeys : settingKeys ? [settingKeys] : []).join(' ');
  const labelText = nodeText(label).trim();
  if (layout === 'stacked') {
    return (
      <div className={cn('setting-row', disabled && 'opacity-50')} data-search={search} data-setting-keys={keys || undefined} data-setting-label={keys ? labelText : undefined}>
        <div className="text-[13.5px] font-medium text-primary">{label}</div>
        {desc ? <div className="mt-0.5 text-[12.5px] leading-relaxed text-secondary">{desc}</div> : null}
        <div className="mt-2.5">{node}</div>
      </div>
    );
  }
  return (
    <div className={cn('setting-row flex items-start justify-between gap-4', disabled && 'opacity-50')} data-search={search} data-setting-keys={keys || undefined} data-setting-label={keys ? labelText : undefined}>
      <div className="min-w-0">
        <div className="text-[13.5px] font-medium text-primary">{label}</div>
        {desc ? <div className="mt-0.5 text-[12.5px] leading-relaxed text-secondary">{desc}</div> : null}
      </div>
      <div className="shrink-0">{node}</div>
    </div>
  );
}

/**
 * Inline label + control row with a fixed-width label column, so a stack
 * of fields (URL / token / key) lines up its inputs. The control is passed
 * as children and typically fills the remaining width
 * (`wrapperClassName="flex-1"` on the Input). Replaces the per-section
 * `fieldRow` render helpers that hand-rolled this same flex row.
 */
export function FieldRow({ label, children, labelWidth = 'w-20' }: {
  label: ReactNode;
  children: ReactNode;
  /** Tailwind width class for the label column (e.g. `w-24`). */
  labelWidth?: string;
}) {
  return (
    // Rendered as a <label> so the text implicitly labels its single control
    // (input/select) for screen readers and click-to-focus. Stacks above the
    // control on phones so a fixed label column can't strangle monospace
    // URL/key inputs (a 96px label left ~240px on a 360px screen); switches to
    // the aligned inline row at sm+.
    <label className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
      <span className={cn('shrink-0 text-[13px] font-medium text-secondary', labelWidth)}>{label}</span>
      {children}
    </label>
  );
}

/** Inset/nested box used for sub-settings under a parent toggle. */
export function NestedBox({ children, className, dimmed = false }: {
  children: ReactNode;
  className?: string;
  dimmed?: boolean;
}) {
  return (
    <div className={cn('p-3.5', SETTINGS_NESTED, dimmed && 'opacity-50', className)}>
      {children}
    </div>
  );
}

/**
 * Themed range slider with a label and live value readout. Replaces the
 * bare `<input type="range">` blocks the confidence + threshold sections
 * hand-rolled. `color` tints the track/value; `valueLabel` formats the
 * readout (e.g. `≥ 95%`).
 */
export function SliderField({
  label, value, min, max, step = 1, onChange, disabled = false,
  color = 'var(--accent)', valueLabel, dot,
}: {
  label: ReactNode;
  value: number;
  min: number;
  max: number;
  step?: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  color?: string;
  /** Formatted readout, e.g. `≥ 95%`. Defaults to the raw value. */
  valueLabel?: ReactNode;
  /** Optional leading swatch color for confidence-bucket rows. */
  dot?: string;
}) {
  return (
    <div className={cn('flex items-center gap-3', disabled && 'opacity-50')}>
      <span className="inline-flex w-20 shrink-0 items-center gap-2 text-[13px] font-medium text-primary">
        {dot ? <span className="size-2 rounded-full" style={{ background: dot }} /> : null}
        {label}
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={e => onChange(+e.target.value)}
        className="h-1.5 flex-1 cursor-pointer disabled:cursor-not-allowed"
        style={{ accentColor: color }}
        aria-label={typeof label === 'string' ? label : undefined}
        aria-valuetext={typeof valueLabel === 'string' ? valueLabel : String(value)}
      />
      <span className="w-16 shrink-0 text-right font-mono text-[12.5px] font-semibold" style={{ color }}>
        {valueLabel ?? value}
      </span>
    </div>
  );
}

/** Compact number input that clamps to [min, max] and reports clean values. */
export function NumberField({ value, min, max, step = 1, onChange, className, suffix }: {
  value: number;
  min?: number;
  max?: number;
  step?: number;
  onChange: (v: number) => void;
  className?: string;
  /** Optional trailing unit label, e.g. `sec`. */
  suffix?: ReactNode;
}) {
  return (
    <Input
      wrapperClassName={cn('w-28', className)}
      mono
      type="number"
      min={min}
      max={max}
      step={step}
      value={value}
      trailing={suffix ? <span className="shrink-0 select-none text-[12px] font-medium text-tertiary">{suffix}</span> : undefined}
      onChange={e => {
        const n = Number(e.target.value);
        if (!Number.isFinite(n)) return;
        let next = n;
        if (min != null) next = Math.max(min, next);
        if (max != null) next = Math.min(max, next);
        onChange(next);
      }}
    />
  );
}

type FieldKind = 'text' | 'password' | 'select' | 'toggle';

export interface ProviderFieldProps {
  kind?: FieldKind;
  label: string;
  value?: string;
  placeholder?: string;
  options?: string[];
  mono?: boolean;
  desc?: string;
  onSave?: (next: string | boolean) => void;
  /** Grey out + block the control when its prerequisite isn't met (e.g. a
   *  toggle whose API key isn't configured). `disabledReason` is shown beneath
   *  the label so the user knows WHY and how to fix it. */
  disabled?: boolean;
  disabledReason?: string;
}

export function ProviderField({ kind = 'text', label, value, placeholder, options, mono, desc, onSave, disabled = false, disabledReason }: ProviderFieldProps) {
  const [text, setText] = useState(value ?? '');
  const [on, setOn] = useState(value !== 'false');
  const [show, setShow] = useState(false);

  // Re-sync local `text` when the upstream `value` arrives late (the field
  // mounts before rawSettings hydrates). Only adopt the server value while the
  // field is empty so we never clobber an in-progress edit.
  useEffect(() => {
    if (text === '' && value) setText(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  // Keep the toggle in sync when `value` hydrates/changes late. Previously
  // `on` seeded once from the initial (often empty) value and never updated,
  // so a toggle field could render stale once settings finished loading.
  useEffect(() => { setOn(value !== 'false'); }, [value]);

  const labelBlock = (
    <div className={disabled ? 'opacity-60' : undefined}>
      <div className="text-[13px] font-medium text-primary">{label}</div>
      {desc ? <div className="mt-0.5 text-[11.5px] leading-relaxed text-tertiary">{desc}</div> : null}
      {disabled && disabledReason ? (
        <div className="mt-0.5 text-[11px] leading-relaxed text-warning-primary">{disabledReason}</div>
      ) : null}
    </div>
  );

  if (kind === 'toggle') {
    return (
      <div className="flex items-center justify-between gap-4">
        {labelBlock}
        {/* A prerequisite-blocked toggle reads OFF (not its saved/default-on
            state) — "on but greyed" looks active yet does nothing. */}
        <Toggle isSelected={on && !disabled} isDisabled={disabled} onChange={() => { const next = !on; setOn(next); onSave?.(next); }} aria-label={label} />
      </div>
    );
  }

  if (kind === 'select') {
    return (
      <div>
        <div className="mb-1.5">{labelBlock}</div>
        <Select<string>
          value={value ?? null}
          disabled={disabled}
          onChange={v => onSave?.(v)}
          options={(options ?? []).map(o => ({ value: o, label: o }))}
          placeholder={placeholder}
        />
      </div>
    );
  }

  const isPassword = kind === 'password';
  return (
    <div>
      <div className="mb-1.5">{labelBlock}</div>
      <Input
        mono={mono}
        editGate
        type={isPassword && !show ? 'password' : 'text'}
        value={text}
        aria-label={label}
        disabled={disabled}
        onChange={e => setText(e.target.value)}
        onBlur={() => {
          if (text === value) return;
          // Don't clear a configured secret by blurring a blank field. A masked
          // secret's prop value is '' after a refresh, so an empty blur there is
          // spurious (mirrors the backend empty-secret guard); a password is never
          // cleared by blanking. A real value still passes through.
          if (isPassword && text.trim() === '') return;
          if (text.trim() === '' && (value ?? '') === '') return;
          onSave?.(text);
        }}
        placeholder={placeholder}
        // Off for ALL credential fields (not just passwords) — these API-key
        // `text` inputs were the ones browser autofill silently overwrote.
        autoComplete="off"
        trailing={isPassword ? (
          <button
            type="button"
            onClick={() => setShow(s => !s)}
            title={show ? 'Hide' : 'Show'}
            aria-label={show ? 'Hide value' : 'Show value'}
            className="grid size-6 shrink-0 place-items-center rounded-md text-tertiary transition-colors hover:bg-white/[0.07] hover:text-primary [&_svg]:size-[14px]"
          >
            {show ? <IcEyeOff /> : <IcEye />}
          </button>
        ) : undefined}
      />
    </div>
  );
}

interface ProviderBlockProps {
  providerKey: ProviderKey;
  fields?: ProviderFieldProps[];
  defaultOpen?: boolean;
  status?: 'connected' | 'warning' | 'error' | 'disabled' | 'coming-soon' | 'not-configured';
  rateLimit?: string;
  warning?: string;
  /** Returns `true` on a verified connection (drives the success pulse).
   *  A void / falsy result just means "no celebration". */
  onTest?: () => void | boolean | Promise<void | boolean>;
  /** Unix timestamp (seconds) when the provider's ban expires. When set
   *  AND > now, the block renders a live countdown banner. AniDB only. */
  bannedUntil?: number | null;
  /** Provider keys this one falls back to when unavailable. Surfaced as
   *  a tooltip-style hint so the user understands "if AniDB is banned,
   *  TVDB takes over." */
  fallbackChain?: string[] | null;
}

/**
 * Live ban-countdown banner. Renders inside a provider block when the
 * provider is throttled / banned (currently only AniDB has this state).
 * Returns null when the ban has expired so the banner auto-dismisses
 * without a manual refresh.
 */
function BanCountdownBanner({
  unixSec,
  fallbackChain,
}: { unixSec: number; fallbackChain?: string[] | null }) {
  const remaining = useCountdown(unixSec);
  if (!remaining) return null;
  // Reconstruct the local time of expiry for the secondary "at HH:MM"
  // hint — useful when the wait is hours so the user can plan around it.
  const at = new Date(unixSec * 1000);
  const atStr = at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const fallback = fallbackChain && fallbackChain.length > 0
    ? `Kira is using ${fallbackChain.map(k => k.toUpperCase()).join(' → ')} in the meantime.`
    : 'New matches against this provider will fail until then.';
  return (
    <Alert
      color="warning"
      icon={IcAlertTri}
      title={`Rate-limited — unbans in ${remaining} (at ${atStr})`}
    >
      {fallback}
    </Alert>
  );
}

function useCountdown(unixSec: number | null | undefined): string | null {
  // Re-renders every 30s while the deadline is in the future.
  const [, tick] = useState(0);
  useEffect(() => {
    if (!unixSec) return;
    const remaining = unixSec * 1000 - Date.now();
    if (remaining <= 0) return;
    const t = setInterval(() => tick(t => t + 1), 30_000);
    return () => clearInterval(t);
  }, [unixSec]);
  if (!unixSec) return null;
  const remainingMs = unixSec * 1000 - Date.now();
  if (remainingMs <= 0) return null;
  const totalMin = Math.ceil(remainingMs / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h > 0) return `${h}h ${m}m`;
  return `${m}m`;
}

// Map a provider's icon slug (from PROVIDERS metadata) to an icon component.
const PROVIDER_ICON: Record<string, FC<{ className?: string }>> = {
  film: IcFilm, tv: IcTv, anime: IcAnime, disc: IcDisc, waveform: IcWaveform, caption: IcCaption,
};

// Real provider brand logos now live in frontend/public/providers/. When a
// provider has one (PROVIDERS[k].logo), the card renders the full-colour logo
// on a clean white tile (so marks read on the dark UI); otherwise it falls back
// to the brand-tinted glyph above.
export function ProviderLogo({ src, size = 'md' }: { src: string; size?: 'sm' | 'md' }) {
  return (
    <span className={cn(
      'grid shrink-0 place-items-center overflow-hidden bg-white ring-1 ring-inset ring-black/10',
      size === 'md' ? 'size-9 rounded-lg' : 'size-7 rounded-md',
    )}>
      <img src={src} alt="" aria-hidden loading="lazy" className={cn('object-contain', size === 'md' ? 'size-[26px]' : 'size-[19px]')} />
    </span>
  );
}

export function ProviderCard({ providerKey, fields = [], defaultOpen = false, status = 'connected', warning, onTest, bannedUntil, fallbackChain }: ProviderBlockProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [testing, setTesting] = useState(false);
  // "Last tested" feel — remember a verified test so the status badge reads
  // "Verified". Local + transient (resets on navigation).
  const [testedAt, setTestedAt] = useState<number | null>(null);
  const p = PROVIDERS[providerKey];
  if (!p) return null;

  const Icon = PROVIDER_ICON[p.icon] ?? IcFilm;

  // F-06: clearer labels for the discovered states. "Coming soon"
  // distinguishes "we haven't built this yet" from "you need a key";
  // "Not configured" is for implemented providers awaiting credentials.
  const statusLabel =
    status === 'connected' ? 'Connected' :
    status === 'warning' ? 'Rate-limited' :
    status === 'error' ? 'Error' :
    status === 'coming-soon' ? 'Coming soon' :
    status === 'not-configured' ? 'Not configured' : 'Disabled';
  // Provider state → UUI BadgeWithDot colour (the dot carries the meaning).
  const badgeColor: 'success' | 'warning' | 'error' | 'gray' =
    status === 'connected' ? 'success' :
    status === 'warning' ? 'warning' :
    status === 'error' ? 'error' : 'gray';
  // A connected card reads "wired up" at a glance via a faint green ring.
  const live = status === 'connected';

  const handleTest = async () => {
    if (!onTest) return;
    setTesting(true);
    try {
      const ok = await onTest();
      if (ok === true) setTestedAt(Date.now());
    } finally { setTesting(false); }
  };

  return (
    <div
      className="overflow-hidden rounded-xl bg-secondary shadow-xs transition-shadow"
      // Inset 1px ring as the border — neutral by default, faint green when the
      // provider is connected. Inline (not a Tailwind ring class) so the
      // color-mix tint stays valid CSS.
      style={{ boxShadow: live
        ? 'inset 0 0 0 1px color-mix(in srgb, var(--conf-high) 40%, transparent)'
        : 'inset 0 0 0 1px var(--color-border-secondary)' }}
    >
      {/* Header = two toggle regions (the main info area + the status/chevron),
          either of which expands/collapses the card. The Test action lives in
          the expanded body footer, not here, so the header stays clean. */}
      <div className="flex w-full items-center gap-3 p-4">
        <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={() => setOpen(o => !o)}>
          {p.logo ? <ProviderLogo src={p.logo} size="md" /> : <FeaturedIcon size="md" tint={p.color} icon={<Icon />} />}
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-sm font-semibold text-primary">{p.name}</span>
              {p.for.map(t => <Badge key={t}>{t}</Badge>)}
            </div>
            <div className="mt-0.5 truncate text-[12px] text-tertiary">{p.desc}</div>
          </div>
        </button>
        <button type="button" className="flex shrink-0 items-center gap-2.5" onClick={() => setOpen(o => !o)} aria-label={open ? 'Collapse' : 'Expand'}>
          <BadgeWithDot color={badgeColor} pulse={live}>{testedAt ? 'Verified' : statusLabel}</BadgeWithDot>
          <IcChevDown className={cn('size-4 shrink-0 text-tertiary transition-transform duration-200', open && 'rotate-180')} />
        </button>
      </div>

      {/* CSS-only collapse via grid-rows 0fr→1fr — avoids motion animating
          `height` (a per-frame React render + layout) across every provider
          card. The body always mounts but is clipped when collapsed. */}
      <div className={cn('grid transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none', open ? 'grid-rows-[1fr]' : 'grid-rows-[0fr]')}>
        <div className="overflow-hidden">
            <div className="flex flex-col gap-3.5 border-t border-secondary px-4 py-4">
              {/* Ban countdown — only renders while bannedUntil is set + future. */}
              {bannedUntil ? <BanCountdownBanner unixSec={bannedUntil} fallbackChain={fallbackChain} /> : null}
              {warning ? <Alert color="warning" icon={IcAlertTri}>{warning}</Alert> : null}
              {fallbackChain && fallbackChain.length > 0 ? (
                <Alert color="info">
                  <strong className="text-secondary">Fallback chain:</strong> if unavailable, Kira tries{' '}
                  {fallbackChain.map((k, i) => (
                    <span key={k}>{i > 0 ? ' → ' : ''}<span className="text-secondary">{k.toUpperCase()}</span></span>
                  ))}{' '}in order.
                </Alert>
              ) : null}

              {fields.length > 0 ? (
                <div className="flex flex-col gap-3.5">
                  {fields.map((f, i) => <ProviderField key={i} {...f} />)}
                </div>
              ) : null}

              {/* Test the connection — a clean footer action, right-aligned. */}
              {onTest ? (
                <div className="flex justify-end pt-0.5">
                  <Button color="secondary" size="sm" iconLeading={IcRefresh} isLoading={testing} showTextWhileLoading onClick={handleTest}>
                    Test
                  </Button>
                </div>
              ) : null}
            </div>
        </div>
      </div>
    </div>
  );
}

// Jinja2 ({{ }}) versions of the built-in profiles — mirror backend
// DEFAULT_PROFILES. Kept LOCAL (not the shared NAMING_PROFILES, which stays
// {token} so the rename-modal preview's formatPath keeps working) and used
// as editor seeds + the input to the real backend live preview.
const JINJA_PROFILES: Record<string, Record<MediaType, string>> = {
  Plex: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}} [{{q}}].{{x}}',
    tv:    '{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{q}}].{{x}}',
    anime: '{{n}}/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}} [{{rg}}].{{x}}',
    music: '{{artist}}/{{album}} ({{y}})/{{tn}}{{variant}} - {{title}}.{{x}}',
  },
  Jellyfin: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}}.{{x}}',
    tv:    '{{n}} ({{y}})/Season {{s2}}/{{n}} ({{y}}) - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}',
    anime: '{{n}} ({{y}})/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}{{variant}} - {{t}}.{{x}}',
    music: '{{artist}}/{{album}}/{{tn}}{{variant}} {{title}}.{{x}}',
  },
  Kodi: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}){{variant}} - {{q}}.{{x}}',
    tv:    '{{n}}/Season {{s2}}/{{n}}.S{{s2}}E{{e2}}{{variant}}.{{t}}.{{x}}',
    anime: '{{n}}/S{{s2}}/{{n}} - {{abs}}{{variant}} - {{t}}.{{x}}',
    music: '{{artist}} - {{album}}/{{tn}}{{variant}}. {{title}}.{{x}}',
  },
  Custom: {
    movie: '{{n}} ({{y}})/{{n}} ({{y}}).{{x}}',
    tv:    '{{n}}/Season {{s2}}/{{n}} - S{{s2}}E{{e2}}.{{x}}',
    anime: '{{n}}/{{n}} - {{abs}} [{{rg}}].{{x}}',
    music: '{{artist}} - {{album}}/{{tn}}. {{title}}.{{x}}',
  },
};

// Complete token reference per media type ({{ }} syntax). Mirrors every
// token the backend _build_ctx provides so the palette is a true reference,
// not a subset. Filters (| pad, | ascii, | roman, | clean, | sortName,
// | upperInitial, | acronym, plus Jinja's | upper/lower/replace/default)
// are documented in TOKEN_FILTERS below.
const TOKEN_CHIPS: Record<MediaType, { k: string; d: string }[]> = {
  movie: [
    { k: '{{n}}', d: 'Title' }, { k: '{{y}}', d: 'Year' }, { k: '{{ny}}', d: 'Title (Year)' },
    { k: '{{decade}}', d: '1990s' }, { k: '{{x}}', d: 'Ext' }, { k: '{{q}}', d: 'Quality' },
    { k: '{{resolution}}', d: '1080p' }, { k: '{{source}}', d: 'BluRay/WEB' }, { k: '{{vc}}', d: 'Video codec' },
    { k: '{{ac}}', d: 'Audio codec' }, { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' },
    { k: '{{bitdepth}}', d: '10bit' }, { k: '{{edition}}', d: 'Edition' }, { k: '{{variant}}', d: 'Variant suffix' },
    { k: '{{director}}', d: 'Director' }, { k: '{{cast}}', d: 'Cast' }, { k: '{{genres}}', d: 'Genres' },
    { k: '{{genre}}', d: 'First genre' }, { k: '{{studio}}', d: 'Studio' }, { k: '{{country}}', d: 'Country' },
    { k: '{{runtime}}', d: 'Minutes' }, { k: '{{gigabytes}}', d: 'Size (GB)' },
    { k: '{{tmdbid}}', d: 'TMDB id' }, { k: '{{imdbid}}', d: 'IMDb id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  tv: [
    { k: '{{n}}', d: 'Series' }, { k: '{{y}}', d: 'Year' }, { k: '{{s2}}', d: 'Season (00)' },
    { k: '{{e2}}', d: 'Episode (00)' }, { k: '{{e2end}}', d: 'End ep (ranges)' }, { k: '{{s00e00}}', d: 'S01E05' },
    { k: '{{sxe}}', d: '1x05' }, { k: '{{t}}', d: 'Ep title' }, { k: '{{airdate}}', d: 'Air date' },
    { k: '{{q}}', d: 'Quality' }, { k: '{{resolution}}', d: '1080p' }, { k: '{{vc}}', d: 'Video codec' },
    { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' }, { k: '{{variant}}', d: 'Variant suffix' },
    { k: '{{network}}', d: 'Network' }, { k: '{{studio}}', d: 'Studio' }, { k: '{{genres}}', d: 'Genres' },
    { k: '{{yearrange}}', d: '2022 – 2024' }, { k: '{{tvdbid}}', d: 'TVDB id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  anime: [
    { k: '{{n}}', d: 'Series' }, { k: '{{s2}}', d: 'Season' }, { k: '{{e2}}', d: 'Episode' },
    { k: '{{abs}}', d: 'Absolute (000)' }, { k: '{{s00e00}}', d: 'S01E05' }, { k: '{{t}}', d: 'Ep title' },
    { k: '{{rg}}', d: 'Group' }, { k: '{{group}}', d: 'Group (blank)' }, { k: '{{cour}}', d: 'Cour #' },
    { k: '{{vc}}', d: 'Video codec' }, { k: '{{channels}}', d: 'Audio ch (5.1)' }, { k: '{{hdr}}', d: 'HDR' },
    { k: '{{bitdepth}}', d: '10bit' }, { k: '{{variant}}', d: 'Audio/edition' }, { k: '{{studio}}', d: 'Studio' },
    { k: '{{genres}}', d: 'Genres' }, { k: '{{anidbid}}', d: 'AniDB id' }, { k: '{{plex}}', d: 'Full Plex path' },
  ],
  music: [
    { k: '{{artist}}', d: 'Artist' }, { k: '{{album}}', d: 'Album' }, { k: '{{y}}', d: 'Year' },
    { k: '{{decade}}', d: '1990s' }, { k: '{{tn}}', d: 'Track # (02)' }, { k: '{{title}}', d: 'Track title' },
    { k: '{{label}}', d: 'Label' }, { k: '{{genres}}', d: 'Genres' }, { k: '{{x}}', d: 'Ext' },
  ],
};

// Reusable filters (advanced string helpers + Jinja built-ins). Shown as a hint under
// the token palette so users can discover them. e.g. `{{ n | upper }}`,
// `{{ episode | pad(3) }}`, `{{ n | acronym }}`.
const TOKEN_FILTERS = 'pad(n) · ascii · roman · clean · sortName · upperInitial · acronym · upper · lower · replace(a,b) · default(x)';

// ── Template ⇄ chip parsing ─────────────────────────────────────────
// The naming template is a Jinja-ish STRING that must round-trip BYTE-FOR-BYTE
// (the backend engine + live preview consume it). The chip editor below is a
// pure VIEW over that string: we parse string → segments to render, and
// serialize the contentEditable DOM → string on every edit. The string in
// React state stays the single source of truth.

type TplSeg =
  | { kind: 'text'; value: string }
  // A {{ ... }} token. `name` is the leading identifier (e.g. `n`, `episode`),
  // `filters` the pipe filter chain text (e.g. `upper`, `pad(3)`). `raw` is the
  // exact original span so serialization is loss-less.
  | { kind: 'token'; raw: string; name: string; filters: string[] }
  // A {% ... %} Jinja statement (if / endif / for …). Rendered as a muted,
  // non-editable chip; we never try to make these fancy — just don't corrupt.
  | { kind: 'stmt'; raw: string; body: string };

// Split a template string into ordered segments. Matches both {{ }} and {% %};
// everything else is literal text. Greedy-but-minimal: `[^}]` / `[^%]` style
// inner matching keeps a stray brace in literal text from swallowing the rest.
const TPL_SPLIT_RE = /(\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\})/g;

function parseTemplate(tpl: string): TplSeg[] {
  const segs: TplSeg[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  TPL_SPLIT_RE.lastIndex = 0;
  while ((m = TPL_SPLIT_RE.exec(tpl)) !== null) {
    if (m.index > last) segs.push({ kind: 'text', value: tpl.slice(last, m.index) });
    const raw = m[0];
    if (raw.startsWith('{{')) {
      const inner = raw.slice(2, -2).trim();
      const parts = inner.split('|').map(p => p.trim());
      const name = parts[0] ?? '';
      const filters = parts.slice(1).filter(Boolean);
      segs.push({ kind: 'token', raw, name, filters });
    } else {
      segs.push({ kind: 'stmt', raw, body: raw.slice(2, -2).trim() });
    }
    last = m.index + raw.length;
  }
  if (last < tpl.length) segs.push({ kind: 'text', value: tpl.slice(last) });
  return segs;
}

// Human label for a token chip: `{{ n | upper }}` → "n · upper". Pure display;
// the chip carries the exact raw span in a data attribute for serialization.
function tokenLabel(seg: Extract<TplSeg, { kind: 'token' }>): string {
  return seg.filters.length ? `${seg.name} · ${seg.filters.join(' · ')}` : seg.name;
}

// Build the contentEditable's inner DOM imperatively from a template string.
// Chips are contenteditable=false spans carrying their exact `raw` in
// data-raw; literal text becomes plain text nodes. We render into a fresh
// fragment so the caller can swap it in atomically. `editable` toggles the
// per-chip × delete affordance.
function buildEditorDom(tpl: string, editable: boolean): DocumentFragment {
  const frag = document.createDocumentFragment();
  for (const seg of parseTemplate(tpl)) {
    if (seg.kind === 'text') {
      // Even empty strings: skip — empty text nodes only confuse caret logic.
      if (seg.value) frag.appendChild(document.createTextNode(seg.value));
      continue;
    }
    const chip = document.createElement('span');
    chip.className = seg.kind === 'token' ? 'tpl-chip' : 'tpl-chip tpl-chip-stmt';
    chip.setAttribute('contenteditable', 'false');
    chip.setAttribute('data-raw', seg.raw);
    // Zero-width separators are NOT inserted; native contentEditable lets the
    // caret sit either side of a non-editable span on its own.
    const label = document.createElement('span');
    // `kbd` gives the same keycap look as the palette chips below; stmt chips
    // stay plain (no keycap) so {% … %} reads distinct from tokens.
    label.className = seg.kind === 'token' ? 'kbd tpl-chip-label' : 'tpl-chip-label';
    label.textContent = seg.kind === 'token' ? tokenLabel(seg) : seg.body;
    chip.appendChild(label);
    if (editable) {
      const del = document.createElement('span');
      del.className = 'tpl-chip-x';
      del.setAttribute('data-chip-x', '1');
      del.setAttribute('contenteditable', 'false');
      del.textContent = '×';
      chip.appendChild(del);
    }
    frag.appendChild(chip);
  }
  return frag;
}

// Serialize the contentEditable DOM back to the canonical template string.
// Chips contribute their exact stored `data-raw`; everything else contributes
// its text content. <br>/<div> (which browsers inject on Enter / paste) map to
// nothing / newline-free joins — the template is single-logical-line, so we
// flatten block boundaries to empty rather than emitting "\n".
function serializeEditor(root: HTMLElement): string {
  let out = '';
  const walk = (node: Node) => {
    node.childNodes.forEach(child => {
      if (child.nodeType === Node.TEXT_NODE) {
        out += child.textContent ?? '';
      } else if (child.nodeType === Node.ELEMENT_NODE) {
        const el = child as HTMLElement;
        if (el.dataset.raw != null) {
          out += el.dataset.raw; // chip → exact original span
        } else if (el.tagName === 'BR') {
          // ignore — wrapping is visual, not part of the string
        } else {
          walk(el); // descend into injected <div>/<span> wrappers
        }
      }
    });
  };
  walk(root);
  return out;
}

// Caret position as an offset into the SERIALIZED string (chips count as the
// full length of their raw span). Returns null if the selection isn't inside
// the editor. This lets us restore the caret precisely after a DOM rebuild,
// regardless of where in the template a token was typed.
function getCaretOffset(root: HTMLElement): number | null {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) return null;
  const { anchorNode, anchorOffset } = sel;
  if (!anchorNode || !root.contains(anchorNode)) return null;
  let offset = 0;
  let found = false;
  const walk = (node: Node): void => {
    if (found) return;
    if (node.nodeType === Node.TEXT_NODE) {
      if (node === anchorNode) { offset += anchorOffset; found = true; return; }
      offset += node.textContent?.length ?? 0;
      return;
    }
    const el = node as HTMLElement;
    if (el !== root && el.dataset?.raw != null) {
      // Caret resolved to the chip element itself (anchorNode is the chip):
      // anchorOffset 0 = before the chip, >=1 = after it.
      if (node === anchorNode) { offset += anchorOffset > 0 ? el.dataset.raw.length : 0; found = true; return; }
      offset += el.dataset.raw.length;
      return;
    }
    if (node === anchorNode) {
      // Caret in a container: count children before anchorOffset.
      for (let i = 0; i < anchorOffset && i < node.childNodes.length; i++) walk(node.childNodes[i]);
      found = true;
      return;
    }
    node.childNodes.forEach(walk);
  };
  walk(root);
  return found ? offset : null;
}

// Place the caret at a SERIALIZED-string offset within the freshly-rebuilt DOM.
// Chips are atomic: an offset landing inside a chip's raw span snaps to just
// after that chip.
function setCaretOffset(root: HTMLElement, target: number): void {
  const sel = window.getSelection();
  if (!sel) return;
  const range = document.createRange();
  let remaining = target;
  let placed = false;
  const children = Array.from(root.childNodes);
  for (const node of children) {
    if (node.nodeType === Node.TEXT_NODE) {
      const len = node.textContent?.length ?? 0;
      if (remaining <= len) { range.setStart(node, remaining); placed = true; break; }
      remaining -= len;
    } else {
      const el = node as HTMLElement;
      const len = el.dataset?.raw != null ? el.dataset.raw.length : 0;
      if (remaining <= len) {
        // Snap to just after this chip (atomic unit).
        range.setStartAfter(el);
        placed = true;
        break;
      }
      remaining -= len;
    }
  }
  if (!placed) { range.selectNodeContents(root); range.collapse(false); }
  range.collapse(true);
  sel.removeAllRanges();
  sel.addRange(range);
}

// Rich chip editor for one template string. contentEditable div: tokens render
// as inline chips ({{…}} and {%…%}), literal text stays editable, the field
// WRAPS to multiple lines (no horizontal scroll), and typed `{{ … | filter }}`
// auto-promotes to a chip on completion. Round-trips the string via onChange.
function TemplateChipEditor({
  value, editable, onChange, dropActive, registerInsert, ...dnd
}: {
  value: string;
  editable: boolean;
  onChange: (next: string) => void;
  dropActive: boolean;
  // Lets the parent (click-to-insert from the palette) inject a token at the
  // current caret. We expose an imperative insert via this ref-setter.
  registerInsert: (fn: ((token: string) => void) | null) => void;
  onDragOver?: (e: React.DragEvent) => void;
  onDragLeave?: (e: React.DragEvent) => void;
  onDrop?: (e: React.DragEvent) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  // Last string we wrote INTO the DOM. Lets us skip re-populating (which would
  // reset the caret) when an onChange-driven value update just echoes what the
  // DOM already contains.
  const domStringRef = useRef<string>('');

  // Read the DOM, normalize completed tokens into chips, and report the string.
  const syncFromDom = () => {
    const el = ref.current;
    if (!el) return;
    const str = serializeEditor(el);
    domStringRef.current = str;

    // Smart-typing: if a freshly-typed, completed `{{ … }}` / `{% … %}` now
    // lives inside a TEXT node, re-populate so it becomes a chip. We only do
    // this when a text node actually contains a complete token to avoid
    // needless re-renders (and caret jumps) on ordinary literal typing.
    const hasInlineToken = Array.from(el.childNodes).some(
      n => n.nodeType === Node.TEXT_NODE && /\{\{[\s\S]*?\}\}|\{%[\s\S]*?%\}/.test(n.textContent ?? ''),
    );
    // Capture caret as a string offset BEFORE we rebuild, so we can restore it
    // exactly (even when the new chip sits in the middle of the template).
    const caret = hasInlineToken ? getCaretOffset(el) : null;
    onChange(str);
    if (hasInlineToken) {
      repopulate(str, caret);
    }
  };

  // Rebuild the editor DOM from a string. When `caret` is a number we restore
  // the caret to that serialized-string offset (used after promoting a typed
  // token to a chip); otherwise we leave the selection alone (external value
  // changes where focus isn't ours, e.g. profile switch).
  const repopulate = (str: string, caret: number | null = null) => {
    const el = ref.current;
    if (!el) return;
    const frag = buildEditorDom(str, editable);
    el.replaceChildren(frag);
    domStringRef.current = str;
    if (caret != null) setCaretOffset(el, caret);
  };

  // Populate on mount + whenever the canonical value changes from the OUTSIDE
  // (profile switch, click-insert, drag-drop). Skip when the value already
  // equals what's in the DOM — that means the change originated from our own
  // typing and re-populating would thrash the caret.
  useEffect(() => {
    if (value === domStringRef.current) return;
    repopulate(value);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, editable]);

  // Imperative caret-aware insert for click-to-insert from the palette.
  useEffect(() => {
    if (!editable) { registerInsert(null); return; }
    registerInsert((token: string) => {
      const el = ref.current;
      if (!el) return;
      el.focus();
      const sel = window.getSelection();
      let range: Range;
      if (sel && sel.rangeCount > 0 && el.contains(sel.anchorNode)) {
        range = sel.getRangeAt(0);
        range.deleteContents();
      } else {
        range = document.createRange();
        range.selectNodeContents(el);
        range.collapse(false);
      }
      const node = document.createTextNode(token);
      range.insertNode(node);
      // Collapse caret to just after the inserted text, then sync (which will
      // promote the {{…}} text into a chip and restore the caret by offset).
      sel?.removeAllRanges();
      const after = document.createRange();
      after.setStartAfter(node);
      after.collapse(true);
      sel?.addRange(after);
      syncFromDom();
    });
    return () => registerInsert(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editable]);

  // Backspace/Delete at a chip edge removes the whole chip. Native
  // contentEditable already deletes a non-editable span as a unit in most
  // browsers, but we make it deterministic + also handle the × click.
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!editable) { e.preventDefault(); return; }
    if (e.key === 'Enter') {
      // Single logical line — don't let Enter inject <div>/<br>.
      e.preventDefault();
      return;
    }
    if (e.key === 'Backspace' || e.key === 'Delete') {
      const sel = window.getSelection();
      if (!sel || sel.rangeCount === 0 || !sel.isCollapsed) return; // let native handle selections
      const range = sel.getRangeAt(0);
      const el = ref.current;
      if (!el) return;
      // Find a chip immediately adjacent to the caret in the delete direction.
      let chip: HTMLElement | null = null;
      const { startContainer, startOffset } = range;
      if (e.key === 'Backspace') {
        if (startContainer.nodeType === Node.TEXT_NODE && startOffset > 0) return; // mid-text
        const prev = startContainer.nodeType === Node.TEXT_NODE
          ? startContainer.previousSibling
          : (startContainer.childNodes[startOffset - 1] ?? null);
        if (prev && (prev as HTMLElement).dataset?.raw != null) chip = prev as HTMLElement;
      } else {
        const len = startContainer.nodeType === Node.TEXT_NODE ? (startContainer.textContent?.length ?? 0) : 0;
        if (startContainer.nodeType === Node.TEXT_NODE && startOffset < len) return;
        const nextN = startContainer.nodeType === Node.TEXT_NODE
          ? startContainer.nextSibling
          : (startContainer.childNodes[startOffset] ?? null);
        if (nextN && (nextN as HTMLElement).dataset?.raw != null) chip = nextN as HTMLElement;
      }
      if (chip) {
        e.preventDefault();
        chip.remove();
        syncFromDom();
      }
    }
  };

  // Click the × on a chip removes that whole token.
  const onClick = (e: React.MouseEvent) => {
    if (!editable) return;
    const target = e.target as HTMLElement;
    if (target.dataset?.chipX != null) {
      const chip = target.closest('[data-raw]');
      if (chip) { e.preventDefault(); chip.remove(); syncFromDom(); }
    }
  };

  return (
    <div
      ref={ref}
      className={cn('tpl-editor mono', !editable && 'tpl-editor-locked', dropActive && 'tpl-editor-drop')}
      contentEditable={editable}
      suppressContentEditableWarning
      spellCheck={false}
      role="textbox"
      aria-multiline="true"
      onInput={editable ? syncFromDom : undefined}
      onKeyDown={onKeyDown}
      onClick={onClick}
      onDragOver={dnd.onDragOver}
      onDragLeave={dnd.onDragLeave}
      onDrop={dnd.onDrop}
    />
  );
}

// Seed the 4-type template set for a profile. For Custom, layer any saved
// custom templates over the built-in Custom defaults so unset types still
// render sensibly.
function seedTemplates(profile: string, savedCustom?: Record<string, string>): Record<MediaType, string> {
  if (profile === 'Custom') {
    return { ...JINJA_PROFILES.Custom, ...(savedCustom ?? {}) } as Record<MediaType, string>;
  }
  return { ...(JINJA_PROFILES[profile] ?? JINJA_PROFILES.Plex) };
}

// 4-tab naming template editor with a REAL live preview (rendered by the
// backend engine against the user's own files — see LiveTemplatePreview).
// Layout is a 2-pane editor | preview grid so the wide Settings column gets
// used: the editor (tabs + template + token palette) sits on the left, the
// live-rendered paths (which are long) get the full right pane.
//
// Custom-profile edits persist via onSaveCustom → backend `naming.custom.Custom`
// (the same JSON dict the rename engine reads at rename time). Built-in
// profiles are read-only.
export function NamingTemplateTabs({ profile, savedCustom, onSaveCustom }: {
  profile: string;
  savedCustom?: Record<string, string>;
  onSaveCustom?: (dict: Record<string, string>) => void;
}) {
  const tabs: { key: MediaType; label: string; icon: ReactNode }[] = [
    { key: 'movie', label: 'Movies', icon: <IcFilm style={{ width: 13, height: 13 }} /> },
    { key: 'tv',    label: 'TV',     icon: <IcTv style={{ width: 13, height: 13 }} /> },
    { key: 'anime', label: 'Anime',  icon: <IcAnime style={{ width: 13, height: 13 }} /> },
    { key: 'music', label: 'Music',  icon: <IcMusic style={{ width: 13, height: 13 }} /> },
  ];
  const [tab, setTab] = useState<MediaType>('movie');
  const [templates, setTemplates] = useState<Record<MediaType, string>>(() => seedTemplates(profile, savedCustom));

  // Keep the latest saved-custom values in a ref so a profile switch can
  // re-seed from them WITHOUT savedCustom being a seed-effect dependency —
  // otherwise every debounced save (which updates savedCustom) would re-seed
  // and clobber the user's in-progress edits / jump their caret.
  const savedCustomRef = useRef(savedCustom);
  useEffect(() => { savedCustomRef.current = savedCustom; }, [savedCustom]);

  // Re-seed only when the profile changes. Declared AFTER the ref-sync effect
  // so that when a settings load lands both a new profile AND savedCustom in
  // the same commit, the ref is already current before this reads it.
  useEffect(() => {
    setTemplates(seedTemplates(profile, savedCustomRef.current));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [profile]);

  const editable = profile === 'Custom';
  const tpl = templates[tab];
  const tokens = TOKEN_CHIPS[tab] || [];

  // Imperative caret-aware insert exposed by the chip editor. Click-to-insert
  // from the palette calls through this so the token lands at the caret inside
  // the contentEditable (which has no selectionStart like an <input> does).
  const insertAtCaretRef = useRef<((token: string) => void) | null>(null);

  // Apply an edit to the active tab. Updates local state and, for the Custom
  // profile, persists the whole 4-type dict through onSaveCustom (debounced
  // upstream).
  function applyEdit(nextForTab: string) {
    // Compute the next dict from the current closure value and call both
    // setters OUTSIDE any updater. (Calling the parent's onSaveCustom from
    // inside a setTemplates updater triggers React's "setState while
    // rendering another component" warning.)
    const next = { ...templates, [tab]: nextForTab };
    setTemplates(next);
    if (editable) onSaveCustom?.(next);
  }

  // Click a token chip → insert it at the caret (Custom profile only). The
  // editor handles caret placement + chip promotion; if it isn't focused yet,
  // fall back to appending so the click still does something.
  function insertToken(k: string) {
    if (!editable) return;
    if (insertAtCaretRef.current) insertAtCaretRef.current(k);
    else applyEdit((templates[tab] ?? '') + k);
  }

  // Drag-and-drop: drag a palette pill onto the chip editor to drop a token in.
  // contentEditable doesn't reliably fire input on a native text drop AND we
  // need the dropped text to become a chip, so we own the drop: place the caret
  // at the drop point via caretRangeFromPoint, insert the token text, then let
  // the editor's onChange promote it. preventDefault on dragover marks a valid
  // target.
  const [dropActive, setDropActive] = useState(false);

  return (
    <div>
      {/* Media-type tabs — OWN class (.naming-mtab), never the shared
          .provider-tab (ManualSearch depends on that). The active tab tints to
          its media colour so the whole studio "wears" the type you're editing. */}
      <div className="naming-mtabs">
        {tabs.map(t => {
          const c = TYPE_COLOR[t.key];
          const on = tab === t.key;
          return (
            <button
              key={t.key}
              className={`naming-mtab ${on ? 'on' : ''}`}
              onClick={() => setTab(t.key)}
              style={on ? { color: c, borderColor: `color-mix(in srgb, ${c} 40%, transparent)`, background: `color-mix(in srgb, ${c} 10%, transparent)`, boxShadow: `inset 0 -2px 0 0 ${c}` } : undefined}
            >
              <span style={{ display: 'inline-flex', alignItems: 'center', width: 13, height: 13, color: on ? c : 'var(--ink-3)' }}>
                {t.icon}
              </span>
              {t.label}
            </button>
          );
        })}
      </div>

      <div className="naming-editor" style={{ ['--mtab' as string]: TYPE_COLOR[tab] }}>
        {/* ── LEFT: template + token palette ───────────────── */}
        <div className="naming-pane">
          <div className="naming-pane-head">
            <span>Template · {tabs.find(t => t.key === tab)!.label}</span>
            {!editable
              ? <span className="naming-lock">{profile} preset · pick Custom to edit</span>
              : <BadgeWithDot color="success" pulse>editable · autosaved</BadgeWithDot>}
          </div>
          <TemplateChipEditor
            key={tab}
            value={tpl}
            editable={editable}
            onChange={applyEdit}
            dropActive={dropActive}
            registerInsert={fn => { insertAtCaretRef.current = fn; }}
            onDragOver={editable ? (e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; if (!dropActive) setDropActive(true); }) : undefined}
            onDragLeave={editable ? (() => setDropActive(false)) : undefined}
            onDrop={editable ? (e => {
              // Own the drop: place the caret at the drop point, insert the
              // dropped token text, and let the editor promote it to a chip.
              e.preventDefault();
              setDropActive(false);
              const k = e.dataTransfer.getData('text/plain');
              if (!k) return;
              const el = e.currentTarget as HTMLDivElement;
              el.focus();
              // caretRangeFromPoint (Chromium/WebKit) / caretPositionFromPoint
              // (Firefox) gives the caret at the pointer; fall back to end.
              const doc = document as Document & {
                caretRangeFromPoint?: (x: number, y: number) => Range | null;
                caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
              };
              const sel = window.getSelection();
              let range: Range | null = null;
              if (doc.caretRangeFromPoint) {
                range = doc.caretRangeFromPoint(e.clientX, e.clientY);
              } else if (doc.caretPositionFromPoint) {
                const pos = doc.caretPositionFromPoint(e.clientX, e.clientY);
                if (pos) { range = document.createRange(); range.setStart(pos.offsetNode, pos.offset); range.collapse(true); }
              }
              if (!range || !el.contains(range.startContainer)) {
                range = document.createRange();
                range.selectNodeContents(el);
                range.collapse(false);
              }
              const node = document.createTextNode(k);
              range.insertNode(node);
              sel?.removeAllRanges();
              const after = document.createRange();
              after.setStartAfter(node);
              after.collapse(true);
              sel?.addRange(after);
              // Trigger the editor's own DOM→string sync + chip promotion.
              el.dispatchEvent(new Event('input', { bubbles: true }));
            }) : undefined}
          />
          <div className="naming-hint">
            Pipe filters like <code>{'{{ n | upper }}'}</code> and conditionals
            like <code>{'{% if hdr %}…{% endif %}'}</code> work too.
          </div>

          <div className="naming-pane-head" style={{ marginTop: 18 }}>
            <span>Tokens · {tab}</span>
            {editable ? <span className="naming-lock">click or drag to insert</span> : null}
          </div>
          <div className="naming-tokens">
            {tokens.map(t => (
              <button
                key={t.k}
                type="button"
                className={`token-chip ${editable ? 'clickable' : ''}`}
                disabled={!editable}
                draggable={editable}
                onDragStart={editable ? (e => { e.dataTransfer.setData('text/plain', t.k); e.dataTransfer.effectAllowed = 'copy'; }) : undefined}
                onClick={() => insertToken(t.k)}
                title={editable ? `Insert or drag ${t.k}` : t.d}
                style={editable ? { cursor: 'grab', userSelect: 'none' } : undefined}
              >
                <span className="kbd" style={{ margin: 0 }}>{t.k}</span>
                <span style={{ color: 'var(--ink-3)', fontSize: 11 }}>{t.d}</span>
              </button>
            ))}
          </div>
          <div style={{ marginTop: 8, fontSize: 11, color: 'var(--ink-3)', lineHeight: 1.5 }}>
            <span style={{ color: 'var(--ink-4)' }}>Filters (pipe with </span>
            <code style={{ color: 'var(--ink-2)' }}>|</code>
            <span style={{ color: 'var(--ink-4)' }}>): </span>
            {TOKEN_FILTERS}
          </div>
        </div>

        {/* ── RIGHT: live preview against the real library ─── */}
        <div className="naming-pane naming-pane-preview">
          <div className="naming-pane-head">
            <span style={{ color: 'var(--mtab)' }}>Live preview · your library</span>
          </div>
          {/* OUTPUT TRACK BAR — the page's "wow": one track per media type, only
              the active one lit full-colour. The structural twin of Matching's
              confidence tier bar. Reads ONLY `tab` + TYPE_COLOR, so it never
              touches the editor or the live-preview internals. */}
          <div className="mb-3.5">
            <div className="flex h-2.5 w-full gap-1.5">
              {tabs.map(t => {
                const c = TYPE_COLOR[t.key];
                const on = t.key === tab;
                return <span key={t.key} className="flex-1 rounded-full transition-all" style={{ background: on ? c : `color-mix(in srgb, ${c} 22%, transparent)`, boxShadow: on ? `0 0 0 3px color-mix(in srgb, ${c} 22%, transparent)` : 'none' }} />;
              })}
            </div>
            <div className="mt-2.5 flex flex-wrap justify-between gap-x-4 gap-y-1 text-[11.5px] text-tertiary">
              {tabs.map(t => {
                const c = TYPE_COLOR[t.key];
                const on = t.key === tab;
                return (
                  <span key={t.key} className="inline-flex items-center gap-1.5" style={on ? { color: c, fontWeight: 600 } : undefined}>
                    <span className="size-1.5 rounded-full" style={{ background: c }} />
                    {t.label}
                  </span>
                );
              })}
            </div>
          </div>
          <LiveTemplatePreview tab={tab} template={tpl} />
        </div>
      </div>
    </div>
  );
}

// Real live preview: debounces edits, calls the backend's /rename/preview-
// template (the SAME engine a real rename uses) against the user's recent
// matched files, and shows the actual paths it would produce.
function LiveTemplatePreview({ tab, template }: { tab: MediaType; template: string }) {
  const [samples, setSamples] = useState<{ filename: string; rendered: string; error: string | null }[] | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    const handle = window.setTimeout(async () => {
      try {
        const body: { movie?: string; tv?: string; anime?: string; music?: string; samples_per_type?: number } = { samples_per_type: 3 };
        body[tab] = template;
        const resp = await api.previewTemplate(body);
        if (cancelled) return;
        setSamples(resp.samples.filter(s => s.media_type === tab));
      } catch (e) {
        if (!cancelled) { setErr((e as Error).message); setSamples(null); }
      }
    }, 350);
    return () => { cancelled = true; window.clearTimeout(handle); };
  }, [tab, template]);

  if (err) {
    return <div className="naming-preview-empty" style={{ color: 'var(--conf-low)' }}>Preview unavailable: {err}</div>;
  }
  if (samples === null) {
    return <div className="naming-preview-empty">Rendering…</div>;
  }
  if (samples.length === 0) {
    return (
      <div className="naming-preview-empty">
        No matched {tab} files yet — scan &amp; match some to see a live preview.
      </div>
    );
  }
  return (
    <div className="naming-preview-list anim-stagger">
      {samples.map((s, i) => (
        <div key={i} className="naming-preview-row" style={{ ['--i' as string]: Math.min(i, 6) }}>
          {s.error ? (
            <span style={{ color: 'var(--conf-low)', fontSize: 12 }}>{s.filename}: {s.error}</span>
          ) : (
            <>
              <div className="naming-preview-src">{s.filename}</div>
              {/* Re-key the rendered span on the path string so it re-fires the
                  morph-in animation each time an edit produces a new path —
                  the live preview visibly "lands" on every keystroke. */}
              <div className="preview-path">
                <span key={s.rendered} className="seg-new naming-preview-morph">{s.rendered}</span>
              </div>
            </>
          )}
        </div>
      ))}
    </div>
  );
}
