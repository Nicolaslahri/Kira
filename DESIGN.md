# Kira Design System

> Glassmorphic dark theme with emerald accent and pink/orange brand gradient.
> Every screen, component, and new page must follow these tokens and patterns.

---

## Colors

### Surfaces
| Token | Value | Usage |
|-------|-------|-------|
| `--bg` | `#07060c` | Page background |
| `--ink` | `#ffffff` | Primary text |
| `--ink-2` | `#bdc1d0` | Secondary text |
| `--ink-3` | `#71778e` | Muted text, labels |
| `--ink-4` | `#4a4f63` | Disabled text, divider labels |
| `--line` | `rgba(255,255,255,0.08)` | Borders, dividers |
| `--line-strong` | `rgba(255,255,255,0.16)` | Stronger borders (focus, hover) |
| `--glass` | `rgba(255,255,255,0.04)` | Glass panel background |
| `--glass-2` | `rgba(255,255,255,0.07)` | Glass hover state |
| `--glass-3` | `rgba(255,255,255,0.10)` | Glass active / selected state |
| `--hover` | `rgba(255,255,255,0.06)` | Row/item hover |
| `--active` | `rgba(255,255,255,0.10)` | Row/item active |

### Brand (Sphix gradient)
Used for logo mark and active nav item only. Not for general UI.

| Token | Value |
|-------|-------|
| `--brand-a` | `#ff974b` (orange) |
| `--brand-b` | `#e54bba` (pink) |
| `--brand-grad` | `linear-gradient(135deg, #ff974b 0%, #e54bba 100%)` |

### Accent (Emerald)
Primary action color. Used for CTAs, checkboxes, approved states, high confidence.

| Token | Value |
|-------|-------|
| `--accent` | `#28d9a0` |
| `--accent-deep` | `#0e7c5a` |
| `--accent-soft` | `rgba(40,217,160,0.14)` |
| `--accent-line` | `rgba(40,217,160,0.32)` |

### Confidence Levels
| Level | Color | Background |
|-------|-------|------------|
| High (>=85%) | `--conf-high: #28d9a0` | `rgba(40,217,160,0.12)` |
| Medium (50-84%) | `--conf-mid: #ffc94a` | `rgba(255,201,74,0.12)` |
| Low (<50%) | `--conf-low: #ff5b6e` | `rgba(255,91,110,0.12)` |

### Misc
| Token | Value | Usage |
|-------|-------|-------|
| `--info` | `#49b8fe` | Info badges, TV type indicator |
| `--info-bg` | `rgba(73,184,254,0.12)` | Info badge background |

---

## Typography

| Purpose | Font | Size | Weight | Tracking |
|---------|------|------|--------|----------|
| UI text | Inter | 14px | 400 | -0.005em |
| Labels | Inter | 12px | 500 | normal |
| Section labels | Inter | 10-10.5px | 600 | 0.06-0.08em, uppercase |
| Page title | Inter | 26px | 700 | -0.02em |
| Card title | Inter | 14px | 600 | -0.01em |
| Stat value | Inter | 32px | 700 | -0.03em, tabular-nums |
| Monospace (paths, code) | JetBrains Mono | 12px | 400 | normal |
| Brand name | Inter | 18px | 700 | -0.02em |

**Font stacks:**
- `--font-ui`: `'Inter', -apple-system, 'SF Pro Text', system-ui, sans-serif`
- `--font-mono`: `'JetBrains Mono', ui-monospace, 'SF Mono', Menlo, monospace`

**Line height:** 1.45 globally.
**Font smoothing:** antialiased on both webkit and moz.

---

## Spacing & Radius

| Token | Value | Usage |
|-------|-------|-------|
| `--r-sm` | 6px | Posters, small elements |
| `--r-md` | 10px | Buttons, inputs, cards |
| `--r-lg` | 14px | Large cards, panels |
| `--r-xl` | 20px | Modals |

**Page padding:** 28px horizontal, 28px top, 80px bottom.
**Card padding:** 20px (`.card-pad`).
**Sidebar width:** 240px fixed.
**Topbar height:** 60px sticky.

---

## Backdrop

Fixed radial gradient glow behind all content. Gives the glassmorphic feel.

```css
background:
  radial-gradient(900px 600px at 15% -10%, rgba(229,75,186,0.18), transparent 60%),
  radial-gradient(900px 700px at 100% 100%, rgba(255,151,75,0.14), transparent 55%),
  radial-gradient(1200px 800px at 50% 120%, rgba(114,0,228,0.10), transparent 60%);
```

Plus a subtle dot grid overlay (32px spacing, 0.025 opacity white dots).

---

## Layout Shell

```
+--sidebar(240px)--+--------main(flex 1)--------+
|  Brand logo      |  Topbar (sticky, 60px)     |
|  Nav items       |  Page content               |
|  Footer status   |                             |
+------------------+-----------------------------+
```

- Sidebar: sticky, full height, glass background with 40px blur
- Main: flex column, min-width 0
- Topbar: sticky top 0, z-index 30, blurred background

---

## Components

### Buttons
| Variant | Background | Text | Border |
|---------|-----------|------|--------|
| Default (`.btn`) | `--glass-2` | `--ink` | `--line` |
| Primary (`.btn-primary`) | `--accent` solid | `#061814` (dark) | transparent |
| Brand (`.btn-brand`) | `--brand-grad` | white | transparent |
| Danger (`.btn-danger`) | `rgba(255,91,110,0.14)` | `#ff8a98` | `rgba(255,91,110,0.3)` |
| Ghost (`.btn-ghost`) | transparent | `--ink-2` | transparent |

All buttons: 9px 14px padding, 10px radius, 13px font, 500 weight.
Small variant: 6px 10px, 12px font.
Hover: subtle brightness increase. Active: 1px translateY.
Primary has emerald glow shadow: `0 8px 18px -8px rgba(40,217,160,0.6)`.

### Badges (Confidence)
Pill shape (999px radius), 11px font, 600 weight, 3px 8px padding.
Contains a 6px dot + text like "High · 97%".

| Class | Background | Text color |
|-------|-----------|------------|
| `.badge-high` | `--conf-high-bg` | `--conf-high` |
| `.badge-mid` | `--conf-mid-bg` | `--conf-mid` |
| `.badge-low` | `--conf-low-bg` | `--conf-low` |
| `.badge-neutral` | `rgba(255,255,255,0.06)` | `--ink-2` |

### Cards / Glass Panels
Background: `--glass` with 1px `--line` border, `--r-lg` radius, 20px blur.
Card head: flex row, 16px 20px padding, bottom border.
Card body: 20px padding.

### Inputs
Background: `--glass`, border: `--line`, 9px radius, 9px 12px padding.
Focus: border becomes `--accent-line`, background becomes `--glass-2`.
Monospace variant (`.input.mono`): JetBrains Mono, 12px.

### Posters
Gradient placeholders showing initials + year. Rounded 6px corners.
Sizes: xs (32x48), sm (44x66), md (64x96), lg (120x180).
Each poster gets a consistent gradient tint derived from the title hash.
8 tint pairs from the Sphix palette.

### Checkboxes
18x18px, 1.5px border, 5px radius.
Unchecked: `--line-strong` border, transparent bg.
Checked: `--accent` bg and border, dark checkmark icon.

### Segmented Controls
Container: `--glass` bg, `--line` border, 9px radius, 3px padding.
Buttons: 7px 8px padding, 6px radius, 12px font.
Active button: `--glass-3` bg, `--ink` color.

### Filter Pills
Inside a filter-group container. 12px font, 5px 11px padding, 7px radius.
Active: `--glass-3` bg, `--ink` color.
Includes optional count number in muted text.

### Toasts
Fixed bottom-right, 280-420px width.
Glass background with blur, 11px radius, 12px 16px padding.
Success: emerald accent border. Error: red accent border.
Slide in from right with 0.2s animation.
Auto-dismiss after 4 seconds.

### Modals
Overlay: fixed inset, black 55% opacity, 6px blur.
Modal: glass bg (rgba(20,16,32,0.92)), 40px blur, `--r-xl` radius.
Max-width: 720px default, 920px for `.size-lg`.
Fade + scale animation (0.18s cubic-bezier).
Header: 18px 22px padding, bottom border.
Body: 22px padding, scrollable.
Footer: 16px 22px padding, top border, dark bg overlay.

---

## Page Patterns

### Dashboard
- Page header with title + action buttons
- Scan banner (when scanning): emerald gradient border, pulse animation, progress bar
- 4-column stat grid: big number + label + delta indicator
- 2-column layout: activity feed (left, wider) + health/quick actions (right)

### Review (Core Screen)
- Filter toolbar: status pills | confidence pills | media type pills | sort dropdown
- Bulk action bar (when items selected): emerald bg, count + actions
- Table with 7-column grid: checkbox | poster | filename+path | match info | confidence | status | actions
- Row header: sticky below topbar, uppercase labels, dark bg
- Rows: hover highlight, selected state (emerald tint), focused state (left brand border)
- Click row opens FileDetails modal (not inline expand)

### History
- Filter toolbar: time range + operation type
- Rows: checkbox | poster | title+op badge+paths (old strikethrough, new green) | time | undo button

### Settings
- 2-column layout: left nav (240px sticky) + right content card
- Sections: Connections, Paths, Naming, Confidence, Advanced
- Field rows: 280px label column + control column

---

## Animations

| Animation | Duration | Easing | Usage |
|-----------|----------|--------|-------|
| fadeIn | 0.12s | ease | Modal overlay |
| modalIn | 0.18s | cubic-bezier(0.2,0.9,0.3,1.1) | Modal panel |
| slideDown | 0.15s | ease | Bulk bar, expanded panels |
| toastIn | 0.2s | ease | Toast notifications |
| ping | 1.6s | ease-out infinite | Scan pulse dot |
| spin | 1s | linear infinite | Loading spinner |

General transitions: 0.1-0.12s ease for hover/active states. Never exceed 0.2s.

---

## Keyboard Shortcuts

| Key | Action | Context |
|-----|--------|---------|
| `j` / `k` | Navigate rows up/down | Review page |
| `a` | Approve focused file | Review page |
| `r` | Reject focused file | Review page |
| `m` | Open manual search | Review page |
| `Enter` | Open file details modal | Review page |
| `/` | Focus search bar | Global |
| `?` | Open keyboard shortcuts | Global |
| `g d/r/h/s` | Go to Dashboard/Review/History/Settings | Global |
| `Cmd+Shift+A` | Approve all high-confidence | Review page |
| `Cmd+Enter` | Open rename preview | Review page |
| `Esc` | Close modal | When modal open |

---

## Icons

Lucide-style SVGs: 24x24 viewBox, 2px stroke, round caps/joins, currentColor.
Default sizing via CSS: `svg { width: 1em; height: 1em; }`.
Nav icons: 18x18. Button icons: 14x14. Action icons: 14x14. Activity dots: 12x12.

---

## Poster Tint Palette

8 gradient pairs for placeholder posters, derived from the Sphix palette:

```
['#e54bba', '#ff974b']   // pink-orange
['#7200e4', '#e54bba']   // purple-pink
['#125dff', '#49b8fe']   // royal blue
['#28d9a0', '#125dff']   // teal-blue
['#ff974b', '#db413c']   // orange-red
['#9b18a6', '#7200e4']   // magenta-purple
['#ffc94a', '#ff974b']   // gold-orange
['#0a5d3f', '#28d9a0']   // deep emerald
```

Tint selected by hashing the title string. Initials: first letter of up to 2 significant words (skip "the", "of", "a", "an", "and", "to", "part").

---

## Scrollbar

Webkit scrollbar: 10px width, transparent track, `rgba(255,255,255,0.08)` thumb with 10px radius.
Hover thumb: `rgba(255,255,255,0.14)`.

## Selection

`::selection`: `--accent-soft` background, `--ink` color.
