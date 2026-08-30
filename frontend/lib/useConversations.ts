"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ChatMessage } from "@/components/MessageBubble";
import { isStorageAvailable } from "./persistence";

export const CONVERSATIONS_STORAGE_KEY = "sentinel:conversations";
export const CONVERSATIONS_SCHEMA_VERSION = 1;
export const MAX_CONVERSATIONS = 50;
export const MAX_MESSAGES_PER_CONVERSATION = 100;

export interface StoredConversation {
  id: string;
  title: string;
  titleIsCustom: boolean;
  createdAt: string; // ISO timestamp
  updatedAt: string; // ISO timestamp
  messages: ChatMessage[];
}

interface StoredEnvelope {
  version: number;
  updatedAt: string;
  conversations: StoredConversation[];
}

export interface ConversationGroup {
  label: "Today" | "Yesterday" | "Previous 7 days" | "Older";
  conversations: StoredConversation[];
}

/**
 * Generates a concise title from the first user message:
 * 1. Strips leading/trailing whitespace and markdown formatting.
 * 2. Truncates at a word boundary to ~48 characters max with ellipsis.
 * 3. Preserves question mark if the original input asked a question.
 */
export function generateConversationTitle(firstUserMessage: string): string {
  let clean = firstUserMessage
    .trim()
    .replace(/^#+\s+/g, "")
    .replace(/[*_~`#[\]()]/g, "")
    .replace(/\s+/g, " ");

  if (!clean) return "New research session";

  const endsWithQuestion = clean.endsWith("?");
  if (clean.length <= 48) {
    return clean;
  }

  let truncated = clean.slice(0, 48);
  const lastSpace = truncated.lastIndexOf(" ");
  if (lastSpace > 20) {
    truncated = truncated.slice(0, lastSpace);
  }
  truncated = truncated.trimEnd();

  return endsWithQuestion ? `${truncated}…?` : `${truncated}…`;
}

/**
 * Groups conversations chronologically by their updatedAt date.
 */
export function groupConversations(
  conversations: StoredConversation[],
  now = new Date(),
): ConversationGroup[] {
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const yesterdayStart = todayStart - 24 * 60 * 60 * 1000;
  const sevenDaysStart = todayStart - 7 * 24 * 60 * 60 * 1000;

  const today: StoredConversation[] = [];
  const yesterday: StoredConversation[] = [];
  const prev7: StoredConversation[] = [];
  const older: StoredConversation[] = [];

  for (const conv of conversations) {
    const time = new Date(conv.updatedAt || conv.createdAt).getTime();
    if (isNaN(time)) {
      older.push(conv);
    } else if (time >= todayStart) {
      today.push(conv);
    } else if (time >= yesterdayStart) {
      yesterday.push(conv);
    } else if (time >= sevenDaysStart) {
      prev7.push(conv);
    } else {
      older.push(conv);
    }
  }

  const groups: ConversationGroup[] = [];
  if (today.length > 0) groups.push({ label: "Today", conversations: today });
  if (yesterday.length > 0) groups.push({ label: "Yesterday", conversations: yesterday });
  if (prev7.length > 0) groups.push({ label: "Previous 7 days", conversations: prev7 });
  if (older.length > 0) groups.push({ label: "Older", conversations: older });

  return groups;
}

/**
 * Loads and validates conversations from localStorage, migrating legacy sessions if present.
 */
export function loadStoredConversations(): StoredConversation[] {
  if (typeof window === "undefined" || !isStorageAvailable()) return [];

  try {
    const raw = localStorage.getItem(CONVERSATIONS_STORAGE_KEY);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (isValidEnvelope(parsed)) {
        return (parsed as StoredEnvelope).conversations
          .filter(isValidConversation)
          .sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());
      }
    }

    // Graceful migration from legacy single-session key 'sentinel.chat.v1'
    const legacyRaw = localStorage.getItem("sentinel.chat.v1");
    if (legacyRaw) {
      try {
        const legacyParsed = JSON.parse(legacyRaw) as { messages?: ChatMessage[] };
        if (Array.isArray(legacyParsed.messages) && legacyParsed.messages.length > 0) {
          const firstUser = legacyParsed.messages.find((m) => m.role === "user");
          const title = firstUser?.question
            ? generateConversationTitle(firstUser.question)
            : "Previous research session";
          const now = new Date().toISOString();
          const migrated: StoredConversation = {
            id: generateId(),
            title,
            titleIsCustom: false,
            createdAt: now,
            updatedAt: now,
            messages: sanitizeMessages(legacyParsed.messages),
          };
          saveStoredConversations([migrated]);
          return [migrated];
        }
      } catch {
        // Ignore legacy parse errors
      }
    }

    return [];
  } catch {
    return [];
  }
}

/**
 * Saves conversations array to localStorage safely.
 */
export function saveStoredConversations(conversations: StoredConversation[]): boolean {
  if (typeof window === "undefined") return false;
  try {
    const trimmed = conversations.slice(0, MAX_CONVERSATIONS).map((c) => ({
      ...c,
      messages: sanitizeMessages(c.messages).slice(-MAX_MESSAGES_PER_CONVERSATION),
    }));

    const envelope: StoredEnvelope = {
      version: CONVERSATIONS_SCHEMA_VERSION,
      updatedAt: new Date().toISOString(),
      conversations: trimmed,
    };

    localStorage.setItem(CONVERSATIONS_STORAGE_KEY, JSON.stringify(envelope));
    return true;
  } catch {
    return false;
  }
}

function sanitizeMessages(messages: ChatMessage[]): ChatMessage[] {
  return messages
    .filter((m) => m.status !== "pending")
    .map((m) => ({
      id: m.id,
      role: m.role,
      ...(m.question !== undefined ? { question: m.question } : {}),
      status: (m.status === "complete" || m.status === "canceled" || m.status === "error"
        ? m.status
        : "error") as ChatMessage["status"],
      ...(m.answer !== undefined ? { answer: m.answer } : {}),
      ...(m.citations !== undefined ? { citations: m.citations } : {}),
      ...(m.agent_path !== undefined ? { agent_path: m.agent_path } : {}),
      ...(m.trace_url !== undefined ? { trace_url: m.trace_url } : {}),
      ...(m.forcedAgents !== undefined ? { forcedAgents: m.forcedAgents } : {}),
      ...(m.errorCode !== undefined ? { errorCode: m.errorCode } : {}),
      ...(m.errorMessage !== undefined ? { errorMessage: m.errorMessage } : {}),
    }));
}

function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `c_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
}

function isValidEnvelope(val: unknown): val is StoredEnvelope {
  return (
    val !== null &&
    typeof val === "object" &&
    "version" in val &&
    "conversations" in val &&
    Array.isArray((val as Record<string, unknown>).conversations)
  );
}

function isValidConversation(val: unknown): val is StoredConversation {
  if (val === null || typeof val !== "object") return false;
  const c = val as Record<string, unknown>;
  return (
    typeof c.id === "string" &&
    typeof c.title === "string" &&
    typeof c.titleIsCustom === "boolean" &&
    typeof c.createdAt === "string" &&
    typeof c.updatedAt === "string" &&
    Array.isArray(c.messages)
  );
}

// ---------------------------------------------------------------------------
// Hook: useConversations
// ---------------------------------------------------------------------------

export function useConversations() {
  const [conversations, setConversations] = useState<StoredConversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [storageError, setStorageError] = useState<string | null>(null);
  const [hasHydrated, setHasHydrated] = useState(false);

  // Load from localStorage on hydration
  useEffect(() => {
    const loaded = loadStoredConversations();
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setConversations(loaded);
    if (loaded.length > 0 && loaded[0]) {
      setActiveConversationId(loaded[0].id);
    }
    setHasHydrated(true);
  }, []);

  const activeConversation = useMemo(() => {
    if (!activeConversationId) return null;
    return conversations.find((c) => c.id === activeConversationId) ?? null;
  }, [conversations, activeConversationId]);

  const groupedConversations = useMemo(() => {
    return groupConversations(conversations);
  }, [conversations]);

  /**
   * Start a new chat session.
   * Does NOT persist an empty conversation to storage immediately (avoids phantom empty chats).
   */
  const startNewChat = useCallback(() => {
    setActiveConversationId(null);
    setStorageError(null);
  }, []);

  /**
   * Switch to an existing conversation by ID.
   */
  const selectConversation = useCallback((id: string) => {
    setActiveConversationId(id);
    setStorageError(null);
  }, []);

  /**
   * Save messages for the active conversation or create a new conversation if on new chat.
   */
  const saveActiveMessages = useCallback(
    (messages: ChatMessage[]) => {
      const completed = sanitizeMessages(messages);
      if (completed.length === 0) return;

      const firstUserMessage = completed.find((m) => m.role === "user")?.question ?? "";
      const now = new Date().toISOString();

      setConversations((prev) => {
        let next: StoredConversation[];
        if (activeConversationId) {
          // Update existing conversation
          next = prev.map((c) => {
            if (c.id === activeConversationId) {
              return {
                ...c,
                updatedAt: now,
                messages: completed,
              };
            }
            return c;
          });
        } else {
          // Create new conversation
          const newId = generateId();
          const newTitle = generateConversationTitle(firstUserMessage);
          const newConv: StoredConversation = {
            id: newId,
            title: newTitle,
            titleIsCustom: false,
            createdAt: now,
            updatedAt: now,
            messages: completed,
          };
          next = [newConv, ...prev];
          setActiveConversationId(newId);
        }

        // Sort newest first
        next.sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

        const success = saveStoredConversations(next);
        if (!success) {
          setStorageError(
            "Couldn't save this chat — your browser's local storage may be full or disabled.",
          );
        } else {
          setStorageError(null);
        }

        return next;
      });
    },
    [activeConversationId],
  );

  /**
   * Renames a conversation and marks titleIsCustom = true.
   */
  const renameConversation = useCallback((id: string, newTitle: string) => {
    const trimmed = newTitle.trim();
    if (!trimmed) return;

    setConversations((prev) => {
      const next = prev.map((c) => {
        if (c.id === id) {
          return {
            ...c,
            title: trimmed,
            titleIsCustom: true,
            updatedAt: new Date().toISOString(),
          };
        }
        return c;
      });
      saveStoredConversations(next);
      return next;
    });
  }, []);

  /**
   * Deletes a conversation by ID.
   */
  const deleteConversation = useCallback(
    (id: string) => {
      setConversations((prev) => {
        const next = prev.filter((c) => c.id !== id);
        saveStoredConversations(next);
        return next;
      });

      if (activeConversationId === id) {
        setActiveConversationId(null);
      }
    },
    [activeConversationId],
  );

  /**
   * Clears all conversations.
   */
  const clearAllConversations = useCallback(() => {
    setConversations([]);
    setActiveConversationId(null);
    if (typeof window !== "undefined") {
      try {
        localStorage.removeItem(CONVERSATIONS_STORAGE_KEY);
      } catch {
        // Ignore
      }
    }
  }, []);

  return {
    conversations,
    activeConversationId,
    activeConversation,
    groupedConversations,
    hasHydrated,
    storageError,
    startNewChat,
    selectConversation,
    saveActiveMessages,
    renameConversation,
    deleteConversation,
    clearAllConversations,
  };
}
