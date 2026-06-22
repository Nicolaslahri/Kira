# Kira → Untitled UI — Component Inventory

**Purpose.** Map every Kira UI element — across every page, every Settings subsection, the global chrome, and every modal/overlay — to an Untitled UI (UUI) component, so we can replace bespoke pieces with real UUI components and **reuse them universally** across the app. The goal is one canonical implementation per pattern (one Button, one Badge, one Select, one Modal, one SectionCard…) instead of the dozens of hand-rolled variants catalogued below.

## How to read this

- The **Universal component map** is the source of truth: it groups every element in the app under the UUI component it should become, ordered by impact. Start there to standardize and reuse.
- The **Per-surface inventory** lists what each screen contains today (element names match the Universal map). **Text styles** and **Custom — no UUI equivalent** cover typography tokens and the genuinely-bespoke pieces that must stay.
- "Current impl" notes whether a piece is **already a UUI base component** (in `frontend/src/components/base/**`, re-skinned to Kira tokens), a **shared Kira primitive** (`components/ui.tsx`, `settings-blocks.tsx`), or **raw/bespoke** (inline Tailwind or a `index.css` class). Raw/bespoke is where the migration work is.
- The **Untitled UI design tokens** and **Untitled UI component catalog** sections below are the reference menu (extracted from the provided *Design System.zip* → `tokens/*.css` + `components/**/*.d.ts`). When a map/inventory row says "→ Buttons (primary)", the exact variant/prop API lives in the catalog and the exact color/font/spacing lives in the tokens.

---

## Untitled UI design tokens

> Canonical UUI token system from `Design System.zip` (`tokens/`). These are the **token names to standardize on**. The zip ships **stock UUI** — violet brand, light default. Kira already re-maps these names to its **emerald-on-black** palette in `frontend/src/styles/theme.css` (the "Kira look bridge"): brand → emerald (`--accent #28d9a0`), gray → Kira's dark neutrals, surfaces → pure-black + white-alpha glass, error/warning/success ≈ red/amber/green. **Use the UUI names; Kira supplies the values.**

### Color — primitive scales (stock UUI hex)

| Scale | 25 | 50 | 100 | 200 | 300 | 400 | 500 | 600 | 700 | 800 | 900 | 950 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Brand** violet *(Kira→emerald)* | fcfaff | f9f5ff | f4ebff | e9d7fe | d6bbfb | b692f6 | 9e77ed | 7f56d9 | 6941c6 | 53389e | 42307d | 2c1c5f |
| **Gray** | fcfcfd | f9fafb | f2f4f7 | eaecf0 | d0d5dd | 98a2b3 | 667085 | 475467 | 344054 | 182230 | 101828 | 0c111d |
| **Error** red | fffbfa | fef3f2 | fee4e2 | fecdca | fda29b | f97066 | f04438 | d92d20 | b42318 | 912018 | 7a271a | 55160c |
| **Warning** orange | fffcf5 | fffaeb | fef0c7 | fedf89 | fec84b | fdb022 | f79009 | dc6803 | b54708 | 93370d | 7a2e0e | 4e1d09 |
| **Success** green | f6fef9 | ecfdf3 | dcfae6 | abefc6 | 75e0a7 | 47cd89 | 17b26a | 079455 | 067647 | 085d3a | 074d31 | 053321 |
| **Blue** accent | f5faff | eff8ff | d1e9ff | b2ddff | 84caff | 53b1fd | 2e90fa | 1570ef | 175cd3 | 1849a9 | 194185 | 102a56 |

### Color — semantic aliases (reference THESE, not the primitives)
- **Text** — `text-primary` gray-900 · `text-secondary` gray-700 (hover 800) · `text-tertiary` gray-600 · `text-quaternary` gray-500 · `text-disabled`/`text-placeholder` gray-500 · `text-white` · `text-brand-primary` brand-900 · `text-brand-secondary` brand-700 · `text-brand-tertiary` brand-600 · `text-error-primary` error-600 · `text-warning-primary` warning-600 · `text-success-primary` success-600.
- **Foreground / icons** — `fg-primary` gray-900 · `fg-secondary` gray-700 · `fg-tertiary` gray-600 · `fg-quaternary` gray-400 · `fg-quinary` gray-400 · `fg-white` · `fg-disabled` gray-400 · `fg-brand-primary` brand-600 · `fg-brand-secondary` brand-500 · `fg-error/warning/success-primary`.
- **Background** — `bg-primary` white · `bg-primary-hover` gray-50 · `bg-secondary` gray-50 (hover 100, subtle gray-25) · `bg-tertiary` gray-100 · `bg-quaternary` gray-200 · `bg-active` gray-50 · `bg-disabled` gray-100 · `bg-overlay` gray-950 · `bg-brand-primary` brand-50 · `bg-brand-secondary` brand-100 · `bg-brand-solid` brand-600 (hover 700) · `bg-brand-section` brand-800 · `bg-error/warning/success-primary` (50) + `-solid` (600).
- **Border** — `border-primary` gray-300 · `border-secondary` gray-200 · `border-tertiary` gray-100 · `border-disabled` gray-300 · `border-brand` brand-300 · `border-brand-solid` brand-600 · `border-error` error-300 · `border-error-solid` error-600.
- **Utility surfaces** (soft pill/alert fills) — `utility-{brand,gray,success,error,warning,blue}-{50,100,200,500,600,700}`.
- **Dark mode** (`[data-theme="dark"]`/`.dark` — Kira is always dark) — text-primary→gray-50, text-secondary→gray-300, text-tertiary/quaternary→gray-400, fg-primary→white, bg-primary→gray-950 *(Kira pushes to pure #000)*, bg-secondary→gray-900, bg-tertiary→gray-800, border-primary→gray-700, border-secondary/tertiary→gray-800.

### Typography
- **Families** — `--font-sans` = `--font-display` = **Inter** (fallback `-apple-system, Segoe UI, Roboto…`). `--font-mono` = **Roboto Mono** (`ui-monospace, SF Mono, Menlo…`). *(Kira keeps Inter + JetBrains Mono.)*
- **Weights** — regular **400** · medium **500** · semibold **600** · bold **700**.
- **Display scale** *(size/line, tracking −0.02em on md+)* — display-2xl **72/90** · display-xl **60/72** · display-lg **48/60** · display-md **36/44** · display-sm **30/38** · display-xs **24/32**.
- **Text scale** *(tracking 0)* — text-xl **20/30** · text-lg **18/28** · text-md **16/24** · text-sm **14/20** · text-xs **12/18**.
- **Tracking** — display −0.02em · tight −0.01em · normal 0. Utility classes `.ui-display-*`, `.ui-text-*`.

### Spacing, radius, shadows
- **Spacing** (4px base) — 0 · 0.5=2 · 1=4 · 1.5=6 · 2=8 · 3=12 · 4=16 · 5=20 · 6=24 · 8=32 · 10=40 · 12=48 · 16=64 · 20=80 · 24=96 · 32=128 · 40=160 px. **Containers** sm 640 · md 768 · lg 1024 · xl 1280 · 2xl 1440.
- **Radius** — none 0 · xxs 2 · xs 4 · sm 6 · md 8 · lg 10 · xl 12 · 2xl 16 · 3xl 20 · 4xl 24 · full 9999.
- **Shadows** (cool, gray-900-tinted) — `shadow-xs · sm · md · lg · xl · 2xl · 3xl`. **Focus rings** — `ring-brand` (brand@24%) · `ring-gray` (gray@14%) · `ring-error` (error@24%). **Skeuomorphic** solid-button inner border — `shadow-skeu-brand`/`-gray` *(Kira's UUI Button already uses `shadow-xs-skeuomorphic`)*.

---

## Untitled UI component catalog — exact components, variants & props

> Every component in `Design System.zip` (`components/**/*.d.ts`), with its real options. This is the **menu** the Universal map picks from. "← Kira …" notes which Kira `base/` component already implements it.

### Buttons (`components/buttons`)
- **Button** — `color`: primary · secondary · tertiary · primary-destructive · secondary-destructive · tertiary-destructive · link-gray · link-color · link-destructive. `size`: xs · sm · md · lg · xl · 2xl. Props: `iconLeading`/`iconTrailing` (name|component|element), `isLoading`, `showTextWhileLoading`, `isDisabled`, `href` (→ `<a>`), `fullWidth`. ← **Kira `base/buttons/button.tsx` is this 1:1.**
- **IconButton** — `icon` (req), `hierarchy`: primary · secondary · tertiary. `size`: sm · md · lg. `aria-label` req. ← every icon-only button.
- **ButtonGroup** — joined segmented buttons; `items` [{value,label,icon}], value, onChange. ← mutually-exclusive clusters / Kira `.seg`.
- **SocialButton** — social: google|facebook|apple|x|figma|dribbble; theme: color|outline|gray; iconOnly, fullWidth. **AppStoreBadge** — store: apple|google|galaxy|appgallery; outline.

### Feedback — badges · alerts · status · progress (`components/feedback`)
- **Badge** — `color`: gray · brand · error · warning · success · blue · gray-blue · blue-light · indigo · purple · pink · orange. `type`: pill-color · color · modern. `size`: sm · md · lg. `dot`, `icon`, `iconTrailing` (e.g. "X" → dismissable). ← **the universal pill**: replaces ConfidenceBadge, StatusPill, history-op chips, sync chips, count chips, neutral attribute pills.
- **BadgeGroup** — announcement: leading pill (label, color brand|gray|success, icon) + trailing text + arrow.
- **Banner** — full-width announcement: color brand|gray, icon, action, onClose.
- **Alert** — inline callout: color brand|gray|error|warning|success, title, icon, actions, onClose. ← **Kira `base/alert` matches.**
- **Tag** — removable/selectable token: `size` sm|md|lg, `count`, `dot`, `avatarSrc`, `isDisabled`, `onRemove`/`onClose`. ← FilterPill, language/source chips, removable selections.
- **Toast** — color success|error|warning|brand|gray, icon, title, actions, onClose. ← **Kira Toast.**
- **Tooltip** — title, description, arrow, placement top|bottom|left|right, dark.
- **Skeleton** — width, height, circle. ← **Kira Skeleton.**
- **ProgressBar** (value, showLabel) · **ProgressCircle** (value, size, stroke, showLabel) · **HalfCircleProgress** (gauge) · **LoadingIndicator** (spinner size). ← ProgressBar, scan ring, confidence donut (gauge), spinners.
- **RatingBadge** · **RatingStars** (value, max, onChange, size).

### Forms (`components/forms`)
- **Input** — label, hint, `error` (→ error style), iconLeading, iconTrailing, `size`: sm · md. ← **Kira `base/input`.** All text/credential/path/search fields.
- **Textarea** — label, hint, error. *(Kira has none — adopt when a multiline field appears.)*
- **Select** — `options` [{value,label}], value, onChange, placeholder, label. ← single-value dropdowns *(Kira's bespoke `Select` in ui.tsx; **do NOT** use the React-Aria one — it caused a scroll-jump and was removed).*
- **MultiSelect** — values[], onChange, placeholder (tags input). ← language/source chip pickers.
- **Checkbox** — checked, `indeterminate` (tri-state), size sm|md, label, hint, disabled. ← **Kira `.cb`.**
- **Radio** — checked, size sm|md, label, hint, name, value. ← force-import / onboarding radios.
- **Toggle** — checked, size sm|md, `slim`, label, hint, disabled. ← **Kira `base/toggle`.**
- **Slider** — value, min, max. ← confidence/threshold sliders.
- **FileUpload** — dropzone: hint, onFiles, files[]. **Calendar** · **DatePicker** · **ColorPicker** · **VerificationCodeInput** (length, size). *(none used by Kira yet)*

### Data display (`components/data-display`)
- **Avatar** (src, initials, size xs–2xl, status online|busy|offline) · **AvatarGroup** (+N) · **AvatarLabelGroup**. *(Kira has no people-avatars; cover-art Poster stays custom.)*
- **FeaturedIcon** — `icon`, `color`: brand|gray|error|warning|success, `theme`: light|gradient|dark|modern|modern-neue|outline, `size`: sm|md|lg|xl. ← **Kira `base/featured-icons` (subset).** Use everywhere a tinted icon chip appears.
- **MetricCard** — label, value, change, trend up|down, icon, variant simple|icon|chart, iconColor, chartData (sparkline), actions. ← Dashboard KPI cards.
- **Table** — columns [{key,header,align,render}], rows. ← History/Review rows (if moved to a table).
- **EmptyState** — icon, title, children, actions. ← **Kira EmptyState (re-skinned).**
- **InlineCTA** — icon, title, actions. ← Settings cross-link cards / cleanup breadcrumb.
- **ActivityFeed** — items [{avatar,name,action,target,time}]. ← Dashboard recent-activity timeline.
- **Card** (padding) · **CardHeader** (title, badge, supportingText, actions) · **ContentDivider** (optional centered label) · **CodeSnippet** · **Accordion** · **Message** · **Carousel** · **Illustration** (type box|cloud|search|folder|chart|bell|mail|users; size) · **CreditCard** · **QRCode**.

### Layout — headers (`components/layout`)
- **PageHeader** — title, supportingText, actions, breadcrumbs. ← Dashboard/History/Settings page titles.
- **SectionHeader** — title, supportingText, actions. ← Kira `SectionHeader` (settings-blocks) — *should* use FeaturedIcon for its icon.
- **CardHeader** — title, badge, supportingText, actions. ← every card title row (Dashboard cards, SectionCard).

### Navigation (`components/navigation`)
- **Tabs** — items [{value,label,icon,badge}], value, onChange, `variant`: button | underline. ← media-type tabs, ManualSearch tabs.
- **Breadcrumbs** — items [{label,href,icon}]. ← Topbar breadcrumb.
- **Pagination** — page, total, onChange. *(Kira uses "newest 200" caps — adopt if real paging added.)*
- **ProgressSteps** — steps [{title,description}], current, `orientation`: vertical | horizontal. ← Onboarding stepper.
- **FilterBar** — filters [{value,label,icon}], value[], onChange (multi toggle chips). ← Review filter bar (Kira FilterPill/FilterGroup).
- **TreeView** — recursive nodes {id,label,icon,children}, activeId, onSelect. ← FolderPicker tree; Settings sub-nav.

### Overlays (`components/overlays`)
- **Modal** — open, onClose, icon, `iconColor`: brand|gray|error|warning|success, title, actions (footer). ← **all centered dialogs** (rename, shortcuts, confirms, CoverPopup sub-modals, FolderPicker).
- **Slideout** — open, onClose, title, subtitle, footer (right drawer). ← candidate host for CoverPopup / notifications.
- **DropdownMenu** — items [{label,icon,shortcut,destructive,divider,onClick}]. ← row action menus, notifications panel.
- **CommandMenu** — ⌘K palette: items [{label,icon,shortcut,group}]. ← Topbar search could become this.

### Charts (`components/charts`) · Media (`components/media`)
- **LineBarChart** (series, labels, type line|area|bar) · **PieChart** (data, donut) · **RadarChart** (axes, series). ← Dashboard storage/composition/confidence viz options.
- **TextEditor** (WYSIWYG) · **VideoPlayer** (poster, duration). *(not used by Kira)*

---

## Untitled UI design rules (how to apply)

> Usage rules from the DS guide (`readme.md` / `SKILL.md`) — the "how" beyond the tokens. Kira keeps its emerald-on-black skin but should follow these structural rules; deviations Kira makes on purpose are flagged.

- **Voice & copy** — **Sentence case everywhere** (headings, buttons, nav, table headers: "Add folder", "Save changes" — never "Add Folder"). Verb-first, terse button labels ("Get started", "Scan now", "Export"). Microcopy helpful & unobtrusive. **No emoji in UI** — status = dots + icons. *(Audit Kira for any Title-Case labels.)*
- **Type usage** — Inter for UI + display; Roboto Mono for code *(Kira: JetBrains Mono)*. **Semibold 600 is the workhorse** (buttons, labels, headings); 400 body, 500 medium, 700 rare. Display scale (72→24, −2% tracking) for hero; text scale (20→12, normal) for UI. Roomy line-heights.
- **Radii** — inputs & buttons **8px** (`radius-md`); cards **12px** (`radius-xl`); large promo/modal panels **16–24px**; **full-round only** for avatars, badges, toggles, dots. *(Kira over-rounds today — settings cards `rounded-2xl`=16, inputs `rounded-xl`=12; reconcile toward md/xl.)*
- **Elevation** — cool, gray-tinted (rgb 16,24,40), low-opacity, **never black/heavy**. Buttons/inputs `shadow-xs`; cards `shadow-sm`; menus/popovers `shadow-lg`; modals `shadow-xl`+. **Borders do the structural work; shadow adds just enough lift.**
- **Cards** — `1px border-secondary` · `12px` radius · `shadow-sm`, content-first *(Kira: dark glass surface instead of white)*.
- **Borders** — hairline **1px**. `border-primary` on inputs/controls; `border-secondary` for dividers & card edges. **Focus = brand border + a 4px translucent brand ring** (`ring-brand`).
- **Backgrounds** — flat; **no gradients on content surfaces**; the one bold move is a solid deep-brand band (`bg-brand-section`/brand-800). *(Kira's orange→magenta nav-pill gradient is a deliberate Kira-only identity exception.)*
- **Motion** — restrained: ~**120ms** ease on hover/focus color+shadow; **150ms** on toggle thumb; no bounces/loops; one indeterminate spinner. *(Kira adds spring pops + a `pressed:scale-[0.97]` on purpose.)*
- **Interaction states** — hover = step-darker fill or tint (tertiary buttons gain `bg-primary-hover`); press subtle; focus = visible brand ring; **disabled = `opacity-50`** (v8), no shadow.
- **Iconography** — Feather-style **line icons**, 24×24, ~1.67px stroke, rounded caps, `currentColor`. Sizes **16** (inline/dense) · **20** (buttons/inputs/nav default) · **24** (sidebar/headers) — **never below 16**. Color via surrounding `color` (`fg-quaternary` muted, `fg-brand-primary` accent). `FeaturedIcon` = the decorative tile. Real UUI icon names: `SearchLg`, `Settings01`, `Trash01`, `Folder`, `Plus`, `XClose` (Feather aliases `Search`/`Trash2` also resolve). *(Kira: `@untitledui/icons` installed, `PathsSection` swapped — roll out app-wide; keep media-type icons custom.)*
- **Component API parity** — Button `color`/`size`/`iconLeading`/`isLoading`; Badge `type`/`color`/`size`; Checkbox/Toggle/Input use `isSelected`/`isIndeterminate`/`isInvalid`/`isDisabled` (legacy `checked`/`error`/`disabled` still work). Kira's `base/` components already track this API.

---

## Universal component map

> The most important section. Each subsection = one UUI component. Tables list **every** app element/pattern that maps to it, where it appears, its current implementation, and the target. Ordered by how common / high-impact the consolidation is.

### Buttons

The single most-reused control. A real UUI `Button` (`base/buttons/button.tsx`, react-aria, full `color`×`size` system with `iconLeading`/`iconTrailing`/`isLoading`/`showTextWhileLoading`) already exists and is adopted on many surfaces — but legacy `.btn`/`.btn-primary`/`.btn-ghost`/`.btn-danger`/`.btn-brand` classes and inline-styled buttons (esp. in CoverPopup sub-modals and FolderPicker) still need to fold in.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Primary CTA (Scan now / Rename N / Approve & rename / Apply · Rename / Use this match / Get started / Sign in / Create account) | Topbar, Dashboard hero, Review, CoverPopup, Rename modal, ManualSearch, Onboarding, Login | UUI `Button` color=primary (most); legacy `.btn.btn-primary` in modals.tsx & LibraryGrid; inline-gradient button in FolderPicker | Buttons (primary) |
| Secondary / neutral button (Re-parse, Get all, Test connection, Add folder, Export/Import settings, Install for me, Browse…, Re-identify, Get subtitles, Clean undo leftovers, Export CSV) | Dashboard, every Settings section, History, CoverPopup, Subtitle modals, Onboarding | UUI `Button` color=secondary (most); legacy `.btn.btn-ghost` in modals.tsx; `CTL_BTN` string + glass button in FolderPicker | Buttons (secondary) |
| Tertiary / Cancel / Dismiss text button | Advanced danger rows, Subtitle modals, save bar, modal footers | UUI `Button` color=tertiary; some raw `.btn` / inline text buttons | Buttons (tertiary) |
| Link / link-gray text button (CardLink "Review/History/Configure", Mark all read, Sign-out, update link, "Get a key →", inline "Advanced"/"Connections" jumps, "Search →") | Dashboard cards, Notifications panel, Sidebar footer, Settings cross-links, Onboarding, LibraryGrid no-match | Mostly local wrappers over UUI `Button` color=link-gray/link; many are raw `<a>`/`<button>` with `text-info underline` | Buttons (link / tertiary) |
| Destructive — secondary (Reject, Undo selected, Reset database…, Blacklist, Empty trash armed) | Review, History, CoverPopup, Advanced, Subtitle History, Cleanup | UUI `Button` color=secondary-destructive | Buttons (destructive) |
| Destructive — primary (Confirm / Delete from disk / Empty trash confirm / armed factory reset) | Advanced danger rows, Cleanup, CoverPopup delete modals | UUI `Button` color=primary-destructive; inline-styled buttons in CoverPopup dupe/delete modals | Buttons (destructive primary) |
| Brand / gradient CTA (Onboarding "Get started", FolderPicker "Use this folder", login submit sizing) | Onboarding, FolderPicker, Login | `.btn.btn-brand` and inline `linear-gradient(brand-a,brand-b)` buttons | Buttons (primary, brand variant) |
| Busy/loading button (spinner swap + label change) | Everywhere async (Apply, Identify, Fetch subs, Fill N, Install) | UUI `Button isLoading showTextWhileLoading`; some manual `IcSpin` ternaries | Buttons (loading state) |
| Utility / icon-only button (search clear ×, eye show/hide, modal close ×, toast dismiss ×, Browse-folder, Clear filter ×, remove watch folder, reorder up/down, collapse/expand chevron buttons, back ‹) | Topbar, Settings inputs, Modals, Toast, Notifications, Cleanup, Matching, FolderPicker, Subtitle modals | Mostly raw `<button>` with a repeated `grid size-6/7 rounded-md hover:bg-… [&_svg]:size-*` recipe; `.close-x`, `.press` | Buttons (Utility / icon-only) |
| Button group / action cluster (bulk-bar actions, modal footers, Export/Import row, per-row approve/reject pair, candidate row actions) | Review bulk bar, History bulk bar, modal footers, CoverPopup rows, Subtitle History | Raw flex wrappers around buttons; `.seg-pair` for the approve/reject pair | Button groups |

### Badges & pills

A real UUI `Badge` (neutral pill) and `BadgeWithDot` (glassy pill + leading status dot, `color`+`pulse`) exist in `base/badges/badges.tsx` and are partly adopted. A huge tail of bespoke colored pills (history op chips, sync-status, severity tags, "needs key", count chips, confidence pills) should consolidate onto `Badge` color variants and `BadgeWithDot`.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Status badge-with-dot (Live/Scanning/Matching, provider Connected/Not set up/Checking, Sonarr Connected/Failed, "Coming soon") | Dashboard hero & providers, Connections, Integrations | UUI `BadgeWithDot` (color, pulse) | Badges (badge with dot) |
| Settings section StatusPill ("N connected", "On · recycle", "Auto ≥95%", "MediaInfo on", "{profile} profile") + provider-tile StatusPill | Every Settings section header, ProviderCard | Bespoke `StatusPill` (settings-blocks.tsx) — rounded-full pill + glowing/breathing dot via `STATUS_TONE` | Badges (badge with dot) |
| Save-status pill (Saving/Saved/Save failed, spinning dot) | Settings shell header | Inline `.save-indicator*` pill + `size-1.5` dot | Badges (badge with dot) |
| ConfidenceBadge (Strong/Likely/Needs review/Probably wrong + %) | Review, History, CoverPopup, FileDetails modal | Bespoke `.badge.badge-{level}` + `.dot` (ui.tsx) | Badges (with dot, color variants) |
| Confidence pill on cover (avg % / matched fraction) | LibraryGrid cover cards, CoverPopup hero/rows | `.cc-conf-pill` / `.cx-summary-chip` / `.cx-row-conf` + inline swatch | Badges (with dot) |
| Lifecycle StatusPill (Approved/Rejected/Pending/No match) | History, FileDetails modal, CoverPopup rows | Bespoke `.status-pill` + `.swatch` (ui.tsx); `.cx-row-status` | Badges (status) |
| File-operation chip (MOVE/COPY/HARDLINK/SYMLINK) | History rows | `.hist-op` + per-op color class | Badges (color variants) |
| Sync-status badge (in sync / likely sync / sync unknown) | Subtitle History, Subtitle Browse modal | `SYNC_STYLE`/`SYNC` map → colored `<span>` | Badges (color variants) |
| Neutral attribute pills (language code, SDH, Forced, media-type chips, "season pack", "best guess", release-group, AcoustID match, M·S·E keycaps, BEST FOR / WATCH OUT) | Subtitle History/Browse, Connections, FileDetails, Naming NFO picker, Naming file-op explainer | UUI `Badge` (media-type only); rest are bespoke `<span>` recipes (`rounded-md border px-1.5 py-0.5 text-[10.5px]`) | Badges (neutral / color) |
| "Undone" / "Restored" / stale-undo pills | History rows | `.hist-undone-pill` / `.hist-restored-tag` / `.hist-stale-pill` | Badges (color + Tooltip) |
| Severity tags (history / matches / library data / everything) + status dots | Advanced danger rows | Inline `color-mix()` pills + glowing dot | Badges (color) |
| Labs / experimental chips (Off by default, Experimental, Needs MediaInfo, needs key, Coming soon, soon, setup) | Matching, Naming, Subtitles, Integrations, ManualSearch tabs | Local `LabsChip`; bare `text-conf-mid` text; `.pill.pill-soon/.pill-warn` | Badges (small) |
| Count badge / chip (nav Review count, notification unread "99+", day-count pill, section/franchise count, FilterPill count, selection "N selected", "+N more") | Sidebar, Topbar bell, History day header, LibraryGrid sections, Review/History bulk bars, save bar | Many distinct recipes: nav pill, `motion.span` red badge, `.hist-day-count`, `.lib-section-count`, FilterPill inline span | Badges (number/count) |
| Step number badge (1/2/3) | Onboarding rail/steps, LibraryGrid onboarding hero | `.step-num`, `.dot` | Badges (number) — or Progress steps indicator |
| Standalone status dot (live status, bucket/storage legend dots, confidence swatches, provider corner dot, persistence dot, unread dot) | Sidebar footer, Dashboard legends, Matching/Settings, ProviderLogo, Paths, Notifications | Bare `<span size-1.5/2/2.5 rounded-full>` with inline bg + glow; `.breathe`/`.settings-dot-live` | Badges (dot) / Avatar indicator |

### Tags

Dismissible/selectable chips (with × or toggle state). UUI `Tags` is the target for selection chips; bespoke implementations are duplicated across subtitle pickers and the review filter bar.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| FilterPill (toggle chip with count + optional dot/icon) | Review queue filter bar | Bespoke `FilterPill` (ui.tsx) — accent-soft fill + inset ring + count span | Tags (toggle / selectable) |
| Removable selection chip (language/source override, manual-search match-to) | Subtitles (global + PerTypeChips ×6), Subtitle modal | `<span> + IcX button` duplicated in `PerTypeChips` and the global picker | Tags (dismissible) |
| Per-language "N missing" chips | Dashboard subtitle card | Raw `<span>` font-mono lang | Tags |
| Pending-change chips | Settings save bar | Inline `rounded-full bg-glass-2` span | Tags / Badges |
| Tech/quality chips (size, res, source, codec, HDR, channels, audio, lang, sub, release-group), missing-sub action chip, "+N" dupe pill | CoverPopup rows, MovieBody, dupe modals, ManualSearch | `.cx-row-tag` + modifiers; generic `Chip` (format.tsx) | Tags / Badges |
| Library-type / "auto-scan on" chips, cast chips | Onboarding summary, CoverPopup MovieBody | `.chip`, `.cc-cast-chip` | Tags |
| Keyboard key caps (kbd: /, ⌘, ⇧, Enter, ?) | Topbar search hint, Shortcuts modal, Naming token keycaps | `.kbd` / raw bordered `<span>` | Tags (custom kbd) |

### Inputs

A real UUI `Input` (`base/input/input.tsx`: glass field, `mono`, `invalid`, leading `icon`, `trailing` slot, `editGate` anti-autofill lock) exists and underpins many Settings fields. Bespoke search boxes and a couple of raw `<input>`s should fold in.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Credential / URL / key text field (mono, editGate) | Connections, Integrations, Advanced (perms), Paths | UUI `Input` via `ProviderField`/`PathField` | Inputs |
| Password field + eye show/hide toggle | Connections, Integrations | UUI `Input type=password` + custom trailing eye button | Inputs (password) |
| Path field with trailing Browse/Clear | Paths, Cleanup trash dir | UUI `Input` + `PATH_ICON_BTN` trailing buttons | Inputs (with trailing add-on) |
| Comma-list field (filenames / extensions) | Cleanup | Local `CommaListField` over UUI `Input mono` | Inputs (→ Tags upgrade possible) |
| Confirm-word field (DELETE / FACTORY) | Advanced danger rows | UUI `Input mono autoFocus` | Inputs |
| Global search box (leading magnifier + `/` kbd + clear) | Topbar | Bespoke `.topbar-search` wrapper + bare `<input>` | Inputs (search) |
| Per-section settings filter ("Filter settings…") | Every Settings section header | Bespoke `SettingsFilter` (raw `<input>` + abs icons) | Inputs (search) |
| History search ("Search renames…") | History | Bespoke `.hist-search` + bare `<input>` + clear | Inputs (search) |
| ManualSearch big search input | Rename/ManualSearch modal | Bespoke `.search-input-big` + raw `<input>` | Inputs (search) |
| FolderPicker path bar input | FolderPicker modal | Raw mono `<input>` (not the base Input) | Inputs |
| Onboarding API-key field (label + trailing status icon + validation message) | Onboarding | `KeyField` composite over `.input.mono` | Inputs (with validation) |
| Login username/password fields | Login | Raw `<input className="input">` | Inputs |

### Textarea

Kira has **no multi-line free-text input** anywhere in the catalogued surfaces. The naming template editor is contenteditable (token chips), not a textarea. → No current usage; adopt UUI `Textarea` only if/when a multi-line field is introduced.

### Select / Dropdowns

A single bespoke generic `Select<T>` (`ui.tsx:624+`) — trigger styled like `.input` + a body-portal popup with arrow-key nav, click-outside/Escape, check-on-selected, `mono` variant — is the app's universal dropdown. It is **the** replacement target for UUI `Select` everywhere; native `<select>` is not used.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Single-value Select (operation filter, history retention, on-conflict, retention/auto-purge, provider language, profile/quality/audio/root-folder, monitor seasons, per-folder behaviour, SDH/Forced variant prefs, min-score) | History, Advanced, Paths, Connections, Integrations, Subtitles, Cleanup | Bespoke `Select<T>` (ui.tsx) | Select |
| Select with secondary line per option (root-folder w/ free space) | Integrations | Bespoke `Select` (option.secondary) | Select (with supporting text) |
| Chip-picker trigger ("Add a language…" / "Add a source…") | Subtitles | Bespoke `Select` feeding removable chips | Select + Tags (multi-select) |
| Notifications dropdown panel, settings sub-nav disclosure | Topbar bell, Sidebar | Bespoke popovers (not Select, but same dropdown family) | Dropdowns |

### Checkboxes

Custom tri-state `Checkbox` (`.cb`, role=checkbox, checked/indeterminate/disabled) in ui.tsx is shared; native checkboxes leak in a few modals/onboarding.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Select-all (tri-state) + per-row checkbox | History, Review | Shared `.cb` Checkbox (ui.tsx) | Checkboxes |
| Bulk-select overlay toggle on cover | LibraryGrid cover cards | `.cc-select` button (custom overlay) | Checkboxes (selection) |
| Acknowledge "I understand" checkbox | CoverPopup delete/bulk-delete modals | Native `<input type=checkbox>` inline | Checkboxes |
| Selectable content-type cards (Movies/TV/Anime/Music) | Onboarding step 1 | `<button role=checkbox>` `.ct-card` + `.ct-check` | Checkboxes (checkbox cards) |
| Watch-folder checkbox row (label + sub) | Onboarding step 3 | Native `<input>` + `.onb-checkrow` | Checkboxes (with text) |
| NFO/Artwork per-field grids (toggles used as checkbox group) | Naming | UUI `Toggle` in a `<fieldset>` grid | Checkboxes (group) |

### Radio buttons / groups

Only the Force-import and Onboarding flows use radios; cards-as-radios should map to UUI radio cards.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Import-mode (Copy / Move) radio group | CoverPopup ForceImport modal | Native `<input type=radio>` inline | Radio groups |
| File-operation cards (Hardlink/Move/Copy/Symlink, single-select) | Onboarding step 4 | `<button role=radio>` `.ct-card` | Radio buttons (cards) |
| Rename-mode cards (In place / Into library) | Onboarding step 4 | `<button role=radio>` `.naming-card` | Radio buttons (cards) |
| Naming-profile cards (Plex / Jellyfin, with tree preview) | Onboarding step 5 | `<button role=radio>` `.naming-card` | Radio buttons (cards) |

### Toggles

A clean UUI-derived `Toggle` (`base/toggle/toggle.tsx`, role=switch, motion spring handle, emerald accent, `isDisabled`) is already the app-wide switch for every boolean setting. Direct 1:1 map — keep.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Every boolean setting (auto-scan, auto-approve, embedded extraction, providers on/off, NFO/Artwork, remember matches, symlink relative, permissions, updates, MediaInfo, tech-tags authoritative, season folders, cleanup toggles, subtitle automation…) | Paths, Matching, Naming, Subtitles, Cleanup, Advanced, Integrations, Connections | UUI `Toggle` (via `SettingRow`/`ProviderField`) | Toggles |

### Sliders

Bespoke `SliderField` (label + optional color dot + native `range` + mono readout) in settings-blocks.tsx; native `<input type=range>` underneath.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Auto-approve threshold; High/Med confidence thresholds (color-tinted, with mono % readout) | Matching, Advanced | `SliderField` over native `range` | Sliders |

### Tooltips

The app relies almost entirely on the **native `title` attribute** for hints — a cross-surface upgrade opportunity to one UUI `Tooltip`.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Status-dot detail + last-checked, eye Show/Hide, invalid-URL format hint | Integrations, Connections | Native `title=` | Tooltips |
| Stale-undo reason, disabled-undo reason, path full-value, op chip | History | Native `title=` | Tooltips |
| Sonarr queue explanation, missing-subs languages, marquee full filename, provider links, row chips | LibraryGrid, CoverPopup | Native `title=` | Tooltips |

### Featured icons

A real UUI `FeaturedIcon` (`base/featured-icons/featured-icon.tsx`, sizes sm/md/lg, colors brand/success/warning/error/gray + arbitrary tint) is widely adopted. The inconsistency to fix: `SectionHeader` hand-rolls its own `.settings-section-icon` chip instead of using it, and EmptyState/some toasts/notification chips hand-roll the box.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Card-header icon chip (every SectionCard, KPI/metric, notification row, toast, provider-logo fallback, modal warning/count chips) | Dashboard, all Settings, Notifications, Toast, CoverPopup sub-modals | UUI `FeaturedIcon` (most); inline boxes in CoverPopup modals, EmptyState | Featured icons |
| Section-header flagship icon (44px, accent variant for Naming) | Every Settings section header | Bespoke `.settings-section-icon(-accent)` — NOT FeaturedIcon | Featured icons |
| Section/category icon chip (TV/Anime/Movies/Albums, needs-matching warning) | LibraryGrid section headers | `.lib-section-icon {key}` | Featured icons (themed) |
| Hero/empty-state illustration glyph | LibraryGrid empty states, Onboarding hero, brand marks | `.hero`, `.mark` | Featured icons (large) |

### Avatars / cover art

The `Poster` primitive (ui.tsx) — image with gradient-initials fallback, poster vs square, xs/sm/md/lg — is one of the most-reused atoms. No true UUI equivalent (it's domain cover-art); UUI `Avatars` is the closest for the image-with-fallback mechanics.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Poster / cover-art tile (with initials + year fallback) | Review, History, Dashboard fan, LibraryGrid, ManualSearch results, CoverPopup, FileDetails | `Poster` (`.poster`, ui.tsx); `HistPoster` lazy wrapper | Avatars (image + fallback) — partly Custom |
| Cover hero slot / flying cover | CoverPopup | `.cx-hero-cover-slot`, `.cx-flying-cover` | Avatars / Custom |
| Provider monogram avatar (brand SVG + FeaturedIcon fallback) + corner status dot | Connections, Integrations | `ProviderLogo` + abs status dot | Avatars (with indicator) |

### Alerts / banners

A real UUI `Alert` (`base/alert/alert.tsx`, tones info/warning/error/success + icon + title) exists and is used for provider warnings, ban countdowns, fallback hints, Sonarr success/error, notifications-offline. Many **inline callouts** still hand-rolled (FolderPicker error, skipped-files notice, amber inline warnings, subtitle pack callouts, onboarding states) should consolidate.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Provider warning / fallback-chain / ban-countdown | Connections, Integrations | UUI `Alert` (warning/info) + `BanCountdownBanner` | Alerts |
| Sonarr connected / failed / no-profiles | Integrations | UUI `Alert` (success/error/warning) | Alerts |
| Notifications-offline | Topbar bell | UUI `Alert` color=error | Alerts |
| FolderPicker filesystem error | FolderPicker (Paths, Cleanup, Onboarding) | Inline red bordered div (NOT `Alert`) | Alerts |
| Inline amber dependency warnings ("needs key", "enable MediaInfo", non-video mode caption, skipped files) | Matching, Naming, Cleanup, Rename modal | Raw `text-conf-mid` divs / inline boxes | Alerts |
| Subtitle pack callouts (all-packs, pack-ambiguity amber; season-fill blue w/ actions) | Subtitle Browse modal | Inline rgba bordered boxes | Alerts (with actions) |
| Low-confidence warning banner (with Sync CTA) | CoverPopup | Inline `role=alert` div + primary button | Alerts (with action) |
| Onboarding validation states (success/error/checking), default-tab banner, provider-not-configured | Onboarding, ManualSearch modal | `.onb-state` / `.default-tab-banner` / `.onboarding-state` | Alerts |
| Inline error message (danger row / subtitle error / no-candidates) | Advanced, Subtitle Browse | Raw `text-conf-low` text | Alerts (error) |

### Modals

A shared `Modal` shell (ui.tsx:925 — overlay, centered panel, head/title/sub/close-X, body, footer, `size`, Esc-close, focus-trap, focus-restore) backs most dialogs. The big debt: **CoverPopup's three sub-modals (dupe-resolver, delete-confirm, bulk-delete, force-import) and FolderPicker are fully inline-styled**, not the shared shell, and `window.confirm`/`window.confirm`-style native confirms exist.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| App dialog shell (ManualSearch, Rename preview, FileDetails, Shortcuts) | modals.tsx | Shared `Modal` (ui.tsx) | Modals |
| FolderPicker dialog | Paths, Cleanup, Onboarding | `FolderPickerModal` on shared `Modal`, but internals bespoke | Modals |
| Subtitle Browse modal (+ pack sub-view) | CoverPopup browse | Bespoke portal shell (`.cx`-style, anim-pop) | Modals |
| CoverPopup main detail overlay | Review/CoverPopup | Bespoke `.cx-overlay`/`.cx-shell` (origin-anchored) | Modals (large) / Custom |
| Duplicate resolver / delete-confirm / bulk-delete / force-import sub-modals | CoverPopup | Fully inline-styled overlays (own ESC, high z-index) — NOT shared shell | Modals (confirmation) |
| Native destructive confirm (arm "Everything" cleanup) | Cleanup | `window.confirm()` | Modals (confirmation) |

### Slideout menus

Kira has no true edge-docked drawer except the **mobile sidebar**, which slides in as a drawer; the CoverPopup reads like an origin-anchored popover, not a UUI slideout.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Mobile nav drawer (translate-x sidebar) | Global chrome | `.kira-sidebar` translate-x | Slideout menus (mobile) |
| CoverPopup (anchored detail panel) | Review | Bespoke flight/scale popover | Slideout / Modals — Custom |

### Tables

Kira has **no real `<table>`**; everything tabular is a list of bordered "data row" cards. UUI `Tables` is the conceptual target for these row lists (and their bulk-select headers).

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| History rename row (checkbox, poster, title/badges, from→to paths, timestamp, undo) | History | `.hist-card` row | Tables (data row) |
| Subtitle ledger row (score dial, title/meta, badges, actions) | Subtitle History | Raw `rounded-xl border` row | Tables (data row) |
| Subtitle candidate / pack-entry rows | Subtitle Browse modal | `.cx`-style row cards | Tables (data row) |
| CoverPopup paired episode/file row (+ blank/orphan/download/upcoming/skeleton variants) | CoverPopup | `.cx-pair` + variants | Tables (data row) / Custom |
| Trash item row, candidate row, provider/storage rows, per-folder behaviour rows, watch-folder rows | Cleanup, FileDetails, Dashboard, Paths | Raw flex rows | Tables (data rows) |
| Bulk selection action bar / select-all strip | Review, History | Bespoke sticky toolbar + count chip | Tables (bulk-action header) |

### Tabs

UUI-style `SegmentedControl` (`base/segmented/segmented-control.tsx`, role=tablist) is adopted for History view/period toggles, Matching/Naming/Cleanup segmented switches. Underline-style **provider tabs** and **media-type tabs** are bespoke and should map to UUI `Tabs`.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Renames/Subtitles view toggle; Today/Week/All period | History | `SegmentedControl` | Tabs (button style) |
| Media-type tabs (Movies/TV/Anime/Music, underline + colored icon) | Naming editor | `.provider-tabs`/`.provider-tab` CSS | Tabs (underline) |
| Provider tabs (TMDB/TVDB/AniDB/MusicBrainz, dot + soon/setup pill) | ManualSearch modal | `.provider-tabs` + dot + pill | Tabs (with status) |
| Settings section routing | Settings | Sidebar sub-nav (no in-page tabs) | Tabs (conceptual) |

### Segmented controls

Distinct from Tabs: the small inline pill toggles. One shared `SegmentedControl` exists — but a **second** CSS-class `Segmented`/`.seg` lives in ui.tsx and a hand-rolled inline `.seg` in ManualSearch; consolidate to one.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Profile / Rename-mode / Default-op / Anime-numbering selectors | Naming | UUI `SegmentedControl` | Segmented controls |
| Anime crossref (TVDB/TMDB); non-video mode (Off/Subs/Everything) | Matching, Cleanup | UUI `SegmentedControl` | Segmented controls |
| File-op / Naming-profile in Rename preview | Rename modal | Shared `<Segmented>` (ui.tsx `.seg`) | Segmented controls |
| Inline Movies/TV/Both type filter | ManualSearch search bar | Hand-rolled `.seg`/`.seg-btn` (duplicate) | Segmented controls |
| Per-row approve/reject pair | CoverPopup rows | `.seg-pair` | Segmented controls / Button group |

### Empty states

Shared `EmptyState` (ui.tsx: featured-icon chip + title + sub + optional action) is the canonical one — but several surfaces hand-roll lighter dashed-box variants instead, an inconsistency to reconcile.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Activity empty, no-results, nothing-renamed, no-candidates, filtered-to-zero, all-caught-up | Dashboard, History, FileDetails modal, LibraryGrid, Notifications, Subtitle History | Shared `EmptyState` (ui.tsx) | Empty states |
| Dashed inline empties (no watch folders, trash empty) | Paths, Cleanup | Bespoke `border-dashed` one-liner (NOT EmptyState) | Empty states |
| "Coming soon" placeholder card (Radarr) | Integrations | Bespoke dashed card | Empty states |
| First-run library hero (numbered 3-step setup) | LibraryGrid, Onboarding step 0 | `.lib-empty-hero` + `.lib-empty-steps` | Empty states + Progress steps |
| Inline no-options / storage-fallback / pack-empty | Select panel, Dashboard storage, Subtitle Browse | Inline muted text | Empty states (minimal) |

### Loading / Progress indicators

Two threads: a shared `ProgressBar` (`base/progress-indicators/progress-bar.tsx`, value/max/indeterminate/color) for bars, and a shared `Skeleton` (ui.tsx `.kira-skeleton`) for shimmer. Several spinners (`IcSpin` + the Button's built-in spinner), circular score rings, and bespoke skeleton arrangements also live here.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Linear progress bar (hero scan, coverage, confidence/library legend bars, scan Scan/Match/Tech rows, candidate confidence bar) | Dashboard, ScanProgress, Rename/FileDetails modal | UUI `ProgressBar`; mini bar in candidate row | Progress indicators (bar) |
| Top wizard progress bar | Onboarding | Bespoke `.onb-progress` (NOT the base bar) | Progress indicators (bar) |
| Circular score / confidence ring (subtitle score dial, confidence donut) | Subtitle History/Browse, Dashboard ConfidenceRing | Hand-rolled SVG `<circle>` arcs | Progress indicators (circular) / Activity gauges |
| Download-progress row (animated fill band) | CoverPopup | `role=progressbar` rAF fill | Progress indicators (bar) |
| Skeleton shimmer (KPI numbers, list rows, posters, ledger rows, cover grid) | Dashboard, History, LibraryGrid, Subtitle History, ManualSearch | Shared `Skeleton` / `.kira-skeleton`; AniDB poster `.shimmer-bar` | Loading (skeleton) |
| Spinner (Scan-now, busy buttons, FolderPicker Go, Subtitle "Searching…", login submit, ffmpeg) | Topbar, modals, FolderPicker, Onboarding, Login | `IcSpin` (CSS spin) + Button built-in spinner (two mechanisms) | Loading (spinner) |
| Activity pill / ScanProgress glass cards | Global toast slot | Bespoke `ActivityIndicator` / `ScanProgress` | Loading / Notifications |

### Breadcrumbs

Single instance.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Topbar breadcrumb trail ("Workspace / Dashboard", animated leaf) | Global chrome | Bespoke flex row + `/` separators + `motion.b` | Breadcrumbs |

### Page headers

A shared `.page-header`/`.page-title`/`.page-sub` trio is the app-wide page header; Settings uses `SectionHeader` (banner with featured icon + title + purpose + status/filter slot). Both map to UUI `Page headers` (Settings closer to a Section header).

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Page header (title + subtitle + action cluster) | Dashboard, Review, History, Settings shell | `.page-header` / `.page-title` / `.page-sub` | Page headers |
| Section identity banner (icon + title + purpose + status + filter) | Every Settings section | `SectionHeader` (settings-blocks.tsx) | Page headers (section variant) |
| Marketing hero (Dashboard hero, Welcome hero, Login title) | Dashboard, Onboarding, Login | Bespoke `.dash-hero` / `.onb-hero` / `.login-card h1` | Page headers — mostly Custom |
| Modal title + sub | All modals | `.modal-title` / `.modal-sub` | Page headers (dialog title) |

### Section headers

Smaller in-content headings and eyebrows.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Sub-group headings (Confidence, Experimental boosts, Sources, Variants, Automatic fetching, Per-type overrides, Behavior on add…) | Matching, Subtitles, Integrations | Raw `text-[13px] font-semibold` / uppercase eyebrows | Section headers |
| Uppercase eyebrow labels (Workspace, settings sub-nav groups, "N RENAMES", step eyebrow) | Sidebar, History, Onboarding | Raw `text-[10–11px] uppercase tracking` | Section headers (eyebrow) |
| Media-type / franchise / day-group / list headers | LibraryGrid, History, CoverPopup | `.lib-section-head`, `.lib-franchise-head`, `.hist-day-head`, `.cx-list-head` | Section headers |
| Step eyebrow (Step N of 6 + Required/Optional) | Onboarding | `.onb-eyebrow` + `.step-n` | Section headers + Badge |

### Card headers

The header region of cards (icon + title + supporting text + optional action). Dominant in Settings (`SectionCard`) and Dashboard cards.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| SectionCard header (FeaturedIcon + 15px title + desc + action/headerExtra) | Every Settings section | `SectionCard` (settings-blocks.tsx) | Card headers |
| Dashboard card header (icon + title + CardLink) | Dashboard | Local `Card` header (DashboardPage) | Card headers |
| Notifications / ScanProgress / toast headers, breadcrumb card in Naming | Topbar, ScanProgress, Naming | Bespoke header rows | Card headers |

### Content dividers

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Card header divider (`SETTINGS_DIVIDER`), within-card hairlines | All Settings cards | `border-[var(--border-1)]` border-t/b | Content dividers |
| Vertical divider in bulk bar | Review | Inline `w-px bg-accent-line` span | Content dividers (vertical) |
| Day divider line, dot separators (`.dot-sep`), modal header border, danger-zone red rule | History, LibraryGrid/CoverPopup, modals, Advanced | `.hist-day-line`, `.dot-sep`, `border-b`, inline red `h-px` | Content dividers |

### Activity feeds / timelines

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Recent activity timeline (colored nodes, event text, relative time, ghost fade rows) | Dashboard | `.dash-timeline*` | Activity feeds |
| Day-grouped rename timeline (rail thread + op-colored nodes) | History | `.hist-rail`/`.hist-node` | Activity feeds (timeline) |
| Notification list (icon + title + body + time) | Topbar bell | Notification rows | Activity feeds / Notifications |

### Metrics / KPI cards

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| KPI metric cards (Library/Matched/Pending/Organized, animated count-up) | Dashboard | Local `Metric` + `CountUp` | Metrics (metric cards) |
| Inline stat lines ("N pending · N matched…", footer summaries) | Review, History, CoverPopup | Bold colored counts + `.sep`/middot | Metrics (inline) — partly Custom |
| Numeric KPI readouts (score in dial, slider %, storage size, monospace counters) | Subtitle, Settings sliders, Dashboard, ScanProgress | `font-mono tabular-nums` spans | Metrics (numeric text) |

### Pagination

No paginator. Lists use scroll regions with render caps ("…and N more") instead.

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| "…and N more" overflow note (200-cap), "+N more files" | Cleanup trash, ManualSearch | Inline caption | (none — Custom) → UUI Pagination if real paging is added |

### Sidebar navigation

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Sidebar shell + collapse/drawer; primary nav items + morphing active pill; nested settings sub-nav + active marker; section/group labels; status footer; counts/unread dots | Global chrome | `.kira-sidebar`, `.kira-nav-item`, `.kira-nav-active`, layout-id morphs | Sidebar navigations |

### Notifications

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Toast stack + toast card (featured icon + title + sub + dismiss) | Global | `.toasts` + bespoke `motion.div` cards | Notifications (toast) |
| Notifications dropdown (panel + rows + unread + mark-all-read) | Topbar bell | Bespoke `role=dialog` popover + rows | Notifications |
| Activity pill / ScanProgress live status | Global toast slot | `ActivityIndicator` / `ScanProgress` | Notifications (snackbar) |

### Command menu / search

| App element | Surfaces | Current impl | → UUI component |
|---|---|---|---|
| Global topbar search (+ `/` shortcut) | Global chrome | `.topbar-search` (Input candidate) | Command menu / search (input) |
| ManualSearch provider search (tabs + big input + result grid) | Rename/ManualSearch modal | Bespoke modal | Command menu / search (in dialog) |

---

## Per-surface inventory

> One subsection per surface. Element names match the Universal map. "→ UUI" gives the target component (or "Custom").

### Dashboard

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Hero band | Glassy hero (status badge + title + subline + actions + poster fan) | DashboardPage.tsx:498–568 | Bespoke `.dash-hero*` + Tailwind | Page headers — Custom hero |
| Live status badge | Pill + pulsing dot (Live/Scanning/Matching; provider status) | :506, :695 | UUI `BadgeWithDot` | Badges (dot) |
| Hero title | Gradient-clip "Welcome back" h1 | :509 | Raw `<h1>` `bg-clip-text` | Custom (typography) |
| Hero subline / relative time | Summary line w/ bold spans | :532, :536 | Raw `<p>` text-tertiary | Custom (body) |
| Primary "Scan now" | Emerald CTA w/ spinner | :553 | UUI `Button` primary | Buttons (primary) |
| Secondary Re-parse / Get all | Grey buttons w/ icon + loading | :542, :310 | UUI `Button` secondary | Buttons (secondary) |
| CardLink "see more" | Text-link + arrow per card | :99, used :301/621/682/748 | Local wrapper over `Button` link-gray | Buttons (link) |
| Inline scan progress bar | Determinate/indeterminate bar | :513–531 | UUI `ProgressBar` | Progress (bar) |
| Scan phase counter | Mono `{pct}%` / `{found} found` | :519 | Raw `font-mono` span | Custom (numeric) |
| Poster fan | Decorative blurred poster cluster | :241, render :500 | Local `PosterFan` raw `<img>` | Custom (cover collage) |
| KPI metric card | Icon + label + count-up + sub | :144, render :572 | Local `Metric` + `FeaturedIcon` + `CountUp` | Metrics |
| Featured icon | Tinted icon chip | :158 + card headers | UUI `FeaturedIcon` | Featured icons |
| Count-up number | Tweened KPI value | :120 | Local `CountUp` | Custom (anim) |
| KPI alert sub-line | "N low-confidence" red text | :595 | Inline `text-error-primary` | Custom / Alert |
| Generic Card shell | Rounded glass card | :59, used ×6 | Local `Card` `.dash-card` | Card headers — Custom shell |
| Card header / title | Icon + title + action + divider | :79, :86 | Raw flex; `<h2 text-sm font-semibold>` | Card headers |
| Hover glow accent | Radial glow on hover | :76, :156 | Abs `<div>` radial gradient | Custom (effect) |
| Confidence ring | Donut gauge + center % | :182, render :625 | Hand-rolled SVG arcs + `CountUp` | Progress (circular) — Custom |
| Bucket legend rows | Dot + label + bar + count | :637 | Raw rows + `ProgressBar` | Progress (bar) + Badge dot |
| Status dot | 2px colored dot | :643, :723 | Bare `<span rounded-full>` | Badges (dot) |
| Library composition rows | Type icon + label + bar + count | :656 | Raw rows + `ProgressBar` | Progress (bar) |
| Provider status rows | Name + note + `BadgeWithDot` | :679 | Raw rows + UUI `BadgeWithDot` | Tables (rows) + Badges |
| Storage stacked bar + legend | Total size + segmented bar + per-type legend | :702 | Inline-styled segments (NOT ProgressBar) | Custom (stacked bar) |
| Storage empty fallback | File count + "size unavailable" | :731 | Centered flex | Empty states (inline) |
| Subtitle coverage card | % + Get-all + bar + lang chips | :263, render :741 | `Card` + `FeaturedIcon` + `ProgressBar` + `Button` | Composite |
| Per-language missing chips | Mono lang + "N missing" | :316 | Raw `<span>` | Tags |
| Coverage progress bar | Covered/inspected | :315 | UUI `ProgressBar` | Progress (bar) |
| Recent activity timeline | Event nodes + text + time + ghost rows | :744, list :772 | `.dash-timeline*` | Activity feeds |
| Timeline node | check/x/sparkle status node | :779 | `.dash-timeline-node` | Custom (feed item) |
| Relative timestamp | "Xm ago" caption | :784, :536 | Raw `text-quaternary` | Custom (caption) |
| Activity skeleton | 7 placeholder rows | :753 | Shared `Skeleton` | Loading (skeleton) |
| Activity empty state | Icon + title + sub | :763 | Shared `EmptyState` | Empty states |
| Timeline ghost rows | Fade-out filler | :788 | `.dash-timeline-ghost` | Custom |
| Page grid layout | KPI strip + main + rail grid | :497/571/615/678 | Raw grid + `.page`/`.anim-*` | Custom (layout) |
| Plain icons | scan/check/x/film/tv/etc. | :5 | lib/icons SVGs | Custom (icon set) |

### Review queue (ReviewPage)

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Page title "Library" | H1 | ReviewPage.tsx:329 | `.page-title` | Page headers |
| Stat / status summary line | Inline KPIs w/ colored counts + `.sep` | :330 | Raw Tailwind + `<b>` | Metrics (inline) — Custom |
| Numeric KPI / pill count | Colored count + FilterPill count badge | :331; ui.tsx:838 | `<b>` + tabular-nums span | Badges / Metrics |
| Primary "Rename N / Approve & rename" | Emerald CTA + loading | :338, :443 | UUI `Button` primary | Buttons (primary) |
| Secondary actions (Select high-conf, Clear, Preview, Match…) | Neutral buttons | :355/413/429/436 | UUI `Button` secondary | Buttons (secondary) |
| Destructive "Reject" | Red secondary | :414 | UUI `Button` secondary-destructive | Buttons (destructive) |
| FilterPill | Toggle chip + count (status/conf/media) | :363; ui.tsx:819 | Bespoke `FilterPill` | Tags (toggle) |
| FilterGroup | Inset segmented filter bar | :362; ui.tsx:851 | Bespoke `FilterGroup` | Filter bars / Button groups |
| Status dot | Conf/no-match dot in pill | :367/383 | Inline `<span rounded-full>` | Badges (dot) |
| Inline media-type icon | Film/TV/Anime/Music glyph | :390 | Raw SVG | Custom (icon) |
| Bulk action bar | Sticky selection toolbar | :399 | Raw glass card | Custom (selection bar) |
| Selection count chip | Check square + "N selected" | :401 | Raw tinted square + bold text | Badges / Featured icons |
| Vertical divider | Hairline in bulk bar | :426 | Inline `w-px` | Content dividers (vertical) |
| Button-group cluster | Grouped bulk actions | :412 | Raw flex | Button groups |
| Library grid | Poster/card grid (child) | :469; LibraryGrid.tsx | `LibraryGrid` | Custom (catalogued separately) |
| Cover popup | Per-cluster detail overlay | :493; CoverPopup/ | `CoverPopup` | Slideout / Modals (separate) |
| Manual search modal | Bulk match-all-to | :505; modals.tsx | `ManualSearchModal` on shared `Modal` | Modals |
| Scan progress banner | Live scan (relayed to grid) | :304/483 | `ScanProgress` | Progress / Notifications |
| Empty/loading gating | Delegates to LibraryGrid | :320 | Conditional guard | Empty / Loading (downstream) |

### History

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Page header (title + sub + actions) | Header row | HistoryPage.tsx:358 | `.page-header/.page-title/.page-sub` | Page headers |
| Secondary "Clean undo leftovers" | Button + sparkles + loading | :369 | UUI `Button` secondary | Buttons (secondary) |
| "Export CSV" download link | Button as `<a download>` | :379 | UUI `Button` secondary (href) | Buttons (link) |
| Renames/Subtitles view toggle | 2-seg control | :387 | `SegmentedControl` | Tabs |
| Period filter (counts) | 3-seg control | :399 | `SegmentedControl` | Tabs |
| Operation filter Select | 180px dropdown | :408; ui.tsx:624 | Bespoke `Select` | Select |
| Search box | Magnifier + input + clear | :422 | Bespoke `.hist-search` | Inputs (search) |
| Clear-search × | Ghost icon button | :434 | Raw `<button>` | Buttons (utility) |
| Bulk-undo bar | Pill toolbar (count + Undo selected) | :446 | `.hist-bulkbar` | Custom (selection bar) |
| "N selected" label | Count text | :448 | Raw span | Custom |
| Destructive "Undo selected" | Red secondary | :449 | UUI `Button` secondary-destructive | Buttons (destructive) |
| Select-all strip | Master checkbox + caption | :457 | `Checkbox` + uppercase caption | Checkboxes + Section header |
| Checkbox (tri-state) | Header + per-row | :458/526; ui.tsx:565 | `.cb` Checkbox | Checkboxes |
| Section/count caption | Uppercase eyebrow | :459 | Raw `text-[11px] uppercase` | Section headers (eyebrow) |
| Loading skeleton list | 4 placeholder rows | :464 | `Skeleton` arrangement | Loading (skeleton) |
| Day-grouped timeline rail | Vertical thread + nodes | :486; css 1281 | `.hist-rail`/`.hist-row` | Activity feeds |
| Day header (label + count + line) | Per-day | :490 | `.hist-day-*` | Section headers + dividers |
| Day-count pill | Count pill | :492 | `.hist-day-count` | Badges |
| Day divider line | Gradient hairline | :493 | `.hist-day-line` | Content dividers |
| Timeline node | Op-colored dot | :521; css 1301 | `.hist-node-dot` | Custom (timeline node) |
| History row card | Data row | :525; css 1322 | `.hist-card` | Tables (data row) |
| Row poster | Lazy AniDB cover | :64/532; ui.tsx:18 | `Poster` + `HistPoster` | Avatars |
| Row title / episode subtitle | Title + ep | :535/538 | Raw spans | Custom (text) |
| Operation badge | MOVE/COPY/… chip | :541; css 1336 | `.hist-op*` | Badges (color) |
| "Undone" / stale-undo pills | Status pills (+ tooltip) | :542/543 | `.hist-undone-pill`/`.hist-stale-pill` | Badges (+ Tooltip) |
| Source/destination path lines | Mono from → to | :545/548 | Raw `font-mono` + icon | Custom (mono path) |
| Relative timestamp | Right caption | :552 | Raw span | Custom (caption) |
| Per-row Undo button | Secondary + icon, disabled | :558 | UUI `Button` secondary | Buttons (secondary) |
| "Restored" tag | Green success flash | :556; css 1374 | `.hist-restored-tag` | Badges (success) |
| Empty states (no results / nothing renamed) | Centered | :584/591; ui.tsx:902 | `EmptyState` | Empty states |
| Featured icon chip (in EmptyState) | 64px icon box | ui.tsx:915 | Hand-rolled box (not FeaturedIcon) | Featured icons |
| Toasts (triggered) | Undo/cleanup outcomes | various; ui.tsx:513 | `pushToast` | Notifications |
| Subtitles sub-surface | Delegated tab | :395 | `SubtitleHistory` | (separate surface) |

### Subtitle History

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Loading skeleton | 3 placeholder bars | SubtitleHistory.tsx:71 | Shared `Skeleton` | Loading (skeleton) |
| Empty state | No subtitles fetched | :78 | Shared `EmptyState` | Empty states |
| Ledger row card | Score dial + meta + actions | :94 | Raw `rounded-xl border` row | Tables (data row) |
| Score dial | 44px radial gauge | :101 | Hand-rolled SVG | Progress (circular) |
| Numeric score | KPI in dial | :108 | Abs `font-bold tabular-nums` | Metrics (numeric) |
| Row title | Title / "File #id" | :113 | Conditional spans | Custom (title) |
| Language pill | Uppercase code | :116 | Raw `<span>` recipe | Badges / Tags |
| Sync-status badge | in sync / likely / unknown | :11/119 | `SYNC_STYLE` span | Badges (color) |
| SDH / Forced badges | Neutral pills | :122/123 | Raw `<span>` | Badges / Tags |
| Blacklisted badge | Red pill | :124 | Hardcoded red `<span>` | Badges (error) |
| Provider / release / reasons / time meta | Caption + mono release | :126–130 | Spans + `.dot-sep` | Custom (caption/mono) |
| Dot separator | Middot | :128 | `.dot-sep` | Content dividers (inline) |
| Delete button | Secondary + trash | :136 | UUI `Button` secondary | Buttons (secondary) |
| Blacklist button | Destructive secondary | :140 | UUI `Button` secondary-destructive | Buttons (destructive) |
| Action group | Right-aligned cluster | :134 | Raw flex | Button groups |
| Inactive status (removed/gone) | Icon + text | :146 | Inline icon+text | Badges (status) — Custom |
| Plain icons | trash/alert/check/caption | :3 | lib/icons | Custom |
| Toast (pushed) | Load/delete outcomes | :47 | `pushToast` | Notifications |

### Settings — shell

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Page header bar | Title/sub + save indicator | SettingsPage.tsx:561 | `.page-header` | Page headers |
| Page title / subtitle | H1 + muted line | :563/564 | `.page-title`/`.page-sub` | Page headers |
| Save-status pill (+ dot) | Saving/Saved/Failed | :566/576 | Inline `.save-indicator*` + dot | Badges (dot) |
| Section router container | Keyed remount wrapper | :589 | Plain keyed `<div>` | Custom (routing shell) |
| Floating save bar | Unsaved-changes action bar | :1512 | Bespoke glass panel (`anim-pop`) | Custom (docked action bar) |
| Pending-change chip / "+N more" | Label pills + overflow | :1525/1528 | Inline `rounded-full` span | Tags / Badges |
| Save-bar status message | "N unsaved" / invalid-URL | :1534 | Inline span + emphasis | Custom / Alert |
| Cancel / Save buttons | Secondary / primary | :1541/1544 | UUI `Button` | Buttons |
| Save-bar entrance anim | Scale/pop | :1519 | `.anim-pop` | Custom (motion) |

### Settings — Connections

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header banner | Icon + title + purpose + status | :592; settings-blocks.tsx:143 | `SectionHeader` | Page headers |
| Section featured icon | 44px chip (IcLink) | sb:156 | `.settings-section-icon` (not FeaturedIcon) | Featured icons |
| StatusPill (section + provider) | "N of M connected" / per-provider | sb:115; :597/729 | Bespoke `StatusPill` | Badges (dot) |
| Media-type badge | MOVIE/TV/… chips | badges.tsx:25; sb:717 | UUI `Badge` | Badges |
| ProviderCard | Collapsible provider tile ×9 | sb:645; :616–788 | Bespoke `ProviderCard` | Custom (card + accordion) |
| Provider logo + corner dot | Brand SVG / FeaturedIcon fallback | sb:626/705 | `ProviderLogo` + abs dot | Avatars (+ indicator) |
| Expand chevron | Rotating disclosure | sb:730 | `IcChevDown` rotate | Buttons (utility) / accordion |
| Test-connection button | Secondary + refresh + loading | sb:723 | UUI `Button` secondary | Buttons (secondary) |
| Provider field — text/password/select/toggle | Credential fields (+ eye, lang Select, auto-fingerprint) | sb:462–547 | UUI `Input`/`Select`/`Toggle` via `ProviderField` | Inputs / Select / Toggles |
| Field label/desc/disabled-reason | Typography block | sb:480 | Raw stacked divs | Custom (form text) |
| Ban-countdown / warning / fallback alerts | Provider callouts | sb:574/742/743 | UUI `Alert` (warning/info) | Alerts |
| Card stage / dual flex columns | Cascading layout | sb:65; :614 | `.settings-stage` + flex | Custom (layout) |
| Card divider | Header/body hairline | sb:739 | `SETTINGS_DIVIDER` | Content dividers |
| Plain icons | link/refresh/chev/eye/media | sb:5 | lib/icons | Custom |

### Settings — Library & paths

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header / StatusPill | Banner + state pill | PathsSection.tsx:167/173 | `SectionHeader` + `StatusPill` | Page headers + Badges |
| SectionCard ×4 | Media root / destinations / watch / auto-scan | :186–376 | `SectionCard` + `FeaturedIcon` | Card headers |
| PathField | Mono input + Browse/Clear | :20/191/219 | UUI `Input` + `PATH_ICON_BTN` | Inputs (with trailing) |
| Text input (Ignore patterns) | Base glass field | :233; input.tsx | UUI `Input` | Inputs |
| Numeric stepper (InputNumber) | Settle/Poll/threshold | :309/321/352 | UUI `InputNumber` | Inputs (number) |
| Select (per-folder behaviour) | Scan vs auto-rename | :339; ui.tsx:624 | Bespoke `Select` | Select |
| Toggle (Auto-scan) | On/off switch | :292 | UUI `Toggle` | Toggles |
| Secondary "Add folder" | Button + plus | :258 | UUI `Button` secondary | Buttons (secondary) |
| Destructive remove icon | Trash icon button | :273 | Raw `<button>` (error hover) | Buttons (utility) |
| NestedBox | Inset sub-setting panel | :230/270/304; sb:350 | `NestedBox`/`SETTINGS_NESTED` | Custom (inset) |
| Per-type destination row | Type icon + label + PathField | :213 | Raw flex row | Tables (rows) |
| Watch-folder / per-folder behaviour rows | Folder icon + mono path + control | :269/333 | Raw flex rows | Tables (rows) |
| Persistence dot + caption | Native/index dot + text | :196 | Inline dot + caption | Badges (dot) / Alert |
| Empty state (no watch folders) | Dashed box | :264 | Inline `border-dashed` (not EmptyState) | Empty states |
| Helper/caption + field labels | Hints + inline labels | :243/215 | Raw text utilities | Custom (text) |
| Inline mono path/token | Filesystem snippets | :210/216 | `font-mono` spans | Custom (mono) |
| FolderPickerModal | Browse dialog | :380; FolderPickerModal.tsx | `FolderPickerModal` on `Modal` | Modals (catalogued separately) |
| 2-col layout shell | SettingsLayout + inline grid | :166; sb:43 | `SettingsLayout` | Custom (layout) |

### Settings — Integrations

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header + status pill | Banner + "N configured" | IntegrationsSection.tsx:387/394 | `SectionHeader` + `StatusPill` | Page headers + Badges |
| SectionCard ×4 | Sonarr / webhook / media servers / notifications | :408/537/580/604 | `SectionCard` | Card headers |
| Card title + health dot | Title + inline status dot | :79/410 | `titleWithDot` + `StatusDot` | Card headers + Badge dot |
| StatusDot (tri-state) | Green/red/grey + title | :51/586/593 | Local `StatusDot` | Badges (dot) / Tooltip |
| BadgeWithDot (Sonarr / Coming soon) | Status badge | :414/573; badges.tsx:42 | UUI `BadgeWithDot` | Badges (dot) |
| Test-connection button | Secondary + refresh + loading | :415 | UUI `Button` secondary | Buttons (secondary) |
| Text/URL field (editGate, invalid) | Service URL fields | :431/588; input.tsx | UUI `Input` | Inputs |
| Password + eye toggle | Secret fields (×5) | :436/447; secretEye :127 | UUI `Input` + raw eye button | Inputs (password) |
| Eye-toggle icon button | Reveal secret | :127/447 | Raw `<button>` | Buttons (utility) |
| Toggle (Use season folders) | Switch | :505 | UUI `Toggle` | Toggles |
| Select (type/quality/audio/root/monitor) | Dropdowns (+ secondary line) | :366/510; ui.tsx:624 | Bespoke `Select` | Select |
| Alerts (success/error/warning) | Sonarr connection results | :461/466/526 | UUI `Alert` | Alerts |
| Featured icon | Card header glyph | :565; sb:247 | UUI `FeaturedIcon` | Featured icons |
| NestedBox / flavor cards | Inset panels + TV/Anime config | :358/505/547 | `SETTINGS_NESTED`/`NestedBox` | Custom (inset) + Select rows |
| FieldRow | Aligned label + control | :342; sb:330 | `FieldRow` | Custom (form row) |
| Eyebrow labels | Uppercase sub-headings | :479 | Raw uppercase | Section headers (eyebrow) |
| Card desc / mono URL display / captions | Body text + read-only URL | :411/546/554 | Raw text + `font-mono` | Custom (text/code) |
| Radarr "coming soon" card | Dashed placeholder | :562 | Bespoke dashed card | Empty states |
| Inline divider | Within-card hairline | :478 | `border-t` | Content dividers |
| 2-col grid + SettingsLayout | Layout | :405/622; sb:86 | Raw grid (dupes `SettingsGrid`) | Custom (layout) |
| Native title tooltips | Hover hints | :68/131/431 | `title=` | Tooltips |

### Settings — Matching

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header + featured icon + status pill | Banner | SettingsPage.tsx:1310; sb:143 | `SectionHeader` | Page headers + Featured icons + Badges |
| SectionCard ×5 | Source / auto-approve / thresholds / boosts / runtime | :1321/1412/1438/1482/1489 | `SectionCard` | Card headers |
| Sub-group headings | Confidence / Experimental boosts | :1409/1478/1390 | Raw `font-semibold` | Section headers |
| Provider preference list | Ranked reorderable list ×3 | :1346 | Hand-rolled `<ol>` | Custom (reorder list) |
| Rank number badge | Position chip | :1352 | Raw `<span rounded-md>` | Badges (number) |
| Reorder up/down buttons | Chevron icon buttons | :1359 | Raw `<button>` | Buttons (utility) |
| "primary" / "needs key" tags | Inline text tags | :1355/1356 | Bare colored text | Badges (small) |
| Provider hint / not-configured warning | Captions + amber inline | :1374/1375 | Raw `text-conf-mid` | Custom + Alerts |
| Anime crossref segmented | TVDB/TMDB | :1392; segmented-control.tsx | `SegmentedControl` | Segmented controls |
| In-card dividers | Sub-block rules | :1389/1477 | `border-t` | Content dividers |
| SettingRow (auto-approve) | Label + toggle | :1418; sb:280 | `SettingRow` | Custom (form row) |
| Toggle | Auto-approve / boosts / runtime | :1419/1486/1493 | UUI `Toggle` | Toggles |
| NestedBox | Inset (threshold slider) | :1421; sb:350 | `NestedBox` | Custom (inset) |
| SliderField | Threshold + High/Med (color + mono) | :1422/1444; sb:368 | `SliderField` | Sliders |
| Confidence color dots | High/Med/Low swatches | :1446/1466 | Inline dots | Badges (dot) |
| Low-confidence summary row | Static threshold readout | :1464 | Raw flex (mirrors slider) | Custom (metric row) |
| LabsChip | Off by default / Experimental / Needs MediaInfo | :1479/1484/1491 | Local `LabsChip` | Badges |
| Toggle-only card (headerExtra) | Episode boost / runtime | :1482 | `SectionCard headerExtra` | Card headers |
| Cross-section link | "Advanced" inline link | :1492 | Raw `<button>` underline | Buttons (link) |
| Runtime dependency warning | Amber inline callout | :1495 | Raw `text-conf-mid` | Alerts |
| SettingsLayout | Staggered card stage | :1309; sb:43 | `SettingsLayout` | Custom (layout) |

### Settings — Naming & format

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header (accent icon) + status pill + filter | Banner | SettingsPage.tsx:806; sb:143 | `SectionHeader` + `StatusPill` + `SettingsFilter` | Page headers + Featured icons + Badges + Inputs |
| Settings filter input | Filter settings… | :812; sb:179 | Bespoke `SettingsFilter` | Inputs (search) |
| SectionCard ×4 | Profile / file handling / cleanup / sidecar | :817/852/927/941 | `SectionCard` | Card headers |
| Profile segmented (Plex/Jellyfin/Kodi/Custom) | 4-seg | :823 | `SegmentedControl` | Segmented controls |
| Media-type tabs | Movies/TV/Anime/Music (underline + icon) | sb:1224/1289 | `.provider-tab` CSS | Tabs (underline) |
| Editor pane sub-headers / lock hints | Pane captions | sb:1303/1362/1394 | `.naming-pane-head`/`.naming-lock` | Section headers — Custom |
| Template chip editor | Contenteditable token field | sb:1032/1309 | Custom contentEditable + `.tpl-editor` | Custom (token editor) |
| Inline template chip | Token pill + × | sb:905; css 2953 | `.tpl-chip`/`.tpl-chip-x` | Tags (dismissible) |
| Token palette chips (kbd keycaps) | Draggable `{{token}}` | sb:1366; css 2906 | `.token-chip`/`.kbd` | Tags / Badges (keycap) |
| Filters hint lines | Inline `<code>` filter help | sb:1357/1384 | `.naming-hint code` | Code snippets — Custom |
| Live template preview | Rendered path list + morph | sb:1407/1393 | `LiveTemplatePreview` `.naming-preview-*` | Custom (preview) |
| Mono path / `.seg-new` | Path diff typography | css 1189; sb:1453 | `.preview-path`/`.seg-new` | Code snippets — Custom |
| Preview loading/empty/error | Text states | sb:1428; css 3050 | `.naming-preview-empty` | Empty/Loading (text) |
| SettingRow (stacked/inline) | Label + desc + control | :858/947; sb:280 | `SettingRow` | Custom (form row) |
| Rename-mode / file-op / anime-numbering segmented | Toggles | :864/886/908 | `SegmentedControl` | Segmented controls |
| File-op explainer callout | Inset info + BEST FOR/WATCH OUT tags | :898/1614 | `FileOpExplainer` + uppercase pills | Alerts + Badges |
| Folder-cleanup breadcrumb card | Header + "Open" button | :927 | `SectionCard` + `Button` (trailing icon) | Card headers + Buttons |
| NFO toggle + nested field picker | Toggle + grid of field toggles + M·S·E dots + legend | :947 | `SettingRow`+`Toggle`+`NestedBox`+`NfoTargetDots` | Toggles + Checkboxes (group) |
| M·S·E target dots / legend | Mono keycap indicators | :55/967/986 | `NfoTargetDots` | Badges (square keycap) |
| Artwork toggle + nested kind picker | Toggle + kind grid (+ needs-key) | :1000 | Same reveal-grid pattern | Toggles + Checkboxes (group) |
| "needs key" tag / inline links | Amber tag + Connections links | :1034/1050 | Bare text / `<button>` underline | Badges / Buttons (link) |
| Field hint suffix / inline mono | Parenthetical hints + filenames | :984/963 | `text-ink-soft` / `font-mono` | Custom (text/code) |
| 2-col card grid | Layout | :850/939; sb:86 | Raw grid | Custom (layout) |

### Settings — Subtitles

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header (accent icon) + filter | Banner | SettingsPage.tsx:1066; sb:143 | `SectionHeader` + `SettingsFilter` | Page headers + Featured icons + Inputs |
| SettingsLayout | Page shell | :1064; sb:43 | `SettingsLayout` | Custom (layout) |
| Main SectionCard | Subtitles card | SubtitlesCard.tsx:159 | `SectionCard` + `FeaturedIcon` | Card headers + Featured icons |
| NestedBox ×5 | Connection/Sources+Variants/Auto/Advanced | :168/217/289/314 | `NestedBox` | Custom (inset) |
| Sub-group / sub-sub labels | Eyebrows + medium labels | :218/340 | Raw uppercase / medium | Section headers (eyebrow) |
| Connection-status row | check/warn + text + Connect button | :168 | Raw row + `Button` | Alerts (status) + Buttons |
| Inline status icon + text | success/warning glyph | :170; FfmpegStatus.tsx:53 | Inline colored icon+text | Badges (status) — Custom |
| Secondary buttons (Connect / Install / Upgrade) | sm + leading icon + loading | :179/420; ffmpeg:62 | UUI `Button` secondary | Buttons (secondary) |
| Languages multi-select (global) | Add-language Select + chips | :184 | Bespoke `Select` + chips | Select + Tags (multi-select) |
| PerTypeChips multi-select ×6 | Per-type lang/source overrides | :41/345/363 | `PerTypeChips` | Select + Tags (multi-select) |
| Removable chip / tag | Lang/source chip + × | :196/58 | Raw `<span>` + IcX | Tags (dismissible) |
| Select (single) | Chip trigger + SDH/Forced prefs | :273/186; ui.tsx:624 | Bespoke `Select` | Select |
| Toggle (boolean settings) | Embedded/providers/automation (×9) | :224/235/296/398 | UUI `Toggle` | Toggles |
| Toggle setting rows (~13) | Label + qualifier + control | :219–415 | Hand-rolled rows (not `SettingRow`) | Custom (form row) |
| Label + inline muted qualifier | Text style | throughout | Span + nested muted span | Custom (text) |
| FieldRow (Languages) | Fixed label column | :184; sb:330 | `FieldRow` | Custom (form row) |
| ffmpeg health row | Live status + install | :227; FfmpegStatus.tsx:16 | `FfmpegStatusRow` | Custom (composite) → Buttons+Badges |
| Number input (score/threshold) | Min-score fields | :323/380/407; input.tsx | UUI `Input type=number` | Inputs (number) |
| Helper paragraphs / inline emphasis | Body copy + pseudo-links | :305/163 | `text-ink-soft` + emphasis | Custom (text) |
| Per-type override rows (×9) | Type label + control | :341/359/377 | Raw flex rows | Custom (mini table) |
| Conditional Upgrade disclosure | Reveal sub-settings | :400 | React conditional | Custom (reveal) |

### Settings — Folder cleanup

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Section header + live status pill | Banner ("On · recycle"…) | SettingsPage.tsx:1114/1123; sb:143 | `SectionHeader` + `StatusPill` | Page headers + Badges |
| SettingsLayout | Page shell | :1113; sb:43 | `SettingsLayout` | Custom (layout) |
| SectionCard ×2 (+ Trash) | Source cleanup / Trash bin | :1131/1793 | `SectionCard` | Card headers |
| SettingRow | Label + desc + control | :1138; sb:280 | `SettingRow` | Custom (form row) |
| Toggle | Remove empty / metadata / trash | :1143/1153/1238 | UUI `Toggle` | Toggles |
| NestedBox | Inset sub-options (dimmed) | :1147; sb:350 | `NestedBox` | Custom (inset) |
| CommaListField | Filenames / extensions | :1166; defined :1666 | `CommaListField` over `Input mono` | Inputs (→ Tags) |
| Text input (base) | Underlying field | input.tsx:29 | UUI `Input` | Inputs |
| Field group (label+caption+control) | Stacked micro-pattern | :1161 | Raw markup (not SettingRow stacked) | Custom (form row) |
| Segmented (non-video mode) | Off/Keep subs/Everything | :1190 | `SegmentedControl` | Segmented controls |
| Dynamic warning caption | Mode explainer (amber) | :1217 | Raw `text-conf-mid` | Alerts (inline) — Custom |
| Native destructive confirm | Arm "Everything" w/o trash | :1200 | `window.confirm()` | Modals (confirmation) |
| Trash-dir field + Browse | Mono input + folder button | :1240; :1692 | `Input mono` + trailing button | Inputs (with trailing) |
| Utility icon buttons | Browse / restore / delete | :1708/1832/1841 | Raw `<button>` ghost | Buttons (utility) |
| FolderPickerModal | Browse dialog | :1719; FolderPickerModal.tsx | `FolderPickerModal` on `Modal` | Modals |
| Modal shell (base) | Dialog chrome | ui.tsx:925 | `Modal` | Modals |
| Transparency disclosure | `<details>` "What gets deleted" | :1258 | Native `<details>` + `FeaturedIcon` | Custom (accordion) |
| Featured icon chip | Header / disclosure glyph | sb:247; :1260 | UUI `FeaturedIcon` | Featured icons |
| Mono path / code text | Deleted-files lists | :1270 | `font-mono` spans | Code snippets — Custom |
| Section sub-labels | Deleted/Never-deleted headings | :1269 | Raw `font-semibold` | Custom (label) |
| Empty trash button (arm-to-confirm) | Secondary→primary destructive + loading | :1799 | UUI `Button` (destructive) | Buttons (destructive) |
| Trash item row | Icon + mono name + meta + actions | :1823 | Raw flex row | Tables (data row) |
| Scrollable list w/ render cap | Max-h + "…N more" | :1820 | Raw scroll div | Custom (viewport) |
| Empty state (trash empty) | Dashed one-liner | :1814 | Inline (not EmptyState) | Empty states |
| Count + size summary | Card desc | :1796 | Text (fmtBytes) | Custom / Metrics |
| Auto-purge Select | Retention dropdown | :1859; ui.tsx:624 | Bespoke `Select` | Select |
| Busy spinner | Per-item / Go button | :1839; picker:83 | `IcSpin animate-spin` | Loading (spinner) |
| FolderPicker error / control buttons / gradient CTA | Picker internals | FolderPickerModal.tsx:87/46/59 | Inline callout / `CTL_BTN` / gradient button | Alerts / Buttons |

### Settings — Advanced

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| SettingsLayout + SectionHeader (gear) + status pill | Shell + banner | AdvancedSection.tsx:106/108/112 | `SettingsLayout` + `SectionHeader` + `StatusPill` | Page headers + Featured icons + Badges |
| 2-col card grid | Layout | :116 | Raw grid | Custom (layout) |
| SectionCard ×4 (incl. danger) | Library/Metadata/Backup/Reset | :118/227/254/288 | `SectionCard` (`tone=danger`) | Card headers (+ Alerts) |
| Card title / desc (rich) | Headings + supporting text | :120/121 | `SectionCard` props | Card headers |
| SettingRow + label/desc | Label + control rows | :124; sb:280 | `SettingRow` | Custom (form row) |
| Inline mono token (`{{vc}}`…) | Template vars | :244 | `font-mono` spans | Code snippets |
| Select (retention / on-conflict) | Dropdowns | :130/168; ui.tsx:624 | Bespoke `Select` | Select |
| NumberField | Concurrent file reads | :148; sb:412 | `NumberField` over `Input` | Inputs (number) |
| Toggle | Many booleans | :160/185/246 | UUI `Toggle` | Toggles |
| NestedBox (perms / tech-tags) | Conditional sub-settings | :195/240; sb:350 | `NestedBox` | Custom (inset) |
| ProviderField (perms octal/uid/gid) | Mono labeled inputs | :197; sb:462 | `ProviderField` (Input) | Inputs |
| Secondary Export/Import buttons | Download/refresh | :260/274 | UUI `Button` secondary | Buttons (secondary) |
| Button row / hidden file input | Action toolbar + import trigger | :259/263 | Raw flex + hidden `<input type=file>` | Button groups + File uploaders |
| Toast (export/import) | Outcomes | :81 | `pushToast` | Notifications |
| Danger-zone header + red rule | "DANGER ZONE" + divider | :283 | Uppercase span + red `h-px` | Section headers + Content dividers |
| DangerRow ×4 | Dot + name + severity badge + desc + action + confirm | :385/295 | Local `DangerRow` | Custom (alert-with-action) |
| Severity dot / badge | Tier dot + scope pill | :394/398 | Inline `color-mix()` | Badges (dot/color) |
| Danger name / caption / prompts | Title + "what is lost" + confirm hints | :397/409/421 | Raw text | Custom (text) |
| Trigger / Confirm / Cancel buttons | secondary-destructive / primary-destructive (loading) / tertiary | :412/429/439 | UUI `Button` (variants) | Buttons (destructive / tertiary) |
| Confirm-word input | Mono DELETE/FACTORY field | :424; input.tsx | UUI `Input mono` | Inputs |
| Inline error / armed sub-row | Error text + confirm bar | :442/418 | Raw `text-conf-low` / flex | Alerts + Custom |
| Plain icons | gear/film/alert/download/refresh | :2 | lib/icons | Featured icons / plain |

### Settings — shared building blocks (settings-blocks.tsx)

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| SettingsLayout / SettingsGrid | Page shell + 2-col grid | :43/86 | Custom flex/grid + `.settings-stage` | Custom (layout) |
| SectionHeader (+ accent icon variant) | Section identity banner | :143 | Bespoke + `.settings-section-icon` | Page headers + Featured icons |
| Section title / purpose / intro text | Typography | :160/161/68 | Raw Tailwind | Custom (typography) |
| StatusPill + status dot | Badge-with-dot (tones, breathe) | :115/127 | Bespoke pill + glow dot | Badges (dot) |
| SettingsFilter + clear × | Search input | :179/196 | Raw `<input>` + ghost button | Inputs (search) + Buttons (utility) |
| SectionCard (+ danger variant) | Card with header + divider | :217/234 | Bespoke `.settings-card` + `FeaturedIcon` | Card headers (+ Alerts) |
| Card title / desc | Typography | :249/250 | Raw Tailwind | Custom (typography) |
| FeaturedIcon | Tinted icon chip | featured-icon.tsx:42 | UUI `FeaturedIcon` | Featured icons |
| SettingRow (inline/stacked) | Label + desc + control | :280 | Bespoke + data-* | Custom (form row) |
| FieldRow | Aligned label + control | :330 | Bespoke `<label>` | Custom (form row) |
| NestedBox | Inset sub-panel | :350 | `SETTINGS_NESTED` | Custom (inset) |
| SliderField + readout | Range + mono value | :368/404 | Native `range` + mono | Sliders |
| NumberField | Bounded numeric + unit | :412 | UUI `Input mono number` | Inputs (number) |
| Input (+ editGate button) | Glass text field | input.tsx:29/62 | UUI `Input` | Inputs |
| ProviderField | text/password/select/toggle field | :462 | Composite (Input/Select/Toggle) | Inputs / Select / Toggles |
| Password eye button | Show/hide | :533 | Raw ghost button | Buttons (utility) |
| Toggle / Select | Switch / dropdown | toggle.tsx:18; ui.tsx:624 | UUI `Toggle` / bespoke `Select` | Toggles / Select |
| ProviderCard | Collapsible provider tile | :645 | Bespoke (composite) | Custom (card + accordion) |
| ProviderLogo + corner dot | Avatar + indicator | :626/704 | `ProviderLogo` | Avatars (+ indicator) |
| Media-type Badge | Neutral pill | badges.tsx:25; :717 | UUI `Badge` | Badges |
| Collapse chevron / CSS-grid collapse | Disclosure mechanics | :730/737 | `IcChevDown` + grid-rows trick | Custom (accordion) |
| Test-connection button | Secondary + loading | :723 | UUI `Button` secondary | Buttons (secondary) |
| BanCountdownBanner / Alert / fallback hint | Callouts | :574/588/744 | UUI `Alert` | Alerts |
| Disabled-prerequisite hint | Warning helper text | :484 | Raw `text-warning-primary` | Custom (form text) |

### Global chrome — Sidebar / Topbar / Toast / Notifications

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Sidebar shell / drawer | Glassy rail + collapse + mobile drawer | ui.tsx:216; css 386 | `.kira-sidebar` | Sidebar navigations / Slideout |
| Brand mark / wordmark / version / update link | Logo chip + text + link | :224/227/232 | `.kira-brandmark` + raw text | Featured icons + Custom + Buttons (link) |
| Collapse toggle / rail-expand | Chevron icon buttons | :246/267 | Raw `.press` buttons | Buttons (utility) |
| Nav section label ("Workspace") | Uppercase group label | :261 | Raw uppercase | Section headers (eyebrow) |
| Primary nav item + morphing active pill | Nav rows + layout-id pill | :281/294; css 416 | `.kira-nav-item` + `motion.span` | Sidebar navigations |
| Nav count badge / unread dot | Review count / rail dot | :313/311 | Raw pill / abs dot | Badges (count/dot) |
| Settings disclosure chevron + nested sub-nav | Rotating chevron + collapsible sub-menu + active marker | :323/333/369 | `AnimatePresence` + layout-id | Sidebar navigations (nested) |
| Sub-nav group labels / items | Uppercase bands + links | :350/360 | Raw uppercase + buttons | Sidebar navigations |
| Status footer card + live dot + label + sign-out | Footer block | :393/398/405 | Bespoke card + `.breathe` dot | Sidebar navigations (footer) |
| Topbar shell | Sticky glass header | :438; css 433 | `.topbar-glass` | Header navigations |
| Hamburger button | Mobile menu | :440 | Raw `.press` button | Buttons (utility) |
| Breadcrumb trail | Crumbs + animated leaf | :448 | Raw flex + `motion.b` | Breadcrumbs |
| Search box + kbd hint + clear | Global search | :466/487/477 | `.topbar-search` + bare input | Inputs (search) / Command menu |
| Keyboard-shortcuts button | Bordered icon button | :491 | Raw `.press` border button | Buttons (utility) |
| Notifications bell button + count badge | Trigger + "99+" | NotificationsBell.tsx:107/122 | Raw `.press` button + `motion.span` | Buttons (utility) + Badges (count) |
| Notifications dropdown panel + header + mark-all-read | Popover + title + link | :138/153/156 | Bespoke `role=dialog` + `Button` link-gray | Notifications / Dropdowns + Card headers + Buttons |
| Notifications offline alert | Error callout | :160 | UUI `Alert` | Alerts |
| Notification row + featured icon + unread dot + timestamp | List item | :171/182/190/188 | Bespoke `<button>` + `FeaturedIcon` | Notifications / Activity feeds |
| Notifications empty state | All caught up | :164 | Bespoke + `FeaturedIcon` | Empty states |
| Scan-now CTA | Primary + spinner | :500 | UUI `Button` primary | Buttons (primary) |
| Toast stack + card + featured icon + title/sub + dismiss | Global toasts | :513/529/539/548 | `.toasts` + bespoke `motion.div` + `FeaturedIcon` | Notifications |
| Plain icons + spinner | Chrome glyphs | :7; :503 | lib/icons + `IcSpin` | Custom / Loading |

### Shared UI primitives (ui.tsx)

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Poster | Cover-art tile (initials fallback) | :18 | `.poster` | Avatars — Custom |
| ConfidenceBadge | Verdict + % pill | :68 | `.badge.badge-{level}` + `.dot` | Badges (dot) |
| StatusPill (lifecycle) | Approved/rejected/pending swatch | :89 | `.status-pill` + `.swatch` | Badges (status) |
| MediaTypeIcon | Type → icon | :104 | Switch over lib/icons | Custom (icon util) |
| Checkbox (`.cb`) | Tri-state checkbox | :565 | `.cb` button | Checkboxes |
| Segmented (`.seg`) | Inline segmented group | :593 | `.seg`/`.seg-btn` (duplicate of base) | Segmented controls |
| Select (portal) | Themed dropdown | :624 | Bespoke generic `Select<T>` | Select |
| FilterPill / FilterGroup | Toggle chip + group | :819/848 | Bespoke | Tags / Filter bars |
| Skeleton | Shimmer placeholder | :859; css 1765 | `.kira-skeleton` | Loading (skeleton) |
| EmptyState | Icon + title + sub + action | :902 | Bespoke (UUI tokens) | Empty states |
| Modal + close-x | Dialog shell + dismiss | :925/979 | `.modal*` / `.close-x` | Modals + Buttons (utility) |
| No-options inline message | Select empty | :785 | Inline muted text | Empty states (minimal) |
| Text styles | Modal/badge/poster/Select typography | throughout | Mixed CSS + Tailwind | Custom (typography tokens) |

### Cover Popup (media-detail overlay)

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Overlay + backdrop | Scrim (click-out close) | CoverPopup.tsx:1202 | `.cx-overlay` portal | Modals (overlay) |
| Modal shell | Dialog container (color bleed) | :1240 | `.cx-shell` | Modals |
| Flying cover | Shared-element poster flight | :1210 | Imperative `getBoundingClientRect` div | Custom |
| Low-confidence warning banner | Alert + Sync CTA | :1281 | Inline `role=alert` + primary button | Alerts (with action) |
| Hero poster slot | Cover art (initials fallback) | Hero.tsx:31 | `.cx-hero-cover-slot` | Avatars — Custom |
| Media-type pill | Type chip | Hero.tsx:89; css 3718 | `.cc-mediatype` | Badges |
| Hero meta row / title / alt-title | Year/runtime/count + H2 + romaji | Hero.tsx:88/98/109 | `.cx-hero-*` + `.dot-sep` | Section headers / Custom |
| Overview + show-more | Clamped synopsis + toggle | Hero.tsx:117 | `<p>` clamp + text button | Buttons (link) + body |
| Hero detail list | Studio/Network/Director/… | Hero.tsx:135 | `.cx-hero-detail*` | Custom (label/value) |
| Confidence/status chips | Stats line (% + counts + dots) | Hero.tsx:178; css 4646 | `.cx-summary-chip` + swatch | Badges (dot) |
| Provider external links | TMDB/TVDB/… link chips | Hero.tsx:192; format.tsx:102 | `.cx-prov-link` + IcExternal | Buttons (link) / Badges |
| Footer summary line | File/episode counts | :1390 | Inline-colored `<b>` chain | Metrics (inline) — Custom |
| Footer Close (icon) | Secondary icon button | :1430 | UUI `Button` secondary | Buttons (utility) |
| Footer secondary actions | Re-identify/Search/Get subs/Sync | :1462/1503/1602 | UUI `Button` secondary | Buttons (secondary) |
| Footer "Resolve N duplicates" | Amber secondary | :1644 | UUI `Button` + className overrides | Buttons (custom color) |
| Footer Reject/Ignore/Skip | Destructive secondary | :1700 | UUI `Button` secondary-destructive | Buttons (destructive) |
| Footer primary CTA | Approve all / Search / Restore | :1759 | UUI `Button` primary | Buttons (primary) |
| Footer layout / spacer | Split left/right | :1390 | Flex + spacer | Custom (footer) |
| List header + show-missing toggle | Section header + toggle | SeriesBody.tsx:123 | `.cx-list-head` + `.cx-missing-toggle` | Section headers + Buttons (link) |
| Season / unmatched dividers | Sticky in-list labels | SeriesBody.tsx:160 | `.cx-list-section` | Content dividers / Section headers |
| Paired episode/file row | Core data row | rows.tsx:288 | `.cx-pair` memoized | Tables (row) — Custom |
| Episode/track number badge | Square thumb (SxxExx/FILM) | rows.tsx:291; css 5290 | `.cx-pair-thumb` | Badges / Avatars (square) |
| Blank / orphan / upcoming / just-imported rows | Row state variants | rows.tsx:198/235/422/450 | `.cx-pair` variants | Empty states (inline) / Custom |
| Download-progress row | Animated fill band | rows.tsx:486 | `role=progressbar` rAF fill | Progress (bar) |
| Skeleton row | Shimmer placeholder | rows.tsx:31 | `.cx-pair-skeleton` | Loading (skeleton) |
| Tech/quality chips + missing-sub chip + "+N" dupe pill | Info tags | rows.tsx:321/49/333 | `.cx-row-tag` + modifiers | Tags / Badges |
| Confidence pill / status pill (per-row) | % + Renamed/Approved/Rejected | rows.tsx:372/365 | `.cx-row-conf`/`.cx-row-status` | Badges |
| Approve/reject segmented toggle | Joined check/x | rows.tsx:379; css 5482 | `.seg-pair`/`.cx-row-act` | Segmented controls / Buttons |
| Inline Search/Find CTA | Orphan/wrong-row link | rows.tsx:250/351 | `.cx-blank-btn` | Buttons (link) |
| Wrong-match warning line | Inline amber alert | rows.tsx:346 | `.cx-pair-wrong`/`.cx-row-warn` | Alerts (inline) |
| Marquee / mono path text | Auto-scroll filename + paths | MarqueeText.tsx:16; rows .mono | `.marquee-*` / `.mono` | Custom / Code snippets |
| Movie body card / cast chips / section labels | Movie composite | MovieBody.tsx:22/75/24 | `.cx-movie*` | Custom + Tags + Section headers |
| Dupe-resolver / delete / bulk-delete / force-import sub-modals | Confirm overlays | dupeModals.tsx:24/321/465; ForceImportModal.tsx:32 | Fully inline-styled (NOT shared Modal) | Modals (confirmation) |
| Dupe file card / generic Chip | Composite card + chip | dupeModals.tsx:185; format.tsx:110 | Inline-styled | Custom + Badges/Tags |
| Import-mode radio group | Copy / Move | ForceImportModal.tsx:178 | Native radios | Radio groups |
| Acknowledge checkbox | Irreversible gate | dupeModals.tsx:407 | Native checkbox | Checkboxes |
| Modal featured icon | Warning/count chip | dupeModals.tsx:83; ForceImportModal.tsx:88 | Inline-styled box | Featured icons |
| Tooltips (native title) | Hover hints | throughout | `title=` | Tooltips |
| Plain icons | check/x/search/etc. | lib/icons | SVGs | Custom |

### Modals (rename / shortcuts / file-details / manual-search — modals.tsx)

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Modal shell + header + footer | Overlay/dialog/header/foot | ui.tsx:925; :244/249 | Shared `Modal` (`size`) | Modals |
| Primary / ghost / danger / neutral / busy buttons | Footer + inline actions | :273/254/831/839/264 | `.btn.btn-*` (legacy classes) | Buttons (all variants) |
| Provider tabs | TMDB/TVDB/AniDB/MB (dot + pill) | :280 | `.provider-tab` + dot + pill | Tabs (with status) |
| Type-filter segmented (inline) | Movies/TV/Both | :348 | Hand-rolled `.seg` (dupe) | Segmented controls |
| Segmented (`<Segmented>`) | File-op / Naming profile | :617/632; ui.tsx:593 | Shared `<Segmented>` | Segmented controls |
| Big search input + text input | Search box + field | :332/338 | `.search-input-big` + raw `<input>` | Inputs (search) |
| Search result card | Selectable grid tile | :407 | `.search-result` | Custom (selectable card) |
| AniDB poster shimmer | Loading skeleton | :434 | `.anidb-poster-loading` `.shimmer-bar` | Loading (skeleton) |
| Poster / ConfidenceBadge / StatusPill / media-type badge | Shared primitives | ui.tsx:18/68/89; :848/856 | `Poster`/`ConfidenceBadge`/`StatusPill`/`Badge` | Avatars / Badges |
| Release-group / AcoustID / "Current pick" / soon-setup pills + provider dot | Chips | :862/865/924/297/295 | `.rg-chip`/`.acoustid-match`/`.badge-high`/`.pill*`/`.provider-dot` | Tags / Badges |
| Alerts/callouts (onboarding-state, default-tab banner, skipped-files) | Inline alerts | :309/357/564 | `.onboarding-state`/inline boxes | Alerts |
| Inline CTA links + ProviderLink | Accent links (Open .org, MBID) | :267/698 | Raw `<a>` accent + `ProviderLink` | Buttons (link) |
| Safety footer note | Shield reassurance | :547 | `.preview-counter` | Custom / helper |
| Before/after preview pair + mono path | From→To diff | :585/15 | `.preview-pair`/`.seg-new` | Custom (diff) / Code |
| Option group / field label (opt-label) / meta rows | Label + control + key/value | :614/616/689 | `.opt-group`/`.opt-label`/`Meta` | Custom (form/desc list) |
| Overview paragraph | Synopsis | :872 | Inline `<p>` | Custom (body) |
| Candidates list + candidate row + confidence bar | Alt matches | :903/935 | `.candidates`/`.confidence-bar` | Tables (rows) + Progress (bar) |
| Empty states (no candidates / no results / +N more) | Centered/inline | :951/480 | `.card` / inline | Empty states |
| Keyboard-shortcut table + kbd caps | 2-col grid + keys | :647/678 | CSS grid + `.kbd` | Custom (table) + Tags |
| Spinner / plain icons / star rating | Loading + glyphs + popularity | :334/5/471 | `IcSpin` / lib/icons / inline | Loading / Custom |

### Folder Picker Modal

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Modal shell + title + subtitle | Dialog (size=lg) | FolderPickerModal.tsx:49; ui.tsx Modal | Shared `Modal` | Modals |
| Primary "Use this folder" (gradient) | Confirm CTA | :59 | Inline `linear-gradient` button | Buttons (primary, brand) |
| Secondary "Cancel" | Glass button | :58 | Inline glass button | Buttons (secondary) |
| Control buttons (Up / Go) | Compact glass (CTL_BTN) | :46/73/82 | `CTL_BTN` string | Buttons (tertiary/utility) |
| Path input | Mono editable field | :74 | Raw `<input>` (not base Input) | Inputs |
| Path bar group | Up + input + Go toolbar | :71 | Flex container | Button groups (with input) |
| Inline error alert | Listing failure | :87 | Inline red box (not `Alert`) | Alerts (error) |
| Folder list container | Scroll region | :93 | Raw scroll div | Tables (list) |
| Folder row | Icon + mono name + count/locked | :104 | Raw `<button>` row | Tables (row) / Tree views |
| Folder icon / name / count / "locked" | Row parts | :112/113/114/117 | lib/icons + mono + caption + colored text | Plain icons / Custom / Badges |
| Empty/loading text | No subfolders / Loading drives | :95 | Inline muted text | Empty states / Loading |
| Spinner / arrow / check icons | Go/confirm glyphs | :83/65 | `IcSpin`/`IcArrowRight`/`IcCheck` | Loading / Plain icons |
| Footer path display + action bar | Mono current path + buttons | :56/57 | Mono span + flex | Custom (mono) + Button groups |
| Helper tip text | Click/double-click hint | :124 | Inline caption | Custom (helper) |

### Subtitle Browse Modal

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Modal shell + scrim + header + body | Portal dialog | SubtitleBrowseModal.tsx:198/200/202/215 | Bespoke portal (`anim-pop`) | Modals |
| Header leading icon / back / close | Icon tile + back ‹ + × | :206/204/212 | `FeaturedIcon`-style / raw buttons | Featured icons / Buttons (utility) |
| Title + mono filename subtitle | Dynamic title + filename | :209/210 | Text styles | Modals (title) / Custom |
| Candidate / pack-entry row cards | List items (ring + meta + action) | :313/232 | `.cx`-style rows | Tables (row) — Custom |
| Score ring + numeric KPI | Radial gauge + score | :236/241 | Hand-rolled SVG | Progress (circular) + Metrics |
| Provider name (row title) | Title text | :329 | Raw span | Custom (title) |
| Language / SDH / sync / season-pack / best-guess badges | Status pills | :330/332/331/333/246 | Raw `<span>` recipes / `SYNC` map | Badges (color) |
| Release / pack-entry mono text | Filenames | :337/245 | `font-mono` | Custom (mono) |
| Reasons / signal caption | Ranking line | :248/338 | Caption text | Custom (caption) |
| Download/Extract / Use-this buttons + spinner | Secondary actions | :347/250 | Raw outline button + `IcSpin` | Buttons (secondary) |
| "saved" success indicator | Inline check + text | :344 | Inline `text-conf-high` | Badges (success) |
| Loading / error / empty states | Searching / error / no candidates / pack-empty | :264/262/266/227 | Inline spinner/text | Loading / Alerts / Empty states |
| Amber pack callouts (all-packs, ambiguity) | Warning banners | :299/219 | Inline rgba box | Alerts (warning) |
| Season-fill banner + Fill/Dismiss buttons | Blue opt-in CTA | :273/280/288 | Inline rgba box + buttons | Alerts (with actions) + Buttons |
| Header divider / list stack | Border + flex column | :202/218 | `border-line` / flex | Content dividers / Custom |

### Onboarding wizard

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Full-screen overlay + backdrop + scroll lock | Wizard root | Onboarding.tsx:680; :609 | `.onboarding-root`/`.backdrop` + `useScrollLock` | Modals (full-screen) |
| Two-pane shell (rail + content) | Layout | :682 | `.onb-shell`/`.onb-rail`/`.onb-pane` | Custom (wizard layout) |
| Brand lockup | Logo + name + tagline | :685 | `.brand`/`.mark` | Featured icons + Custom |
| Vertical step nav + step dot | Progress steps (numbered/check) | :692/703; RAIL_STEPS :585 | Bespoke buttons + `.dot` | Progress steps (vertical) |
| Rail footer caption | Self-hosted · vX | :712 | `.foot` | Custom (caption) |
| Top progress bar | Determinate fill | :717; :677 | `.onb-progress` (NOT base bar) | Progress (bar) |
| Animated step body | Per-step transition | :720 | `.onb-body` + `--i` stagger | Custom (anim) |
| Welcome hero + bullet list + gradient text | Step 0 hero | :52/58/56 | `.onb-hero`/`.bullets`/`.grad` | Page headers — Custom |
| Step eyebrow (Step N of 6 + tag) | Counter + Required/Optional | :88; reused | `.onb-eyebrow`/`.step-n` | Section headers + Badge |
| Step title / subtitle / footnote | Headings + copy + hint | :92/93/128 | `.onb-title`/`.onb-sub`/`.onb-hint` | Page headers + Custom |
| Content-type checkbox cards | Movies/TV/Anime/Music (multi) | :97 | `<button role=checkbox>` `.ct-card` | Checkboxes (cards) |
| File-op / rename-mode / naming radio cards | Single-select cards (+ tree preview) | :390/409/478 | `<button role=radio>` `.ct-card`/`.naming-card` | Radio buttons (cards) |
| Card tags (Coming soon / Recommended) + check indicator | Pills + tick | :116/399/120 | `.ct-soon`/`.ct-check` | Badges + Checkboxes/Radio |
| Example-titles caption | Sample titles | :122 | `.ct-ex` | Custom (caption) |
| API-key field (label + input + status icon + validation) | KeyField (TMDB/TVDB) | :137/156/163 | Composite over `.input.mono` | Inputs (with validation) |
| Required/Optional tag + "Get a key →" link | Field header | :152/153 | `.req` pill + `<a>` | Badges + Buttons (link) |
| Validation messages (success/error/checking) | Inline callouts | :169/179/182 | `.onb-state*` | Alerts |
| ffmpeg status row + folder card + Browse | Status row + selected folder | :295/321/327 | `FfmpegStatusRow` / `.onb-folder-card` + `.btn-sm` | Custom + Buttons |
| Watch-folder checkbox row | Checkbox + sub | :332 | Native `<input>` + `.onb-checkrow` | Checkboxes (with text) |
| FolderPickerModal | Browse dialog | :345 | `FolderPickerModal` | Modals |
| Directory-tree preview | Sample layout (mono) | :436 | `.naming-card-tree` `.dir`/`.accent-line` | Code snippets / Tree views |
| Summary list + leading icon + chips + Edit link | Review rows | :522/524/529/535 | `.onb-summary`/`.row`/`.chip`/`.edit` | Custom (review list) + Featured icons + Tags + Buttons (link) |
| Footer nav (Continue/Get started/Start scan/Back/Skip/hint) | Action bar | :743/761/747/753/755 | `.onb-foot` + `.btn-*`/`.onb-skip` | Buttons (all) + Custom |
| Plain icons | lib/icons set | :4 | SVGs | Custom |

### Login / Setup gate

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Auth card (glass panel) | Centered frosted card | LoginGate.tsx:142; css 2302 | `.login-card` | Modals (panel) — Custom |
| Full-screen root + scroll lock | Page wrapper | :139; :7 | `.onboarding-root`/`.login-gate` + `useScrollLock` | Custom (overlay) |
| Aurora backdrop | Ambient gradient + dot grid | :140; css 158 | `.backdrop` | Custom (background) |
| Poster-rail marquee | Tilted sliding poster rows + scrim | :38/141; css 2369 | `PosterRails` + `.login-bg*` | Carousels / Avatars — Custom |
| Brand mark | Logo glyph + glow | :143; css 2316 | `.login-card .mark` + `IcLogoMark` | Featured icons |
| Title (gradient word) | H1 sign-in/create | :144; css 2323 | `.login-card h1` + `.grad` | Page headers — Custom |
| Subtitle | Context line | :145; css 2336 | `.sub` | Page headers (supporting) |
| Field label (uppercase) | USERNAME/PASSWORD | :152; css 2343 | `.field span` | Inputs (label) |
| Text / password inputs | Username + password(s) | :153/164 | Raw `<input className="input">` | Inputs |
| Inline error callout | Mismatch / auth error | :185; css 2070 | `.onb-state.error` | Alerts (error) |
| Primary submit (icon + busy) | Sign in / Create | :192; css 2348 | `.btn.btn-primary.submit` | Buttons (primary, loading) |
| Security footnote | Shield + fine print | :197; css 2354 | `.login-card .note` | Custom / helper |
| Spinner | In-button loading | :193 | `IcSpin animate-spin` | Loading (spinner) |

### Misc widgets — NotificationsBell / FfmpegStatus / ActivityIndicator / ScanProgress / LibraryGrid

| Element | What it is | Location | Current impl | → UUI |
|---|---|---|---|---|
| Notifications bell + count badge | Trigger + "99+" | NotificationsBell.tsx:107/122 | Raw `.press` button + `motion.span` | Buttons (utility) + Badges (count) |
| Notifications panel + header + mark-all + offline alert + rows + featured icons + unread dot + timestamp + empty | Dropdown system | :138/153/156/160/171/182/190/164 | Bespoke popover + UUI `Button`/`Alert`/`FeaturedIcon` | Notifications / Dropdowns / Empty states |
| ffmpeg status row | Label + state/action | FfmpegStatus.tsx:43 | Raw flex row | Custom (status row) |
| "Ready" / "Installing…" / warning inline statuses | Icon + text | :53/57/65 | Inline colored icon+text | Badges (status) — Custom |
| "Install for me" button | Secondary + download + loading | :61 | UUI `Button` secondary | Buttons (secondary) |
| Activity pill + icon chip + edge line + N/M counter + dismiss | Live status toast | ActivityIndicator.tsx:136/159/158/166/174 | Bespoke glass pill (`anim-pop`) | Notifications (snackbar) + Featured icons + Loading |
| Scan progress card + header + labeled bars + completion tick + sheen | Multi-phase status | ScanProgress.tsx:35/41/55/59/96 | Bespoke glass card + UUI `ProgressBar` | Notifications + Progress (bar) |
| Cover card (poster/album tile) | Core library unit | LibraryGrid.tsx:246 | `.cc`/`.cc.cinema` | Custom (media card) |
| Poster image / gradient fallback / initials / no-match placeholder + corner alert | Cover art variants | :370/343 | `.cc-cover`/`.cc-init`/`.cc-cover-nm` | Avatars — Custom |
| Bulk-select checkbox (overlay) | Top-left toggle | :398 | `.cc-select` | Checkboxes |
| Confidence pill + ring | % badge + card outline | :408/265; ccStatChipColor :180 | `.cc-conf-pill` + `ring-{tier}` | Badges (dot) + Custom (outline) |
| Sonarr live pill + missing-subs badge | Cover overlays + tooltips | :1140/436 | `.cc-sonarr-pill`/`.cc-sub-missing` | Badges + Tooltips |
| Card title / sub-line + Search link | Meta block | :452/467/488 | `.cc-title`/`.cc-sub`/`.cc-nm-search-link` | Custom (text) + Buttons (link) |
| Quick-action buttons (Approve/Reject/Search) | Hover-revealed cluster | :508 | `.cc-actions`/`.cc-act` | Button groups / Utility buttons |
| Dot separator | Inline meta divider | :487; throughout | `.dot-sep` | Content dividers (inline) |
| Section header + count chip + icon chip (+ needs-matching variant) | Per media-type banner | :744/749/746/772 | `.lib-section-*` | Section headers + Badges + Featured icons |
| Franchise shelf + heading + count badge | Grouped sub-grid | :1066/1068/1074 | `.lib-franchise-*` | Custom (collection shelf) + Section headers + Badges |
| Library grid container | Responsive cover grid | :957 | `.lib-grid` | Custom (layout) |
| Skeleton loading grid | First-paint placeholders | :628 | `.kira-skeleton` blocks | Loading (skeleton) |
| Empty state (filtered) + onboarding hero (+ step badges/links) | Zero-results + first-run | :670/695/709/710 | `.lib-empty`/`.lib-empty-hero`/`.step-num`/`.step-link` | Empty states (+ Progress steps) + Buttons (link) |
| Hero featured icon | Empty-state illustration | :673/697 | `.hero` | Featured icons (large) |
| Legacy primary button (.btn.btn-primary) | "Clear all filters" | :682 | `.btn.btn-primary` (legacy) | Buttons (primary) |

---

## Text styles

Distinct typographic roles found across the app → the UUI typography/text token to standardize on. Today these are a mix of `index.css` classes (`.page-title`, `.modal-title`, `.badge-label`…) and inline Tailwind `text-[Npx]` sizes — i.e. there is no shared scale, which is the consolidation opportunity.

| Text role | Where it appears | Current impl | → UUI token |
|---|---|---|---|
| Page title (H1) | Dashboard/Review/History/Settings, login | `.page-title` (26px/700) / `.login-card h1` | Display / Heading — Page header title |
| Hero / marketing title (gradient) | Dashboard hero, Onboarding welcome, Login | `.dash-hero-title`/`.onb-hero h1`/`.grad` (40–46px, bg-clip) | Display (xl) — Custom gradient |
| Section title (banner H2) | Settings SectionHeader | `text-[17px] font-semibold` | Heading (md) — Section header |
| Sub-group heading / eyebrow | Settings/CoverPopup/LibraryGrid/Sidebar/Onboarding | `text-[10–13px] font-semibold uppercase tracking` | Eyebrow / overline (xs) |
| Card title | SectionCard, Dashboard card, modal | `text-[14–15px] font-semibold` / `.modal-title` (16/600) | Heading (sm) — Card header title |
| Card / section supporting text | SectionCard desc, page-sub, modal-sub | `text-[12.5–13px] text-secondary` / `.page-sub` / `.modal-sub` | Body (sm, muted) — supporting text |
| Field label | Settings rows, form fields, Onboarding | `text-[13–13.5px] font-medium` / `.opt-label` / `.field span` | Label (Inputs) |
| Field hint / helper / caption | Settings captions, hints, FolderPicker tip | `text-[11–12px] text-tertiary/ink-soft` | Hint text (Inputs) / caption |
| Body / paragraph | Synopsis (CoverPopup/FileDetails), copy | inline `<p>` 13px / `.onb-sub` | Body (md) |
| Monospace path / code | Paths everywhere, release names, `.seg-new` diff, template tokens | `font-mono` / `.mono` / `.preview-path` | Code snippets (mono) |
| KPI / numeric readout | Metric count-up, slider %, score dial, storage size, counters | `font-mono tabular-nums` / `CountUp` | Display number / Metrics |
| Badge / pill text | Confidence verdict, status, op chips, lang codes | `.badge-label` (11/600) / `.badge-pct` / pill recipes | Badge text (xs) |
| Relative timestamp | Activity/History/Notifications | `text-[11px] text-quaternary/faint` | Caption (xs, muted) |
| Nav item / sidebar label | Sidebar nav + group labels | `.kira-nav-item` text / uppercase | Nav label |
| Keyboard keycap | Topbar `/`, Shortcuts modal, Naming keycaps | `.kbd` | Keycap (custom) |

---

## Custom — no Untitled UI equivalent

These have no UUI match and must stay bespoke (reason in one line):

| App element | Surfaces | Why it stays custom |
|---|---|---|
| Poster / cover-art tile (`Poster`, `.cc-cover`, hero slot) | Review, History, Dashboard, LibraryGrid, CoverPopup, modals | Domain cover-art with gradient-initials fallback; UUI Avatars don't cover poster aspect/initials/lazy-AniDB. |
| Poster fan (Dashboard) & login poster-rail marquee | Dashboard hero, Login | Decorative 3D/blurred cover-art collages; no catalog component. |
| Flying / shared-element cover | CoverPopup | Imperative rect-to-rect flight animation on open/close. |
| Confidence donut ring / score dial | Dashboard, Subtitle History/Browse | Hand-rolled multi-segment SVG gauge with spring arcs; bucket palette is Kira-specific. |
| Confidence ring outline on cards (`ring-{tier}`) | LibraryGrid | Semantic card outline tied to Kira confidence tiers. |
| Naming template chip editor (`TemplateChipEditor`) | Naming | Contenteditable token-pill field with caret/serialize logic; no UUI rich/token editor. |
| Live template preview + `.seg-new` path diff | Naming, Rename modal | Backend-rendered path morph/diff list; bespoke. |
| Brand mark / wordmark / gradient nav pill | Sidebar, Onboarding, Login | Brand assets + Framer layout-id morph; not a catalog component. |
| Morphing active nav indicators (`layoutId` pills/markers) | Sidebar | Shared-layout spring animation. |
| Media-type icon set + `MediaTypeIcon` mapping | App-wide | Kira's own icon library (lib/icons), not `@untitledui/icons`. |
| Status-dot breathing/glow effect (`.breathe`/`.settings-dot-live`) | Sidebar, Settings, providers | Decorative pulse/halo on dots beyond a plain Badge dot. |
| Hover glow accent (cards/metrics) | Dashboard | Radial-gradient decorative effect. |
| `.cx-pair` paired episode/file row (+ download/upcoming/orphan/just-imported variants) | CoverPopup | Composite media-pairing row with live Sonarr progress band; no UUI row matches. |
| Sonarr live activity pill / queue mapping | LibraryGrid, CoverPopup | App-specific status→color/label system. |
| Marquee auto-scroll filename text | CoverPopup rows | ResizeObserver-driven ping-pong scroll; bespoke. |
| Activity pill / ScanProgress glass cards + sheen sweep | Global | Kira-tuned live status surfaces with shine animation. |
| Floating Settings save bar | Settings | Docked unsaved-changes action bar; no UUI floating-action-bar primitive. |
| Bulk selection action bars (Review/History) | Review, History | Contextual multi-select toolbars; UUI has no standalone selection bar. |
| NestedBox / inset "well" panel, SettingsLayout/stage, page grids | Settings, all pages | Layout/scaffolding + entrance-stagger motion, not visual components. |
| DangerRow arm-to-confirm reset rows | Advanced | Composite alert-with-inline-confirm; bespoke severity flow. |
| `<details>` "What gets deleted" accordion | Cleanup | Native disclosure; UUI catalog has no Accordion. |
| M·S·E target-dot keycaps / NFO legend | Naming | Bespoke mono-keycap target indicators. |
| Keyboard keycaps (`.kbd`) + shortcuts table | Topbar, Shortcuts modal, Naming | Key-cap chips and a 2-col shortcut grid; no UUI kbd/shortcut component. |
| Directory-tree previews | Onboarding, Naming | Monospace sample folder trees; closest is Code snippets but bespoke. |

---

**Element count:** ~360 catalogued UI elements across 23 surfaces (Dashboard, Review, History, Subtitle History, 9 Settings sub-surfaces + shell + shared blocks, global chrome, shared primitives, CoverPopup, modals.tsx, FolderPicker, Subtitle Browse, Onboarding, Login, and the misc-widgets group).
