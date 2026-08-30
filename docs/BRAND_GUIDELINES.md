# Sentinel — Brand & Visual Design Guidelines

*Version 5.0 — Light-First Editorial Financial Intelligence System*

---

## 1. Brand Essence & Philosophy

Sentinel is an agentic financial intelligence copilot designed for institutional analysts, researchers, and professional investors. It analyzes SEC filings, earnings calls, and market news with verifiable citations and traceable agent reasoning.

### Core Character Attributes
- **Editorial & Grounded:** Reads like a premium financial publication (e.g., Financial Times, The Economist) rather than a toy chatbot.
- **Calm & Trustworthy:** Warm paper foundation and ink typography that promotes deep focus during prolonged research sessions.
- **Precise & Architectural:** Clean lines, deliberate spacing, and strict geometric precision without decorative fluff.
- **Honest & Verifiable:** Every fact is tethered to primary evidence. No fabricated progress, no fake percentages.

---

## 2. Logo System

The Sentinel identity centers around a **clear, flat geometric S-mark** with integrated sentinel cross-anchoring.

```
       ┌───────────────────────────────┐
       │   ●═══════════════════●       │  Upper Terracotta Arm (#C25E3E)
       │         ╲           ║         │
       │          ╲          ║         │
       │           ╲         ║         │
       │            ●════════╝         │  Central Interlocking Pivot
       │            ║        ╲         │
       │            ║         ╲        │
       │            ║          ╲       │
       │            ●═══════════●      │  Lower Terracotta Arm
       │             Charcoal Anchor   │  (#1A1816)
       └───────────────────────────────┘
```

### 2.1 Logo Construction & Meaning
- **Geometric S-Curve:** Continuous architectural sweep representing the flow of financial filings and real-time market data.
- **Diagonal Anchor Axis:** 45-degree cross-brace in charcoal ink symbolizing rigorous validation and cross-checking against primary sources.
- **Sentinel Cardinal Nodes:** Precision calibration points at the extremes and center representing vigilant surveillance over disclosures.

### 2.2 Variants & Usage
1. **Symbol Mark (`variant="symbol"`):** Standalone geometric S-mark. Used for app icons, avatars, header marks, and startup screens.
2. **Wordmark (`variant="wordmark"`):** Typographic lockup with `SENTINEL` in editorial display font followed by a terracotta period (`.`).
3. **Full Lockup (`variant="full"`):** Symbol + Wordmark + optional "Financial Intelligence" subtitle for splash screens, formal reports, and documentation headers.
4. **Compact (`variant="compact"`):** Optimized at small dimensions (16px–24px) for desktop sidebar rail, browser favicons (`icon.svg`), and mobile headers.

### 2.3 Clear Space & Minimum Sizes
- **Clear Space:** Maintain minimum clear space equal to `0.5 × size` on all four sides.
- **Minimum Digital Size:** 16px × 16px for favicon/compact, 24px × 24px for header navigation.

---

## 3. Color Palette & Token Architecture

The Sentinel color system uses a **light-first warm paper foundation** paired with deep charcoal ink and warm terracotta copper accents.

| Token | Name | Hex | Usage & Role | Contrast Ratio |
|---|---|---|---|---|
| `--background` | Warm Paper | `#FAF7F2` | Page background, calm reading surface | Base canvas |
| `--surface` | Pure White | `#FFFFFF` | Primary card panels, composer container | 1.05:1 on bg |
| `--surface-muted`| Warm Stone | `#F2ECE4` | Secondary cards, example prompts, table headers | 1.1:1 on bg |
| `--ink` | Obsidian Charcoal | `#1A1816` | Headings, primary body text, active state text | **14.8:1** (AAA) |
| `--ink-soft` | Slate Charcoal | `#4F4A45` | Secondary text, labels, metadata | **7.6:1** (AAA) |
| `--ink-faint` | Warm Stone Gray | `#7C756D` | Timestamps, hints, borders, subtle icons | **4.6:1** (AA) |
| `--accent` | Muted Terracotta | `#C25E3E` | Primary action buttons, active indicator dots, card border accents | **4.7:1** (AA) |
| `--accent-strong`| Deep Rust | `#A34326` | Button hover/active states | **6.4:1** (AAA) |
| `--accent-soft` | Terracotta Mist | `#FBF0EB` | Tag badges, active pill backgrounds | Tint surface |
| `--line` | Parchment Line | `#E5DED5` | Card dividers, section borders, table grid lines | 1.2:1 |
| `--line-strong` | Active Border | `#CFC5B8` | Focused container borders, hover borders | 1.5:1 |
| `--success` | Sage Green | `#2D6A4F` | Verified filings, high similarity matches, ready pills | **6.2:1** (AAA) |
| `--warning` | Ochre Amber | `#B37D2A` | Limitations caveats, partial data warnings | **3.8:1** (UI) |
| `--warning-ink` | Ochre Ink | `#784E10` | Warning card body text | **6.5:1** (AAA) |
| `--danger` | Crimson Rust | `#C24141` | Error notices, delete actions, request cancellation | **4.8:1** (AA) |

*All primary text/background pairings meet or exceed WCAG 2.2 Level AA requirements (≥4.5:1).*

---

## 4. Typography

Sentinel pairs a distinguished editorial serif for headings with a crisp modern sans for interfaces and a monospaced font for financial tabular metrics.

```
Editorial Headings:   Newsreader / Georgia (Display Serif)
User Interface & Body: Inter / System Sans
Metrics & Citations:  JetBrains Mono / ui-monospace
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
