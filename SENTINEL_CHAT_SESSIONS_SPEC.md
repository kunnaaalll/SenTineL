# Sentinel — Chat Sessions Feature Spec
## Sidebar navigation with new chat / switch chat / rename / delete — Claude/ChatGPT/Gemini pattern

**Builds on:** `SENTINEL_DESIGN_V4_OBSIDIAN_AURORA.md` (visual system) and `SENTINEL_DESIGN_V3.md` (Ledger/trace structure). This is a new feature layer, not a redesign — it adds conversation management around the existing `ChatWindow`.

**Persistence model (decided):** browser-local only — no backend changes, no new API endpoints, no database. Conversations live in the browser via `localStorage`, scoped to the device/browser they were created in. This matches the current "no auth, single-user demo" scope in `SENTINEL_SPEC.md` — nothing here contradicts it.

**Title generation (decided):** auto-generated from the first user message, client-side, no LLM call — keeps this fully frontend-only per your speed preference. (Noted as a future upgrade path in Section 7 if you ever want ChatGPT-style summarized titles.)

---

## 1. Layout

```
┌──────────────┬──────────────────────────────────────────┐
│   SIDEBAR      │                HEADER                     │
│   (glass)      ├──────────────────────────────────────────┤
│                │                                            │
│  + New chat    │                                            │
│  ────────────  │              CHAT WINDOW                  │
│  Today          │           (existing component,            │
│   ● Apple debt  │            unchanged internals)            │
│     analysis    │                                            │
│  Yesterday      │                                            │
│   Tesla vs...   │                                            │
│  Previous 7d    │                                            │
│   NVDA earnings │                                            │
│   ...           │                                            │
│                │                                            │
└──────────────┴──────────────────────────────────────────┘
```

- **Desktop (≥1024px):** persistent sidebar, 280px wide, collapsible via a toggle in the header (collapses to a slim 56px icon rail showing only the "new chat" icon and a hamburger to reopen — same pattern as Claude's desktop UI)
- **Tablet/Mobile (<1024px):** sidebar becomes an off-canvas drawer, opened via a hamburger icon in the header, overlays the chat window with a scrim behind it, closes on selection or outside-tap

---

## 2. Sidebar Contents

### 2.1 New chat button
- Sits pinned at the top of the sidebar, always visible even when the list scrolls
- Full-width glass-raised pill button (per v4 morphism system), gold "+" icon, label "New chat"
- Behavior: clears the active `ChatWindow` to its empty state immediately. **Does not** create a storage entry yet — matches Claude/ChatGPT behavior where an empty "New chat" isn't saved to history until the first message is sent. This avoids the list filling with phantom empty conversations from accidental clicks.

### 2.2 Conversation list, grouped by recency
Group headers (plain text labels, `--ink-faint`, no dividers needed — spacing does the work):
- **Today**
- **Yesterday**
- **Previous 7 days**
- **Older** (further grouped by month if the list grows long, e.g. "November 2026")

Each group only renders if it has entries — no empty "Yesterday" header with nothing under it.

### 2.3 Conversation item
```
 ●  Apple's debt load vs competitors        [rename] [delete]
```
- Title: auto-generated, truncated to ~48 characters with ellipsis, single line
- Active conversation: highlighted with a subtle gold-tinted glass background (`--gold-soft` over `--glass-2`) and a small gold dot at the left, consistent with the aurora/gold data-accent language established in v3/v4
- Hover (desktop) / long-press (mobile): reveals rename (pencil) and delete (trash) icons on the right, replacing nothing — icons fade in over transparent background, don't shift layout
- Rename: click pencil → title becomes an inline editable text field, Enter or blur commits, Escape cancels
- Delete: click trash → the icon set briefly swaps to an inline "Delete this chat? [Confirm] [Cancel]" row in place of the title (no browser `confirm()` popup — stay in the glass UI language), auto-cancels back to normal after 4s if untouched

---

## 3. Title Auto-Generation (client-side, no API call)

Simple, deterministic, no LLM:
1. Take the first user message text
2. Strip leading/trailing whitespace and markdown syntax characters
3. Truncate to 48 characters at the nearest word boundary, append "…" if truncated
4. If the message is a question, keep the question mark; otherwise no punctuation is added

```
"What was Apple's revenue in fiscal 2024?"
→ title: "What was Apple's revenue in fiscal 2024?"

"Compare Tesla, Rivian, and Lucid's cash burn rates over the last two years and tell me which is most at risk"
→ title: "Compare Tesla, Rivian, and Lucid's cash burn ra…"
```

Title is generated once, when the first message in a conversation is sent, and stored — it does not change if the user renames later (rename overrides permanently) or if later messages would suggest a "better" title (no re-summarization — keeps this predictable and free).

---

## 4. Data Model & Storage

### 4.1 Shape (stored in `localStorage`, one key holding the whole list)
```typescript
interface StoredConversation {
  id: string;              // crypto.randomUUID()
  title: string;           // auto-generated or user-renamed
  titleIsCustom: boolean;  // true once user renames, prevents any future auto-title logic touching it
  createdAt: string;       // ISO timestamp
  updatedAt: string;       // ISO timestamp, bumped on every new message
  messages: ChatMessage[]; // reuse the existing ChatMessage type from components/MessageBubble.tsx as-is
}

// localStorage key: "sentinel:conversations"
// value: StoredConversation[]
```

### 4.2 Why `localStorage` and not `IndexedDB` for v1
`localStorage` is simpler to implement and sufficient for a single-user demo — a typical conversation with citations/trace data runs a few KB, and localStorage's ~5-10MB browser limit comfortably holds dozens of conversations. If this ever becomes a real constraint (very long research sessions, many months of history), migrating the same interface to IndexedDB later is a contained change (Section 7) — nothing else in this spec needs to change to do that migration.

### 4.3 Storage safety
- Wrap every `localStorage` write in try/catch — private browsing modes and full storage quotas can throw
- If a write fails, show a small non-blocking toast: "Couldn't save this chat — your browser's local storage may be full or disabled" — never lose the in-memory conversation the user is actively looking at, only the persistence of it
- On load, validate the parsed JSON shape defensively (a corrupted/old-format entry should be skipped, not crash the whole sidebar)

---

## 5. State Management

New hook: `lib/useConversations.ts` — the single source of truth for the sidebar and the active chat.

```typescript
function useConversations() {
  // returns:
  //   conversations: StoredConversation[]        (sorted newest-updated first)
  //   activeConversationId: string | null
  //   activeConversation: StoredConversation | null
  //   startNewChat(): void                         // clears active selection, doesn't persist yet
  //   selectConversation(id: string): void
  //   appendMessage(message: ChatMessage): void     // creates the conversation on first message if needed
  //   renameConversation(id: string, title: string): void
  //   deleteConversation(id: string): void
}
```

`ChatWindow.tsx` changes from owning its own message-array state to reading/writing through this hook — its internal rendering logic (loading states, cancellation, ledger/trace rendering) stays exactly as built; only where the message array comes from changes.

---

## 6. Visual Spec (Obsidian Aurora integration)

| Element | Treatment |
|---|---|
| Sidebar container | `.glass-ambient` — full-height frosted strip, aurora bleeds faintly at its edge nearest the chat window |
| New chat button | `.glass-raised` pill, gold "+" icon, gradient border appears on hover/focus (same recipe as v4 §3.2) |
| Conversation item (default) | Transparent background, `--ink-soft` text, sits directly on the sidebar's glass — no per-item glass panel, keeps the list visually light |
| Conversation item (active) | `--gold-soft` background wash, `--ink` text (brighter), small gold dot marker at left |
| Conversation item (hover) | `--glass-1` background fades in, rename/delete icons fade in |
| Group headers ("Today," etc.) | `--ink-faint`, `--text-xs`, uppercase, letter-spacing wide — quiet, structural, not decorative |
| Mobile drawer scrim | `rgba(5,7,16,0.6)` flat (no blur needed on the scrim itself — keep that cheap for mobile performance) |
| Collapse-to-rail state (desktop) | Sidebar shrinks to 56px, shows only new-chat icon + hamburger; conversation list hidden entirely (not scrolled-and-clipped) until expanded |

---

## 7. Explicitly Deferred (not in this pass)

- **Backend-persisted history / multi-device sync** — would need the `/conversations` CRUD API + database this spec deliberately skips; revisit if you ever add auth
- **LLM-generated conversation titles** (like ChatGPT's actual summarization call) — current spec uses free client-side truncation; upgrading later means one new lightweight backend endpoint (`POST /conversations/title`) that takes the first message and returns a short generated title — everything else in this spec (storage shape, sidebar UI) stays unchanged
- **Search across past conversations** — not requested; would be a client-side filter over `conversations` in the hook if added later, no backend needed even then
- **Export/import chat history** — not requested, but trivial later since the storage shape is already a clean JSON array

---

## 8. Component Change List (for Claude Code)

| File | Change |
|---|---|
| `lib/useConversations.ts` | **New.** Hook per Section 5 — owns all localStorage read/write, exposes CRUD + active-selection state |
| `components/Sidebar.tsx` | **New.** Renders new-chat button + grouped conversation list per Sections 2–6 |
| `components/ConversationItem.tsx` | **New.** Single row: title, active state, hover-revealed rename/delete, inline rename/delete-confirm interactions |
| `app/layout.tsx` | Add `<Sidebar />` alongside existing header/main structure; wire up desktop collapse state and mobile drawer open/close state (simple `useState`, no new library) |
| `components/ChatWindow.tsx` | Change message state source from local `useState` to `useConversations()`'s `activeConversation`/`appendMessage` — rendering logic (loading, ledger, trace) unchanged |
| `app/globals.css` | Add sidebar-specific spacing tokens if needed (likely none — existing spacing/glass tokens from v3/v4 should cover it) |

**Out of scope:** no backend changes, no new dependencies (this is achievable with `localStorage`, `crypto.randomUUID()`, and existing React state — no state-management library needed), no changes to `lib/api.ts`.

---

## 9. Accessibility

- Sidebar is a `<nav aria-label="Chat history">` landmark
- Active conversation marked with `aria-current="page"` (or `"true"` as appropriate) on its list item
- Rename input is keyboard-operable: Enter commits, Escape cancels, focus returns to the item's title button afterward
- Delete confirmation row is reachable and dismissible via keyboard (Tab to Confirm/Cancel, Escape cancels)
- Mobile drawer: focus traps inside while open, Escape closes it, focus returns to the hamburger toggle on close
- Collapsed desktop rail: icons still have accessible names (`aria-label="New chat"`, `aria-label="Expand chat history"`)
