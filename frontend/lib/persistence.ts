/**
 * lib/persistence.ts
 *
 * Local browser-only chat persistence.
 *
 * Key:     sentinel.chat.v1
 * Storage: localStorage (never sessionStorage, cookie, or server)
 *
 * Privacy guarantees:
 * - Only persists completed/canceled/error messages — never in-flight (pending).
 * - Excluded from storage: API keys, auth headers, Langfuse secret keys,
 *   raw backend error bodies, NEXT_PUBLIC_* credentials, AbortController refs.
 * - trace_url is stored only as-is from the backend (already a safe public URL).
 * - No account identifiers, no multi-user sessions, no backend persistence.
 *
 * Resilience:
 * - Catches SecurityError (storage blocked by browser/policy).
 * - Catches QuotaExceededError silently — chat stays functional in memory.
 * - Handles malformed JSON, missing keys, wrong schema version.
 * - Deduplicates messages by id on restore.
 * - Enforces MAX_MESSAGES and MAX_STORAGE_BYTES to prevent unbounded growth.
 */

import type { Citation } from "./api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

export const STORAGE_KEY = "sentinel.chat.v1";
export const SCHEMA_VERSION = 1;
export const MAX_MESSAGES = 200;
export const MAX_STORAGE_BYTES = 2_000_000; // 2 MB

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * The safe subset of ChatMessage that survives storage.
 *
 * "pending" status is intentionally absent — in-flight requests are never
 * persisted. Only completed states are stored.
 */
export interface PersistedMessage {
  id: string;
  role: "user" | "assistant";
  question?: string;
  status: "complete" | "canceled" | "error";
  answer?: string;
  citations?: Citation[];
  agent_path?: string[];
  /** Null or a safe public Langfuse URL — never a secret key. */
  trace_url?: string | null;
  forcedAgents?: boolean;
  /** Sanitized, user-safe error description only — no raw backend responses. */
  errorCode?: string;
  errorMessage?: string;
  /** ISO timestamp when the message was saved. */
  savedAt: string;
}

interface StorageEnvelope {
  version: number;
  savedAt: string;
  messages: PersistedMessage[];
}

// ---------------------------------------------------------------------------
// Storage availability test
// ---------------------------------------------------------------------------

export function isStorageAvailable(): boolean {
  try {
    const key = "__sentinel_storage_test__";
    localStorage.setItem(key, "1");
    localStorage.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Load
// ---------------------------------------------------------------------------

/**
 * Loads persisted messages from localStorage.
 *
 * MUST be called only after client hydration (inside useEffect) to avoid
 * Next.js SSR/hydration mismatches.
 *
 * Returns [] on any error — storage failure is graceful degradation, not fatal.
 */
export function loadPersistedMessages(): PersistedMessage[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];

    const parsed: unknown = JSON.parse(raw);
    if (!isValidEnvelope(parsed)) return [];

    const envelope = parsed as StorageEnvelope;

    // Schema version mismatch — discard rather than risk corruption
    if (envelope.version !== SCHEMA_VERSION) return [];

    const messages = envelope.messages;
    if (!Array.isArray(messages)) return [];

    // Deduplicate by id (safety against double-restore)
    const seen = new Set<string>();
    return messages.filter((m) => {
      if (!isValidPersistedMessage(m)) return false;
      if (seen.has(m.id)) return false;
      seen.add(m.id);
      return true;
    });
  } catch {
    // SecurityError, QuotaExceededError, JSON parse failure, etc.
    return [];
  }
}

// ---------------------------------------------------------------------------
// Save
// ---------------------------------------------------------------------------

/**
 * Saves completed messages to localStorage.
 *
 * Filters out in-flight (pending) messages before writing.
 * Enforces MAX_MESSAGES (trims oldest first).
 * Silently ignores quota errors.
 */
export function saveMessages(
  messages: ReadonlyArray<{
    id: string;
    role: "user" | "assistant";
    question?: string;
    status: string;
    answer?: string;
    citations?: Citation[];
    agent_path?: string[];
    trace_url?: string | null;
    forcedAgents?: boolean;
    errorCode?: string;
    errorMessage?: string;
  }>,
): void {
  try {
    // Filter to safe, completed messages only
    const safe: PersistedMessage[] = messages
      .filter((m) => m.status !== "pending")
      .map((m) => ({
        id: m.id,
        role: m.role,
        ...(m.question !== undefined ? { question: m.question } : {}),
        status: (m.status === "complete" || m.status === "canceled" || m.status === "error"
          ? m.status
          : "error") as PersistedMessage["status"],
        ...(m.answer !== undefined ? { answer: m.answer } : {}),
        ...(m.citations !== undefined ? { citations: m.citations } : {}),
        ...(m.agent_path !== undefined ? { agent_path: m.agent_path } : {}),
        // trace_url is already a safe public URL from the backend
        ...(m.trace_url !== undefined ? { trace_url: m.trace_url } : {}),
        ...(m.forcedAgents !== undefined ? { forcedAgents: m.forcedAgents } : {}),
        // errorCode and errorMessage are already sanitized by userMessage()
        ...(m.errorCode !== undefined ? { errorCode: m.errorCode } : {}),
        ...(m.errorMessage !== undefined ? { errorMessage: m.errorMessage } : {}),
        savedAt: new Date().toISOString(),
      }));

    // Don't save if nothing completed (all messages were pending or empty list)
    if (safe.length === 0) return;

    // Enforce max messages (drop oldest)
    const trimmed = safe.length > MAX_MESSAGES ? safe.slice(-MAX_MESSAGES) : safe;

    const envelope: StorageEnvelope = {
      version: SCHEMA_VERSION,
      savedAt: new Date().toISOString(),
      messages: trimmed,
    };

    const serialized = JSON.stringify(envelope);

    // Size guard — if the payload is too large, skip silently
    if (serialized.length > MAX_STORAGE_BYTES) {
      // Try with reduced messages
      const reduced = trimmed.slice(-50); // keep last 50 only
      const reducedEnvelope: StorageEnvelope = { ...envelope, messages: reduced };
      const reducedSerialized = JSON.stringify(reducedEnvelope);
      if (reducedSerialized.length > MAX_STORAGE_BYTES) return; // give up
      localStorage.setItem(STORAGE_KEY, reducedSerialized);
      return;
    }

    localStorage.setItem(STORAGE_KEY, serialized);
  } catch {
    // QuotaExceededError, SecurityError — chat remains functional in memory
  }
}

// ---------------------------------------------------------------------------
// Clear
// ---------------------------------------------------------------------------

export function clearPersistedMessages(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    // SecurityError — nothing to do
  }
}

// ---------------------------------------------------------------------------
// Validation helpers
// ---------------------------------------------------------------------------

function isValidEnvelope(value: unknown): value is StorageEnvelope {
  return (
    value !== null &&
    typeof value === "object" &&
    !Array.isArray(value) &&
    "version" in value &&
    typeof (value as Record<string, unknown>).version === "number" &&
    "messages" in value &&
    Array.isArray((value as Record<string, unknown>).messages)
  );
}

function isValidPersistedMessage(value: unknown): value is PersistedMessage {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const m = value as Record<string, unknown>;
  return (
    typeof m.id === "string" &&
    (m.role === "user" || m.role === "assistant") &&
    (m.status === "complete" || m.status === "canceled" || m.status === "error")
  );
}
