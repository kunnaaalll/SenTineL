# Sentinel — Brand & Visual Design Guidelines

*Version 6.0 — Dark Engineered Canvas & Sunset Accent System*

---

## 1. Brand Essence & Philosophy

Sentinel is an agentic financial intelligence copilot designed for institutional analysts, researchers, and professional investors. It analyzes SEC filings, earnings calls, and market news with verifiable citations and traceable agent reasoning.

### Core Character Attributes
- **Engineered & Precise:** Pure near-black canvas with 1px hairline borders and crisp typography.
- **Calm & High-Contrast:** `#0A0A0A` dark canvas with pure white displays and high-legibility body text.
- **Accented with Purpose:** Warm sunset orange (`#FF7A17`) accents used deliberately for primary callouts, active indicators, and citation badges.
- **Honest & Verifiable:** Every financial claim is grounded to primary SEC filings and news sources with interactive `[n]` citations.

---

## 2. Logo System

The Sentinel identity centers around a **clear, flat geometric S-mark** with integrated sentinel cross-anchoring.

```
       ┌───────────────────────────────┐
       │   ●═══════════════════●       │  Upper Sunset Arm (#FF7A17)
       │         ╲           ║         │
       │          ╲          ║         │
       │           ╲         ║         │
       │            ●════════╝         │  Central Interlocking Pivot
       │            ║        ╲         │
       │            ║         ╲        │
       │            ║          ╲       │
       │            ●═══════════●      │  Lower Sunset Arm
       │             Charcoal Anchor   │  (#FFFFFF / Contrast)
       └───────────────────────────────┘
```

### 2.1 Logo Construction & Meaning
- **Geometric S-Curve:** Continuous architectural sweep representing the flow of financial filings and real-time market data.
- **Diagonal Anchor Axis:** Precision cross-brace in white/ink symbolizing rigorous validation against primary sources.
- **Sentinel Cardinal Nodes:** Precision calibration points at the extremes and center representing vigilant surveillance over disclosures.

### 2.2 Variants & Usage
1. **Symbol Mark (`variant="symbol"`):** Standalone geometric S-mark. Used for app icons, avatars, header marks, and startup screens.
2. **Wordmark (`variant="wordmark"`):** Typographic lockup with `SENTINEL` in display font followed by an orange period (`.`).
3. **Full Lockup (`variant="full"`):** Symbol + Wordmark + optional "Financial Intelligence" subtitle for splash screens, formal reports, and documentation headers.
4. **Compact (`variant="compact"`):** Optimized at small dimensions (16px–24px) for desktop sidebar rail, browser favicons (`icon.svg`), and mobile headers.

---

## 3. Color Palette & Token Architecture

The Sentinel color system uses an **engineered dark canvas foundation** paired with warm sunset orange accents and crisp hairline dividers.

| Token | Name | Hex | Usage & Role | Contrast Ratio |
|---|---|---|---|---|
| `--background` | Dark Canvas | `#0A0A0A` | Page background, near-black foundation | Base canvas |
| `--surface` | Charcoal Card | `#141518` | Primary card panels, assistant responses | 1.15:1 on bg |
| `--surface-muted`| Muted Surface | `#191B1F` | Secondary cards, example prompts, table headers | 1.3:1 on bg |
| `--surface-raised`| Raised Bubble | `#1C1D22` | User chat bubbles, elevated cards | 1.4:1 on bg |
| `--ink` | Pure White | `#FFFFFF` | Headings, primary displays, high-contrast labels | **21:1** (AAA) |
| `--ink-soft` | High-Legibility Body | `#DADBDF` | Assistant answer body, markdown text, list items | **15.2:1** (AAA) |
| `--ink-faint` | Tracked Metadata | `#7D8187` | Timestamps, hints, mono labels, chunk IDs | **5.8:1** (AA) |
| `--line` | Hairline Divider | `#212327` | Card borders, section dividers, table grid lines | 1.3:1 on bg |
| `--line-strong` | Active Border | `#2F333A` | Focused container borders, hover borders | 1.8:1 on bg |
| `--accent` | Sunset Orange | `#FF7A17` | Active indicator dots, card left-border accent, buttons | **6.1:1** (AAA) |
| `--accent-strong`| Sunset Light | `#FF9E4F` | Button hover/active states, inline citation badges | **8.8:1** (AAA) |
| `--accent-soft` | Sunset Mist | `#231710` | Citation button backgrounds, tag badges | Tint surface |
| `--success` | Emerald Green | `#10B981` | Verified filings, ready status pills | **7.2:1** (AAA) |
| `--warning` | Amber Warning | `#D97706` | Limitations caveats, partial data warnings | **5.5:1** (AA) |
| `--danger` | Crimson Danger | `#EF4444` | Error notices, delete actions, request cancellation | **5.2:1** (AA) |

*All text/background pairings strictly meet WCAG 2.2 Level AA / AAA standards.*

---

## 4. Typography

```
Display Headings:     Newsreader / Inter Display (Weight 700 / 600)
User Interface & Body: Inter / System Sans (Weight 400 / 500)
Metrics & Citations:  JetBrains Mono / GeistMono (Tabular Nums)
```

### Hierarchy Scale
- **Display 1 (H1 / Splash):** `2.25rem (36px)` / `line-height: 1.2` / Newsreader Bold
- **Display 2 (H2 / Section):** `1.5rem (24px)` / `line-height: 1.3` / Newsreader Bold
- **Section Heading (H3):** `0.75rem (12px)` / `letter-spacing: 0.08em` / JetBrains Mono Bold Uppercase
- **Body Regular:** `0.875rem (14px)` / `line-height: 1.6` / Inter Regular (`#1A1816`)
- **Body Small / Captions:** `0.75rem (12px)` / `line-height: 1.5` / Inter Medium (`#4F4A45`)
- **Data / Ticker / Chunk IDs:** `0.6875rem (11px)` / `tabular-nums` / JetBrains Mono Medium

---

## 5. Spacing, Elevation & Cards

### 5.1 Card Structure
- **Border Radius:** `16px` (`rounded-2xl`) for major panels; `8px` (`rounded-lg`) for buttons and interactive chips; `4px` for focus rings.
- **Editorial Card Accent:** Assistant answer cards feature a distinct `3px` left border in terracotta (`--accent`) to clearly anchor research answers in the transcript.
- **Shadows:** Restrained, diffused ambient shadows (`rgba(26, 24, 22, 0.04)` and `rgba(26, 24, 22, 0.02)`). No deep dark shadows or neon glows.

### 5.2 Sticky Composer Architecture
- The query composer is fixed to the bottom of the viewport with safe-area bottom insets.
- The transcript container maintains a generous bottom clearance (`pb-44 sm:pb-48`) so the composer never obscures citations, limitations, or agent trace steps.

---

## 6. Prohibited Visual Treatments (Zero Tolerance)

To preserve Sentinel’s authoritative institutional credibility, the following visual treatments are strictly prohibited:

1. ❌ **No Glowing Balls, Orbs, or Constellations:** No pulsating glowing spheres, space dust, or rotating galaxy effects.
2. ❌ **No Heavy Glassmorphism:** No 30px frosted blur sheets or translucent layered cards over animated gradients.
3. ❌ **No Neon or Electric Colors:** No fluorescent blues, neon purples, or bright cyan highlights.
4. ❌ **No Bright Yellow Buttons:** No mustard or high-saturation yellow CTA buttons.
5. ❌ **No Generic SaaS Dashboard Cards:** Avoid cookie-cutter generic tech startup aesthetics.
6. ❌ **No Bouncing Loading Dots or Copied AI Spinners:** No bouncing 3-ball loaders or spinning circles. Use the structured Sentinel research signal.
7. ❌ **No Fabricated Progress / Percentages:** Never display fake loading percentages or simulated stage speeds.

---

## 7. Motion & Accessibility

### 7.1 Reduced Motion (`prefers-reduced-motion: reduce`)
- All animations (signal sweep, pulse dots, fade-ups, reveal transitions) automatically freeze to static states when reduced motion is preferred by the operating system.

### 7.2 Assistive Technology
- **Live Regions:** Status updates and stage transitions are announced politely to screen readers via `<div role="status" aria-live="polite">`.
- **Keyboard Navigation:** Full keyboard operability with visible focus rings (`2px solid var(--focus)` with `2px` offset), Enter submit, Shift+Enter newline, and Escape cancellation.
- **Navigation Landmarks:** Distinct `<nav aria-label="Primary">`, `<nav aria-label="Chat history">`, and `<main id="main-content">` landmarks.
