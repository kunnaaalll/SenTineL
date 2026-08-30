/**
 * tests/conversations.test.ts
 *
 * Offline unit tests for lib/useConversations.ts.
 * Tests title generation, chronological grouping, storage safety, quota errors,
 * legacy migration, and secret exclusion.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  generateConversationTitle,
  groupConversations,
  loadStoredConversations,
  saveStoredConversations,
  CONVERSATIONS_STORAGE_KEY,
  CONVERSATIONS_SCHEMA_VERSION,
  type StoredConversation,
} from "@/lib/useConversations";

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

describe("generateConversationTitle", () => {
  it("generates clean title from short question", () => {
    const title = generateConversationTitle("What was Apple's revenue in fiscal 2024?");
    expect(title).toBe("What was Apple's revenue in fiscal 2024?");
  });

  it("truncates long questions at a word boundary with ellipsis", () => {
    const longMsg =
      "Compare Tesla, Rivian, and Lucid cash burn rates over the last two years and tell me which is most at risk of insolvency";
    const title = generateConversationTitle(longMsg);
    expect(title.length).toBeLessThanOrEqual(52);
    expect(title).toMatch(/…$/);
    expect(title).not.toMatch(/insolvency/);
  });

  it("preserves question mark when question is truncated", () => {
    const longQ =
      "What were Microsoft and Google cloud revenues and profit margins across all four quarters of fiscal year 2024?";
    const title = generateConversationTitle(longQ);
    expect(title).toMatch(/…\?$/);
  });

  it("strips leading markdown hashes and asterisks", () => {
    const markdown = "### **Compare Apple and Microsoft** gross margins";
    const title = generateConversationTitle(markdown);
    expect(title).toBe("Compare Apple and Microsoft gross margins");
  });

  it("handles empty or whitespace-only messages gracefully", () => {
    expect(generateConversationTitle("   ")).toBe("New research session");
  });
});

describe("groupConversations", () => {
  const baseDate = new Date("2026-08-30T12:00:00Z");

  function createConv(id: string, dateStr: string): StoredConversation {
    return {
      id,
      title: `Conversation ${id}`,
      titleIsCustom: false,
      createdAt: dateStr,
      updatedAt: dateStr,
      messages: [],
    };
  }

  it("groups conversations into Today, Yesterday, Previous 7 days, and Older", () => {
    const convs: StoredConversation[] = [
      createConv("today1", "2026-08-30T09:00:00Z"),
      createConv("yesterday1", "2026-08-29T15:00:00Z"),
      createConv("prev7_1", "2026-08-26T10:00:00Z"),
      createConv("older1", "2026-08-10T10:00:00Z"),
    ];

    const groups = groupConversations(convs, baseDate);
    expect(groups).toHaveLength(4);
    expect(groups[0]?.label).toBe("Today");
    expect(groups[0]?.conversations).toHaveLength(1);
    expect(groups[1]?.label).toBe("Yesterday");
    expect(groups[1]?.conversations).toHaveLength(1);
    expect(groups[2]?.label).toBe("Previous 7 days");
    expect(groups[2]?.conversations).toHaveLength(1);
    expect(groups[3]?.label).toBe("Older");
    expect(groups[3]?.conversations).toHaveLength(1);
  });

  it("omits group headers when no conversations exist for that period", () => {
    const convs: StoredConversation[] = [
      createConv("today1", "2026-08-30T09:00:00Z"),
      createConv("today2", "2026-08-30T11:00:00Z"),
    ];

    const groups = groupConversations(convs, baseDate);
    expect(groups).toHaveLength(1);
    expect(groups[0]?.label).toBe("Today");
    expect(groups[0]?.conversations).toHaveLength(2);
  });
});

describe("loadStoredConversations & saveStoredConversations", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("saves and reloads conversations successfully", () => {
    const { store } = mockLocalStorage();
    const conv: StoredConversation = {
      id: "c1",
      title: "Apple 10-K analysis",
      titleIsCustom: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [
        {
          id: "m1",
          role: "user",
          question: "What was Apple revenue?",
          status: "complete",
        },
        {
          id: "m2",
          role: "assistant",
          question: "What was Apple revenue?",
          status: "complete",
          answer: "Apple revenue was $391B.",
          citations: [],
        },
      ],
    };

    const saved = saveStoredConversations([conv]);
    expect(saved).toBe(true);
    expect(store[CONVERSATIONS_STORAGE_KEY]).toBeTruthy();

    const loaded = loadStoredConversations();
    expect(loaded).toHaveLength(1);
    expect(loaded[0]?.title).toBe("Apple 10-K analysis");
    expect(loaded[0]?.messages).toHaveLength(2);
  });

  it("never persists in-flight (pending) turns", () => {
    mockLocalStorage();
    const conv: StoredConversation = {
      id: "c1",
      title: "Pending test",
      titleIsCustom: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [
        { id: "u1", role: "user", question: "Q", status: "complete" },
        { id: "a1", role: "assistant", question: "Q", status: "pending" },
      ],
    };

    saveStoredConversations([conv]);
    const loaded = loadStoredConversations();
    expect(loaded[0]?.messages.every((m) => m.status !== "pending")).toBe(true);
  });

  it("never persists secrets or auth headers", () => {
    const { store } = mockLocalStorage();
    const conv: StoredConversation = {
      id: "c1",
      title: "Secret test",
      titleIsCustom: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [
        {
          id: "m1",
          role: "assistant",
          status: "complete",
          answer: "Answer",
        },
      ],
    };

    saveStoredConversations([conv]);
    const raw = store[CONVERSATIONS_STORAGE_KEY] ?? "";
    expect(raw).not.toMatch(/api_key/i);
    expect(raw).not.toMatch(/authorization/i);
    expect(raw).not.toMatch(/LANGFUSE_SECRET/i);
    expect(raw).not.toMatch(/OPENAI_API_KEY/i);
  });

  it("handles malformed storage data gracefully without crashing", () => {
    const { store } = mockLocalStorage();
    store[CONVERSATIONS_STORAGE_KEY] = "invalid json {";
    expect(() => loadStoredConversations()).not.toThrow();
    expect(loadStoredConversations()).toEqual([]);
  });

  it("migrates legacy sentinel.chat.v1 storage cleanly", () => {
    const { store } = mockLocalStorage();
    store["sentinel.chat.v1"] = JSON.stringify({
      version: 1,
      savedAt: new Date().toISOString(),
      messages: [
        {
          id: "u1",
          role: "user",
          question: "Legacy question about Tesla",
          status: "complete",
        },
        {
          id: "a1",
          role: "assistant",
          question: "Legacy question about Tesla",
          status: "complete",
          answer: "Tesla produced 1.8M vehicles.",
        },
      ],
    });

    const loaded = loadStoredConversations();
    expect(loaded).toHaveLength(1);
    expect(loaded[0]?.title).toBe("Legacy question about Tesla");
    expect(loaded[0]?.messages).toHaveLength(2);
  });

  it("handles storage write exceptions (e.g. quota exceeded) safely", () => {
    vi.stubGlobal("localStorage", {
      getItem: vi.fn(() => null),
      setItem: vi.fn(() => {
        throw new DOMException("QuotaExceededError");
      }),
      removeItem: vi.fn(),
    });

    const conv: StoredConversation = {
      id: "c1",
      title: "Quota test",
      titleIsCustom: false,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      messages: [],
    };

    expect(saveStoredConversations([conv])).toBe(false);
  });
});
