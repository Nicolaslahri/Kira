# Kira UI Design Guidelines — the "Flow" Design Language

This is the reference for designing any Kira UI surface so it matches the cohesive
look and feel built across the app. Hand this file to Claude with a request like
_"redesign the X page following the Kira design guidelines"_ and it should produce
work consistent with everything already shipped.

It covers three things, because all three are needed to reproduce the result:
1. **The visual language** — tokens and recipes (so the output _looks_ the same).
2. **The patterns** — the signature components and layouts that recur on every surface.
3. **The way of working** — how a surface is approached, verified, and judged.

---

## 1. Philosophy

- **Dark, near-monochrome base.** Black / white / greys carry the structure. Colour
  is *meaning*, never decoration.
- **One bright INDIGO action accent.** Everything interactive or "active" speaks
  indigo (`#6366f1`) — buttons, focus rings, the selected nav row, active filter
  chips, primary CTAs, selection bars. Indigo is reserved; if it's indigo, it's
  actionable or current. (The product is **NOT** emerald/green-accented.)
- **Restraint + cohesion.** Every surface must read as one family. Reuse the app's
  own idioms before inventing new ones — grep for an existing pattern first.
- **Honesty over churn.** If a surface is already cohesive, do a light *cohesion
  pass* (or nothing) — don't fabricate a teardown. Flag real gaps; don't invent them.
- **Data-driven "wow".** Each primary surface earns one small, custom visual derived
  from *real state* — not generic chrome.

---

## 2. Colour system (exact tokens)

All tokens are CSS custom properties in `src/index.css` unless noted. Prefer the
token; never hardcode a hex that duplicates one.

### Indigo action accent
| Token | Value | Use |
|---|---|---|
| `--accent` | `#6366f1` | the accent; solid fills, focus, rails |
| `--accent-deep` | `#4f46e5` | pressed / solid button fill (white label) |
| `--accent-bright` | `#a5b4fc` | accent text/icon **on dark** (active nav icon) |
| `--accent-soft` | accent @14% | soft accent fills |
| `--accent-line` | accent @32% | hairline accent borders/rings |
| `--accent-4/8/12/16/24/32/50` | accent @N% | fills/rings at graded strengths |

Canonical "selected/active" fill is `--accent-8`; its hover deepens to `--accent-12`.

### Status — confidence (the ONLY semantic colour ramp)
| Token | Value | Meaning |
|---|---|---|
| `--conf-high` | `#3cb371` green | strong / success / in-sync |
| `--conf-mid` | `#ffa586` amber/peach | needs review / fair |
| `--conf-low` | `#b51a2b` red | low / none / error |
| `--info` | `#8b8b8b` **GREY** | neutral / "likely" (**NOT blue**) |
| `--warn` `#b0b0b0`, `--danger` `#c41f30` | | warnings / destructive |

Each has `-bg` (@12%), and most have `-16/-24/-32/-50` ring/fill steps and a
`-bright` text variant (`--conf-mid-bright`, `--conf-low-bright`, `--info-bright`).

### Media types
| Type | Colour | Notes |
|---|---|---|
| Movies | `#4ec5b3` teal | inline hex (not a var) |
| TV | `#b3e5fc` sky | inline hex (not a var) |
| Anime | `--media-anime` `#b48cff` violet | + `--media-anime-bright` `#d8c4ff` |
| Music | `--media-music` `#ffb14a` amber | |

### File operations
`--op-move` = `--accent` (indigo) · `--op-copy` = `--info` (grey) ·
`--op-hardlink` = `--conf-mid` (amber) · `--op-symlink` = `--media-anime` (violet).

### Surfaces, text, borders (Untitled UI semantic utilities)
- **Surfaces:** `bg-secondary` (card surface) → `bg-tertiary` (inner row / nested).
  The app shell is darker than `bg-secondary`.
- **Text hierarchy:** `text-primary` > `text-secondary` > `text-tertiary` >
  `text-quaternary` (brightest → dimmest).
- **Hairlines:** `ring-secondary` (resting) → `ring-primary` (hover). Legacy
  `border-[var(--border-2)]` is being phased out — use rings.

> **Hard rules:** never use indigo for status; never use green/amber/red for actions
> or selection; `--info` is grey, not blue. Selection (indigo) and health
> (green/amber/red) are orthogonal axes and must never be conflated.

---

## 3. Surfaces & elevation — the core recipes

**Card / panel:**
```
bg-secondary ring-1 ring-inset ring-secondary shadow-xs rounded-xl
```

**Inner row / nested block:** `bg-tertiary` (+ `ring-1 ring-inset ring-secondary`
if it needs an edge).

**Interactive row hover:** `hover:bg-tertiary hover:ring-primary` with
`transition-[background-color,box-shadow]`.

**THE selected / active idiom (signature — reuse verbatim):**
```
bg-[var(--accent-8)] ring-1 ring-inset ring-[var(--accent-line)]
shadow-[inset_3px_0_0_var(--accent)]      /* 3px indigo left rail */
hover:bg-[var(--accent-12)]               /* active hover never falls back to grey */
```
This exact idiom (`inset 3px 0 0 var(--accent)` over `--accent-8`) is used app-wide
for "you are here / this is selected." Quieter echo for nested/child rows: a **2px**
rail (`shadow-[inset_2px_0_0_var(--accent)]`). A parent + active child share the
left edge as one continuous indigo spine.

Always use `ring-1 ring-inset` (not `border`) for hairlines so there's no layout
shift, and so corners stay clean under `overflow-hidden`.

**Modals/overlays are an exception to the card surface.** `bg-secondary`/`bg-tertiary`
are *translucent* (a few-percent white overlay) — they read fine as cards on the
opaque app shell, but a floating modal sits over a blurred backdrop, so a translucent
panel lets the content behind bleed through (looks "too transparent"). Give a modal an
**opaque panel**: `bg-[var(--panel-90)]` (90% `#121212`, a subtle frost with
`backdrop-blur`) or `bg-[var(--panel)]` (fully solid), with `shadow-[var(--shadow-3)]`
for elevation and `ring-1 ring-inset ring-secondary`. The backdrop is
`bg-[var(--scrim-60)] backdrop-blur-sm`. Inner rows can still use translucent
`bg-tertiary` — over the opaque panel they read as lighter cards.

---

## 4. Typography

- **Eyebrow (section/label kicker) — ONE canonical recipe everywhere:**
  ```
  text-[10px] font-semibold uppercase tracking-[0.14em] text-quaternary
  ```
- **Card title:** `text-sm font-semibold text-primary` (paired with a `FeaturedIcon`).
- **Big display / hero title:** large, `font-bold`, tight tracking (`tracking-[-0.03em]`).
- **Monospace** for filesystem paths, release names, technical IDs.
- **`tabular-nums`** on any number that updates or sits in a row of numbers (counts,
  scores, sizes) to stop reflow.

---

## 5. Components (Untitled UI, vendored under `src/components/base`)

Use these rather than bespoke markup:

- **`FeaturedIcon`** — the standard icon chip. `size` `sm`/`md`; either `color="gray"`
  or a `tint={hex|var}` (tint renders `color-mix(tint 12%)` bg + tint-coloured icon).
  Card headers = `FeaturedIcon` + title.
- **`BadgeWithDot`** — status pill with a leading dot. `color` `brand|success|warning|error|gray`, optional `pulse`.
- **`Badge`** — plain compact label chip.
- **`Button`** — variants: `primary` (indigo), `secondary`, `secondary-destructive`,
  `primary-destructive`, `tertiary`, `link-color`, `link-gray`. Sizes `sm`/`md`.
  `iconLeading`, `isLoading`, `isDisabled`.
- **`ButtonGroup` / `SegmentedControl`** — segmented switchers (e.g. tab toggles).
- **`Select`, `Input`, `Toggle`, `SliderField`** — form controls. Prefer a
  `SliderField` over a number `Input` for a bounded 0–100 score/threshold.
- **`SectionCard`** — settings card shell; supports an optional `tint`.
- **`NavItemBase`** — sidebar nav row (carries the active idiom from §3).
- **`FilterChip`** (exported from `ReviewPage.tsx`) — a detached chip that lights up
  in *its own* accent colour when active. Props: `on`, `onClick`, `label`, `num`,
  `accent`, `icon`, `dot`. Used for period/operation/type/status filters.

---

## 6. Signature patterns

**Flow hero** — a top band introducing a surface: an eyebrow + title + a short
real-state summary line, often with a primary action and a thin status/identity
strip. The Dashboard uses a richer landing-hero variant (gradient title + poster fan).

**The compact "wow"** — one small visual per surface, derived from *real data*:
- a thin segmented **mix bar** (e.g. strong/fair/weak by score, or media composition)
  + a readout (`avg N`, totals);
- a **funnel** / progress strip (pending → matched → renamed);
- a **confidence ring** (donut) with status-coloured segments.
Mirror this grammar across surfaces (e.g. the History "undo-status" strip and the
Subtitles "match-quality" bar are siblings).

**Count badges** — neutral by default (`bg-tertiary text-tertiary ring-secondary`,
`tabular-nums`); join the accent system **only on the active row**
(`bg-[var(--accent-16)] text-[var(--accent-bright)] ring-[var(--accent-32)]`).

**Status pills** — `ring-1 ring-inset ring-secondary bg-secondary shadow-xs` with a
single semantic colour dot (green/amber/red). The container stays neutral; only the
dot carries colour.

**Selection bar** — bulk-select uses an indigo bar (`--accent-soft`/`--accent-line`),
not a floating grey toolbar.

---

## 7. How to approach a surface

1. **Scout** the file(s) and **look at it live** before designing. Identify what's
   already cohesive vs. genuinely off.
2. **Reuse the app's own idioms** (grep `index.css` / existing pages for an existing
   recipe — e.g. the `inset 3px 0 0 var(--accent)` selected idiom) before inventing.
3. For a substantive surface, **explore options** (ideally several independent design
   directions) then **synthesise one buildable blueprint** and reject what doesn't fit
   the dark-monochrome restraint (no heavy glows, no gratuitous motion refactors).
4. Give every primary surface a **flow hero + one data-driven "wow."**
5. **Already-good surface?** Do a focused cohesion pass (align eyebrows, swap legacy
   `border` → `ring`, fix the one real inconsistency) — say so honestly; don't redesign
   for the sake of it.

---

## 8. Motion

- Subtle and purposeful. Entrance: `anim-rise` / `anim-rise-sm`. Transitions on
  colour/box-shadow, ~100–220ms, app easing tokens (`--ease-out`).
- Continuous/looping animation only where it earns its keep (e.g. the live logo,
  a "live" pulse). Keep it slow and tasteful.
- Always guard loops with `@media (prefers-reduced-motion: reduce) { … }`.

---

## 9. Hard rules & gotchas (learned the hard way)

- **Scope discipline:** when a task is scoped to specific surfaces, don't edit shared
  components or global tokens used elsewhere — reuse them. If you *must*, ask first.
  (A component used by only one surface — e.g. the sidebar's `NavItemBase` — is fair game.)
- **Verify tokens exist** before using them (`grep` `index.css`); a missing
  `var(--…)` renders transparent and silently breaks the design.
- **Edit source files with the Edit tool, never PowerShell** `Get-Content`/`Set-Content`
  (they double-encode UTF-8).
- **Verify live, don't ask the user to check** — build (`npm run build`), then use the
  preview tools to reload, screenshot, and confirm. Share proof.
- **`cx` is `tailwind-merge`** — later conflicting utilities win (so a per-state
  `hover:bg-[var(--accent-12)]` correctly overrides a base `hover:bg-primary_hover`).
- **`naturalWidth===0` is a false "broken image"** signal for viewBox-only SVGs — use
  `getBoundingClientRect()` instead.
- **Raster brand logos:** save a real `.png` and reference it directly; don't embed
  base64 inside an `<img>`-loaded SVG (restricted mode blocks data-URIs).
- **A continuously-animating inline/img SVG makes `preview_screenshot` hang** (the page
  never goes idle — the animated logo does this on every page). Workaround: inject a
  freeze stylesheet via `preview_eval` right before the shot, then remove it after —
  `*,*::before,*::after{animation:none !important;transition:none !important}` +
  `[role="dialog"],[role="dialog"] *,.anim-pop{opacity:1 !important;transform:none !important}`
  (re-shows finished entrances that `animation:none` would otherwise revert to opacity:0)
  + `img[alt="Kira"],img[src*="logo.svg"]{visibility:hidden !important}` (the logo's
  internal SVG animation can't be paused from the parent, so hide the img). It's still
  intermittent — also verify via `eval`/`getAnimations()`/computed style and lean on the
  user's eyeball.

---

## 10. Pre-ship checklist

- [ ] Uses the canonical card / row / selected recipes (§3); hairlines are `ring`, not `border`.
- [ ] Eyebrows use the one canonical recipe (§4).
- [ ] Indigo only for action/active; status only green/amber/red/grey; media colours correct.
- [ ] One flow hero + one data-driven "wow" (for a primary surface).
- [ ] All referenced `var(--…)` tokens exist.
- [ ] `npm run build` is green; verified live in the preview with a screenshot/eval.
- [ ] No shared component/global token edited out of scope.
- [ ] Loops respect `prefers-reduced-motion`.
