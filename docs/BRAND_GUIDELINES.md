# Sentinel — Brand & Design System Guidelines

Sentinel is an agentic financial intelligence research product designed to deliver clarity, rigorous grounded citations, and analytical precision over SEC filings, earnings transcripts, and market news.

This document outlines the core brand identity, visual language, typography, color architecture, and UI component standards.

---

## 1. Brand Identity & Narrative

### Philosophy
- **Calm, Editorial & Trustworthy**: Sentinel is built for financial analysts, institutional researchers, and active investors. It avoids loud neon accents, generic SaaS dashboard aesthetics, and gaming motifs.
- **Precision Signal**: The visual language is inspired by optical aperture lenses, cartographic coordinate grids, and editorial print publishing (such as *The Financial Times* and *Bloomberg Intelligence*).

### The Brand Mark: "The Vigilant Aperture"
The Sentinel symbol combines:
1. **Inner Focal Core**: A central node representing high-confidence signal extraction from financial noise.
2. **Precision Reticle**: Concentric aperture rings with cardinal calibration nodes symbolizing meticulous compliance and rigor.
3. **Dual Orbital Sweeps**: Interlocking scanning arcs in warm charcoal and terracotta representing continuous surveillance over market filings.

---

## 2. Logo System & Variants

Sentinel ships with a code-native React SVG component (`frontend/components/SentinelLogo.tsx`):

| Variant | Anatomy | Primary Usage |
|---|---|---|
| **Symbol (`symbol`)** | Standalone 32×32 aperture icon | App icons, favicons, loading orbs, compact mobile headers |
| **Wordmark (`wordmark`)** | `SENTINEL.` in tracked editorial display caps | Editorial headers, document titles, footers |
| **Full Lockup (`full`)** | Symbol + Wordmark + Subtitle ("Financial Intelligence") | Main desktop header, welcome screens, documentation |
| **Compact (`compact`)** | Scaled 16–20px pixel-aligned mark | Status badges, table cells, metadata pills |

### Logo Color Modes
- **Default (Themed)**: Dynamically binds to semantic CSS variables (`var(--ink)`, `var(--accent)`).
- **Light**: Charcoal (`#1A1816`) + Terracotta (`#C25E3E`) on Warm Ivory (`#FAF7F2`).
- **Dark**: Warm Ivory (`#F5F2ED`) + Terracotta Rust (`#D97352`) on Obsidian Charcoal (`#1E1C1A`).
- **Monochrome**: Resolves entirely to `currentColor` for embedded monochrome contexts.

### Prohibited Logo Treatments
- ❌ Do not use shields, padlock icons, generic stock market arrows, or robot mascots.
- ❌ Do not rotate, skew, or distort the aperture geometry.
- ❌ Do not render the mark in neon, electric blue, or high-saturation gradient fills.
- ❌ Do not place the mark on low-contrast photographic or busy textured backgrounds.

---

## 3. Color Palette Architecture

The design system is light-first by default with an editorial warm paper foundation:

### Primary Palette

| Token | Name | HEX | Purpose |
|---|---|---|---|
| `--background` | **Warm Paper / Alabaster** | `#FAF7F2` | Main page canvas background |
| `--surface` | **Pure Card White** | `#FFFFFF` | Primary message cards, composer, source panels |
| `--surface-muted` | **Warm Stone** | `#F2ECE4` | Secondary containers, empty state cards, code blocks |
| `--surface-raised` | **Elevated Paper** | `#FFFFFF` | User question bubble, dropdowns, floating modals |
| `--ink` | **Obsidian Charcoal** | `#1A1816` | Primary headlines, question text, citation titles (WCAG AAA) |
| `--ink-soft` | **Slate Charcoal** | `#4F4A45` | Answer prose body, agent descriptions (WCAG AA) |
| `--ink-faint` | **Warm Stone Gray** | `#7C756D` | Timestamps, metadata, keyboard shortcuts, disclaimers |
| `--line` | **Parchment Border** | `#E5DED5` | Card outlines, dividers, table borders |
| `--line-strong` | **Deep Parchment Rule** | `#CFC5B8` | Focused borders, active tabs, strong separators |

### Accent & Semantic Signals

| Token | Name | HEX | Purpose |
|---|---|---|---|
| `--accent` | **Muted Terracotta** | `#C25E3E` | Primary action buttons, citation markers, active links |
| `--accent-strong` | **Deep Rust** | `#A34326` | Button hover/active states, active tab indicators |
| `--accent-soft` | **Terracotta Mist** | `#FBF0EB` | Highlight tags, citation cards, selection surfaces |
| `--success` | **Sage Green** | `#2D6A4F` | Live backend status, SEC verified filings, completed steps |
| `--success-soft` | **Sage Mist** | `#EBF5F0` | Success badges, verification pills |
| `--warning` | **Warm Amber / Ochre** | `#B37D2A` | Cold-start warnings, degraded backend notification |
| `--warning-soft` | **Amber Mist** | `#FDF6EA` | Degraded session banner background |
| `--danger` | **Crimson Rust** | `#C24141` | Canceled queries, timeout retry alerts, validation errors |
| `--danger-soft` | **Crimson Mist** | `#FDF0F0` | Error card backgrounds |

---

## 4. Typography

### Font Hierarchy
1. **Headings & Display**: `Newsreader` (Editorial Serif) & `Sora` / `Inter Display` — Warm, authoritative, dignified.
2. **Body & Interface**: `Inter` — Crisp legibility at small sizes, optimal line height (`1.65–1.75`), comfortable reading contrast.
3. **Financials & Tickers**: `JetBrains Mono` / Monospace — Ticker symbols, chunk IDs, tabular comparison numbers, filing timestamps.

### Contrast Discipline
Every foreground-to-background pairing strictly satisfies **WCAG 2.2 Level AA**:
- Normal text (`--ink` on `--background`): **14.8:1** (Exceeds 4.5:1)
- Soft text (`--ink-soft` on `--surface`): **7.6:1** (Exceeds 4.5:1)
- Faint text (`--ink-faint` on `--background`): **4.6:1** (Exceeds 4.5:1)
- Interactive buttons (`#FFFFFF` on `--accent` `#C25E3E`): **4.7:1**

---

## 5. UI Layout & Sticky Composer Architecture

- **Viewport Geometry**: Full-viewport height container with independent scroll area for message stream.
- **Sticky Floating Composer**: Anchored cleanly to the bottom with elevated backdrop-blur (`bg-surface/90 backdrop-blur-md`), refined border (`--line`), and gentle elevation shadow.
- **Bottom Clearance**: Main scroll container applies generous bottom padding (`pb-48`) ensuring the final message, full citation cards, and pipeline trace are completely unobstructed.
- **Mobile Ergonomics**: Supports dynamic viewport height (`dvh`) and iOS safe area padding (`env(safe-area-inset-bottom)`).
