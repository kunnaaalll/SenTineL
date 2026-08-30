/**
 * tests/persistence.test.ts
 *
 * Offline unit tests for lib/persistence.ts.
 * All tests run in jsdom. No real network, no real localStorage side-effects
 * (each test gets a fresh mock).
 */

import { describe, it, expect, beforeEach, vi, afterEach } from "vitest";
import {
  loadPersistedMessages,
  saveMessages,
  clearPersistedMessages,
  isStorageAvailable,
  STORAGE_KEY,
  SCHEMA_VERSION,
  MAX_MESSAGES,
} from "@/lib/persistence";
import type { PersistedMessage } from "@/lib/persistence";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeStore(): Record<string, string> {
  return {};
}

function mockLocalStorage(store: Record<string, string> = makeStore()) {
  const mock = {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => {
      store[key] = value;
    }),
    removeItem: vi.fn((key: string) => {
      delete store[key];
    }),
    clear: vi.fn(() => {
      Object.keys(store).forEach((k) => delete store[k]);
    }),
    get length() {
      return Object.keys(store).length;
    },
    key: vi.fn((index: number) => Object.keys(store)[index] ?? null),
  };
  vi.stubGlobal("localStorage", mock);
  return { mock, store };
}

function completeMsg(overrides: Partial<PersistedMessage> = {}): PersistedMessage {
  return {
    id: "m1",
    role: "assistant",
    question: "What was AAPL revenue?",
    status: "complete",
    answer: "Apple revenue was $391B [1].",
    citations: [],
    savedAt: new Date().toISOString(),
    ...overrides,
  };
}

function writeEnvelope(
  store: Record<string, string>,
  messages: PersistedMessage[],
  version = SCHEMA_VERSION,
) {
  store[STORAGE_KEY] = JSON.stringify({
    version,
    savedAt: new Date().toISOString(),
    messages,
  });
}

// ---------------------------------------------------------------------------
// isStorageAvailable
// ---------------------------------------------------------------------------

describe("isStorageAvailable", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("returns true when localStorage works", () => {
    mockLocalStorage();
    expect(isStorageAvailable()).toBe(true);
  });

  it("returns false when localStorage throws SecurityError", () => {
    vi.stubGlobal("localStorage", {
      setItem: vi.fn(() => {
        throw new DOMException("SecurityError");
      }),
      removeItem: vi.fn(),
      getItem: vi.fn(() => null),
    });
    expect(isStorageAvailable()).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// loadPersistedMessages
// ---------------------------------------------------------------------------

describe("loadPersistedMessages", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("returns [] when nothing is stored", () => {
    mockLocalStorage();
    expect(loadPersistedMessages()).toEqual([]);
  });

  it("returns [] on malformed JSON", () => {
    const { store } = mockLocalStorage();
    store[STORAGE_KEY] = "not valid json {{";
    expect(loadPersistedMessages()).toEqual([]);
  });

  it("returns [] on schema version mismatch", () => {
    const { store } = mockLocalStorage();
    const msg = completeMsg();
    writeEnvelope(store, [msg], SCHEMA_VERSION + 1);
    expect(loadPersistedMessages()).toEqual([]);
  });

  it("returns [] on SecurityError", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => {
        throw new DOMException("SecurityError");
      }),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    });
    expect(loadPersistedMessages()).toEqual([]);
  });

  it("restores valid completed messages", () => {
    const { store } = mockLocalStorage();
    const msg = completeMsg({ id: "m42", answer: "AAPL $391B" });
    writeEnvelope(store, [msg]);
    const result = loadPersistedMessages();
    expect(result).toHaveLength(1);
    expect(result[0]?.id).toBe("m42");
    expect(result[0]?.answer).toBe("AAPL $391B");
  });

  it("deduplicates messages with the same id", () => {
    const { store } = mockLocalStorage();
    const msg = completeMsg({ id: "dup" });
    writeEnvelope(store, [msg, { ...msg }, { ...msg }]);
    const result = loadPersistedMessages();
    expect(result).toHaveLength(1);
  });

  it("filters out invalid message shapes", () => {
    const { store } = mockLocalStorage();
    const bad = { id: 42, role: "unknown" }; // invalid
    const good = completeMsg({ id: "good1" });
    store[STORAGE_KEY] = JSON.stringify({
      version: SCHEMA_VERSION,
      savedAt: new Date().toISOString(),
      messages: [bad, good],
    });
    const result = loadPersistedMessages();
    expect(result).toHaveLength(1);
    expect(result[0]?.id).toBe("good1");
  });

  it("returns [] when messages is not an array", () => {
    const { store } = mockLocalStorage();
    store[STORAGE_KEY] = JSON.stringify({
      version: SCHEMA_VERSION,
      savedAt: new Date().toISOString(),
      messages: "not an array",
    });
    expect(loadPersistedMessages()).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// saveMessages
// ---------------------------------------------------------------------------

describe("saveMessages", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("saves completed messages and they can be reloaded", () => {
    const { store } = mockLocalStorage();
    const messages = [
      {
        id: "u1",
        role: "user" as const,
        question: "What is AAPL revenue?",
        status: "complete" as const,
      },
      {
        id: "a1",
        role: "assistant" as const,
        question: "What is AAPL revenue?",
        status: "complete" as const,
        answer: "$391B",
      },
    ];
    saveMessages(messages);
    const restored = loadPersistedMessages();
    expect(restored).toHaveLength(2);
    expect(restored[1]?.answer).toBe("$391B");
  });

  it("does NOT persist in-flight (pending) messages", () => {
    const { store } = mockLocalStorage();
    saveMessages([
      { id: "u1", role: "user" as const, question: "Q?", status: "complete" as const },
      { id: "a1", role: "assistant" as const, question: "Q?", status: "pending" as const },
    ]);
    const restored = loadPersistedMessages();
    expect(restored.every((m) => (m.status as string) !== "pending")).toBe(true);
    expect(restored).toHaveLength(1);
  });

  it("does NOT persist API keys, auth headers, or secrets", () => {
    const { store } = mockLocalStorage();
    const msgWithSecret = {
      id: "a1",
      role: "assistant" as const,
      question: "Q",
      status: "complete" as const,
      answer: "Answer",
      // These fields should never exist on ChatMessage, but even if they sneak in:
    };
    saveMessages([msgWithSecret]);
    const raw = store[STORAGE_KEY];
    expect(raw).not.toMatch(/api_key/i);
    expect(raw).not.toMatch(/authorization/i);
    expect(raw).not.toMatch(/LANGFUSE_SECRET/i);
    expect(raw).not.toMatch(/OPENAI_API_KEY/i);
  });

  it("enforces MAX_MESSAGES — trims oldest", () => {
    const { store } = mockLocalStorage();
    const many = Array.from({ length: MAX_MESSAGES + 50 }, (_, i) => ({
      id: `m${i}`,
      role: "assistant" as const,
      question: "Q",
      status: "complete" as const,
      answer: `Answer ${i}`,
    }));
    saveMessages(many);
    const restored = loadPersistedMessages();
    expect(restored.length).toBeLessThanOrEqual(MAX_MESSAGES);
  });

  it("silently swallows QuotaExceededError", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => {
        throw new DOMException("QuotaExceededError");
      }),
      removeItem: vi.fn(),
    });
    // Should not throw
    expect(() =>
      saveMessages([
        { id: "a1", role: "assistant" as const, question: "Q", status: "complete" as const },
      ]),
    ).not.toThrow();
  });

  it("does not save an empty array", () => {
    const { mock } = mockLocalStorage();
    saveMessages([]);
    expect(mock.setItem).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// clearPersistedMessages
// ---------------------------------------------------------------------------

describe("clearPersistedMessages", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("removes the storage key", () => {
    const { store } = mockLocalStorage();
    const msg = completeMsg();
    writeEnvelope(store, [msg]);
    clearPersistedMessages();
    expect(loadPersistedMessages()).toEqual([]);
  });

  it("does not throw when storage is blocked", () => {
    vi.stubGlobal("localStorage", {
      removeItem: vi.fn(() => {
        throw new DOMException("SecurityError");
      }),
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
    });
    expect(() => clearPersistedMessages()).not.toThrow();
  });
});

// ---------------------------------------------------------------------------
// Save → restore round-trip with all fields
// ---------------------------------------------------------------------------

describe("save → restore round-trip", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("preserves citations, agent_path, and trace_url", () => {
    const { store } = mockLocalStorage();
    const msg = {
      id: "a1",
      role: "assistant" as const,
      question: "Compare AAPL vs MSFT",
      status: "complete" as const,
      answer: "AAPL $391B, MSFT $245B [1][2]",
      citations: [
        {
          source_id: "SEC:AAPL:10-K:2024",
          title: "Apple 10-K",
          excerpt: "Net sales $391B",
          url: "https://www.sec.gov/...",
          chunk_id: "abc123",
          score: 0.91,
          section: "Item 7",
          page_or_position: null,
        },
      ],
      agent_path: ["classify", "fetch", "extract", "compare", "synthesize"],
      trace_url: "https://langfuse.example.com/trace/abc",
      forcedAgents: true,
    };
    saveMessages([msg]);
    const [restored] = loadPersistedMessages();
    expect(restored?.citations).toHaveLength(1);
    expect(restored?.citations?.[0]?.source_id).toBe("SEC:AAPL:10-K:2024");
    expect(restored?.agent_path).toEqual(msg.agent_path);
    expect(restored?.trace_url).toBe(msg.trace_url);
    expect(restored?.forcedAgents).toBe(true);
  });

  it("preserves error states (canceled, error)", () => {
    mockLocalStorage();
    const messages = [
      { id: "a1", role: "assistant" as const, question: "Q", status: "canceled" as const },
      {
        id: "a2",
        role: "assistant" as const,
        question: "Q2",
        status: "error" as const,
        errorCode: "timeout",
        errorMessage: "Request timed out",
      },
    ];
    saveMessages(messages);
    const restored = loadPersistedMessages();
    expect(restored).toHaveLength(2);
    expect(restored[0]?.status).toBe("canceled");
    expect(restored[1]?.status).toBe("error");
    expect(restored[1]?.errorCode).toBe("timeout");
  });
});
