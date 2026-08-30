# Sentinel — Design System v4
## "Obsidian Aurora" — Glassmorphic Visual Overhaul

**Supersedes:** the visual skin in `SENTINEL_DESIGN_V3.md` (flat midnight-navy/gold). The structural ideas from v3 — the Ledger citation treatment, the trading-floor agent trace — stay exactly as designed. This document replaces *how everything looks*: color system and full glass/depth treatment.

---

## 1. Direction

**Obsidian Aurora**: a near-black obsidian base with layered frosted glass panels, lit from behind by a slow-moving aurora gradient (teal → violet → gold) that bleeds through the glass at low intensity. Gold stays as the "hard data" accent (numbers, ledger indices) — it's earned equity from v2/v3 and reads correctly as financial. The aurora gradient is new: it lives in ambient glow, borders, and active/focus states, giving the product real visual richness without turning it into a rainbow.

Think: a research terminal built into obsidian glass, lit from underneath by a data-center's ambient glow.

---

## 2. Color System

### 2.1 Base (opaque, behind all glass)
```
--obsidian-950:  #050710   (page background — near-black, slightly blue)
--obsidian-900:  #090c18
--obsidian-800:  #0f1425
```

### 2.2 Aurora gradient (the new signature)
```
--aurora-teal:    #2dd4bf
--aurora-violet:  #8b5cf6
--aurora-gold:    #f0b429

--aurora-gradient: linear-gradient(115deg, var(--aurora-teal) 0%, var(--aurora-violet) 50%, var(--aurora-gold) 100%);
```
Used for: ambient background blobs, focus/active glows, hover borders on primary actions, the wake-screen orb, the "in progress" pulse on agent trace stations. **Never** used for body text or large fills — it's a light source, not a paint color.

### 2.3 Glass surfaces (translucent, layered over the base + aurora blobs)
```
--glass-1: rgba(255, 255, 255, 0.04)   /* ambient panels: header, footer */
--glass-2: rgba(255, 255, 255, 0.06)   /* standard cards: message bubbles, source cards */
--glass-3: rgba(255, 255, 255, 0.09)   /* raised/active: composer, expanded ledger row, modals */

--glass-border: rgba(255, 255, 255, 0.10)
--glass-border-strong: rgba(255, 255, 255, 0.18)
--glass-highlight: rgba(255, 255, 255, 0.14)   /* inner top-edge highlight, see 3.2 */
```

### 2.4 Text
```
--ink:       #f3f1ea   (warm-white, unchanged personality from v3)
--ink-soft:  #b9b4c2   (cooler than v3 — reads correctly against violet-tinted glass)
--ink-faint: #726d7f
```

### 2.5 Data accent (unchanged role from v3 — gold is for numbers, ledger indices, financial figures)
```
--gold:        #f0b429
--gold-strong: #ffcc4d
--gold-soft:   rgba(240, 180, 41, 0.12)   /* now translucent, sits correctly on glass */
```

### 2.6 Semantic (re-saturated to stay legible through blur)
```
--success: #34d399   --success-soft: rgba(52, 211, 153, 0.14)
--warning: #fbbf24   --warning-soft: rgba(251, 191, 36, 0.14)
--danger:  #f87171   --danger-soft:  rgba(248, 113, 113, 0.14)
--match-high: #34d399   --match-medium: #fbbf24   --match-low: #b9b4c2
```

---

## 3. The Morphism System

This is the part that makes glassmorphism look premium instead of like a free UI-kit template. Three ingredients, applied consistently: **ambient light source**, **layered blur tiers**, **edge treatment**.

### 3.1 Ambient light source (behind everything, fixed)
Three large, heavily-blurred aurora-colored blobs, fixed-position behind all content, very low opacity, drifting extremely slowly:
```css
.aurora-field {
  position: fixed;
  inset: 0;
  z-index: -1;
  overflow: hidden;
  pointer-events: none;
}
.aurora-field::before,
.aurora-field::after,
.aurora-field span {
  content: "";
  position: absolute;
  width: 60vw;
  height: 60vw;
  border-radius: 50%;
  filter: blur(120px);
  opacity: 0.18;
}
.aurora-field::before { background: var(--aurora-teal);   top: -20%; left: -10%; }
.aurora-field::after  { background: var(--aurora-violet); top: 20%;  right: -15%; }
.aurora-field span    { background: var(--aurora-gold);   bottom: -25%; left: 30%; width: 45vw; height: 45vw; opacity: 0.10; }
```
Animate drift only under `prefers-reduced-motion: no-preference` (60–90s ease-in-out loops, translate ±5%). Static under reduced motion — the blobs still render, they just don't move.

This is what makes the glass panels feel lit from within rather than just semi-transparent gray.

### 3.2 Glass panel recipe (apply to every card/panel/header)
```css
.glass {
  background: var(--glass-2);
  backdrop-filter: blur(20px) saturate(150%);
  -webkit-backdrop-filter: blur(20px) saturate(150%);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-lg);
  box-shadow:
    inset 0 1px 0 var(--glass-highlight),   /* top-edge highlight — sells the "glass" read */
    0 8px 32px rgba(0, 0, 0, 0.35);          /* soft drop shadow for depth */
}

@supports not (backdrop-filter: blur(1px)) {
  .glass { background: var(--obsidian-800); }  /* solid fallback, no broken transparency */
}
```
Three tiers, same recipe, different intensity:
- `.glass-ambient` (header, footer, background chrome) → `--glass-1`, `blur(12px)`
- `.glass` (default: message bubbles, source cards, ledger panel) → `--glass-2`, `blur(20px)` — the recipe above
- `.glass-raised` (composer, expanded ledger row, active/focused elements) → `--glass-3`, `blur(28px) saturate(160%)`, plus a **gradient border on focus/hover**:
```css
.glass-raised:focus-within {
  border-color: transparent;
  background-image:
    linear-gradient(var(--obsidian-900), var(--obsidian-900)) padding-box,
    var(--aurora-gradient) border-box;
  border: 1px solid transparent;
  box-shadow: 0 0 24px rgba(139, 92, 246, 0.25);  /* violet glow */
}
```

### 3.3 Edge & depth rules
- Every glass panel gets the inset top-highlight (`box-shadow: inset 0 1px 0 var(--glass-highlight)`) — this single line is what separates convincing glass from a flat translucent gray box.
- Never stack more than 3 glass layers visually on top of each other (background field → panel → raised element is the max depth) — deeper stacking reads muddy, not premium.
- Corners: `--radius-lg` (16px) on panels, `--radius-md` (10px) on inputs/chips, `--radius-full` on status dots and the aurora glow accents.

---

## 4. Where Morphism Applies (component by component)

| Surface | Treatment |
|---|---|
| Page background | `.aurora-field` fixed behind everything, `--obsidian-950` base |
| Header/nav bar | `.glass-ambient` — barely-there frosted strip, aurora bleeds through faintly at the edges |
| Chat message bubbles (assistant) | `.glass` — standard frosted panel |
| Chat message bubbles (user) | `.glass` with a subtle gold-tinted background (`--gold-soft` mixed into `--glass-2`) to visually distinguish from assistant without a hard color break |
| Composer (input area) | `.glass-raised` — the most "alive" surface on the page; gradient border activates on focus per 3.2 |
| Ledger panel (citations) | `.glass` container; each row separated by `--glass-border` hairlines, not full dividers — the glass panel itself is the "ledger book," rows are pages within it |
| Agent trace board | `.glass-ambient` strip; each station dot glows with `--aurora-gradient` when in-progress (static gold-violet gradient fill, pulsing opacity only if motion allowed), solid `--success` when complete |
| Source cards (Sources page) | `.glass` tiles in a grid, each with its own subtle ambient blob tint matching status (teal-tinted glow if available, dimmed/desaturated if unavailable) |
| Wake screen orb | Rebuild using the aurora gradient directly (conic-gradient sweep of teal→violet→gold) inside a glass sphere — replaces the flat gold orb from v2/v3 with the new palette |
| Buttons (primary) | Glass pill, `--aurora-gradient` border, fills with a soft gradient wash on hover (not solid fill — keep the glass read even on buttons) |
| Buttons (secondary/ghost) | `.glass-ambient` pill, no gradient border, just hover brightens `--glass-border` to `--glass-border-strong` |

---

## 5. What Does NOT Change From v3

Keep all structural/interaction specs from `SENTINEL_DESIGN_V3.md` exactly as written — this pass is skin-only:
- The Ledger citation concept (numbered rows, match %, expand-in-place) — just re-skinned in glass per Section 4
- The trading-floor agent trace stations — same layout logic, new glow treatment on the dots
- Typography scale, spacing scale, radius scale (Sections 3.2–3.3 of v3)
- Voice/microcopy rules (Section 8 of v3)
- Accessibility bar (Section 11 of v3) — glass surfaces must still hit contrast minimums; test `--ink` against `--glass-2` over the darkest point of the aurora field, not just against flat `--obsidian-950`, since blur+transparency can shift effective contrast

---

## 6. Contrast & Accessibility Note (important with glass)

Translucent surfaces over a moving/gradient background are the easiest way to silently fail contrast. Concrete rule for Claude Code: compute contrast for `--ink` / `--ink-soft` against the **worst-case** composited background (glass panel directly over the brightest point of an aurora blob, not over flat obsidian), and if it drops below 4.5:1, either darken the glass tier slightly for that surface or add a subtle solid backing layer behind text-heavy regions (e.g., message bubble text sits on an extra `rgba(5,7,16,0.4)` scrim beneath the glass blur, invisible as a "layer" but present for safety). Verify this in the browser, don't assume the numbers on paper hold once blur is applied.

---

## 7. Implementation Notes for Claude Code

1. Replace the token block in `app/globals.css` with Section 2's variables (keep the file's existing structure — `@theme inline` mapping, focus-ring rule, motion-reduction media query — just swap the color values and add the new `--glass-*` / `--aurora-*` tokens)
2. Add the `.aurora-field`, `.glass`, `.glass-ambient`, `.glass-raised` utility classes to `globals.css`
3. Add `<div class="aurora-field"><span></span></div>` once, in `app/layout.tsx`, as the first child of `<body>`, before the skip link
4. Apply `.glass` / `.glass-ambient` / `.glass-raised` classes to the components listed in Section 4's table — this is a class-swap on existing elements, not a rebuild; the v3 component structure (LedgerTable, AgentTraceViewer, etc.) stays intact
5. Rebuild the wake-screen orb's gradient fill (in `BackendGate.tsx`) to use `--aurora-gradient` via conic-gradient instead of the flat gold radial from v2/v3
6. Run the contrast check from Section 6 against real rendered output before calling this done — don't skip straight from "looks cool" to shipped

**Still out of scope:** no new dependencies, no backend/infra changes, no changes to `lib/api.ts` or data logic — this remains a pure visual-layer pass on top of the v3 structural spec.
