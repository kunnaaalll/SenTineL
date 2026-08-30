"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  askAgentsQuery,
  askQuery,
  QUERY_TIMEOUT_MS,
  RequestCanceledError,
  userMessage,
} from "@/lib/api";
import {
  clearPersistedMessages,
  isStorageAvailable,
  loadPersistedMessages,
  saveMessages,
  MAX_MESSAGES,
} from "@/lib/persistence";
import { useBackendStatus } from "@/components/BackendGate";
import { MessageBubble, type ChatMessage } from "./MessageBubble";

const MAX_QUESTION_LENGTH = 4000;

const EXAMPLE_QUESTIONS = [
  "What was Apple's total net sales in fiscal 2024?",
  "Compare Microsoft and Google cloud revenue growth year over year.",
  "Summarize Tesla's main risk factors from its latest 10-K.",
  "What has been reported recently about NVIDIA's data center demand?",
];

let nextMessageId = 0;
function newId(): string {
  nextMessageId += 1;
  return `m${nextMessageId}`;
}

/**
 * The main research interface: question input, transcript, and per-answer
 * evidence. Conversations are persisted to localStorage under sentinel.chat.v1
 * and restored after browser refresh. History is kept in-memory when storage
 * is unavailable, so the chat remains fully functional either way.
 *
 * Privacy: only completed messages are stored — never in-flight state, API
 * keys, auth headers, Langfuse secret keys, or raw backend error bodies.
 */
export function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [forceAgents, setForceAgents] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  // Persistence UI state
  const [storageAvailable, setStorageAvailable] = useState(false);
  // Clear conversation confirmation UX
  const [confirmClear, setConfirmClear] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const hasMountedRef = useRef(false);

  const { isBackendDegraded } = useBackendStatus();

  // Derived: whether we have completed messages that are saved on device
  const hasSavedMessages =
    storageAvailable &&
    messages.some(
      (m) => m.status === "complete" || m.status === "error" || m.status === "canceled",
    );

  // ---------------------------------------------------------------------------
  // Load persisted messages on client hydration (SSR-safe)
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (hasMountedRef.current) return;
    hasMountedRef.current = true;

    const available = isStorageAvailable();
    setStorageAvailable(available);

    if (available) {
      const saved = loadPersistedMessages();
      if (saved.length > 0) {
        // Convert persisted messages back to ChatMessage format
        const restored: ChatMessage[] = saved.map((m) => ({
          id: m.id,
          role: m.role,
          question: m.question ?? "",
          status: m.status,
          answer: m.answer,
          citations: m.citations,
          agent_path: m.agent_path,
          trace_url: m.trace_url,
          forcedAgents: m.forcedAgents,
          errorCode: m.errorCode,
          errorMessage: m.errorMessage,
        }));
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setMessages(restored);
      }
    }
  }, []);

  // ---------------------------------------------------------------------------
  // Save on every messages change
  // ---------------------------------------------------------------------------
  useEffect(() => {
    if (!hasMountedRef.current || !storageAvailable) return;
    const completed = messages.filter((m) => m.status !== "pending");
    if (completed.length === 0) return;

    saveMessages(completed);
  }, [messages, storageAvailable]);

  // ---------------------------------------------------------------------------
  // Scroll to bottom
  // ---------------------------------------------------------------------------
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  // ---------------------------------------------------------------------------
  // Request cancellation
  // ---------------------------------------------------------------------------
  const cancelInFlight = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => cancelInFlight, [cancelInFlight]);

  // ---------------------------------------------------------------------------
  // Clear conversation
  // ---------------------------------------------------------------------------
  const handleClearRequest = () => setConfirmClear(true);
  const handleClearCancel = () => setConfirmClear(false);
  const handleClearConfirm = () => {
    cancelInFlight();
    setMessages([]);
    setConfirmClear(false);
    clearPersistedMessages();
    setAnnouncement("Conversation cleared.");
    inputRef.current?.focus();
  };

  // ---------------------------------------------------------------------------
  // Submit question
  // ---------------------------------------------------------------------------
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = input.trim();
    if (question.length === 0 || loading) return;

    cancelInFlight();
    setInput("");
    setConfirmClear(false);

    const assistantId = newId();
    setMessages((previous) => {
      const next = [
        ...previous,
        { id: newId(), role: "user" as const, question, status: "complete" as const },
        {
          id: assistantId,
          role: "assistant" as const,
          question,
          status: "pending" as const,
          forcedAgents: forceAgents,
        },
      ];
      // Trim if over max (keep newest)
      return next.length > MAX_MESSAGES ? next.slice(-MAX_MESSAGES) : next;
    });
    setLoading(true);
    setAnnouncement("Researching your question. This can take up to a minute.");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const history = messages
        .filter((m) => m.status === "complete" && (m.role === "user" || m.answer))
        .map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.role === "user" ? (m.question ?? "") : (m.answer ?? ""),
        }));
      const request = {
        question,
        ...(history.length > 0 ? { history } : {}),
      };
      const response = forceAgents
        ? await askAgentsQuery(request, { signal: controller.signal, timeoutMs: QUERY_TIMEOUT_MS })
        : await askQuery(request, { signal: controller.signal, timeoutMs: QUERY_TIMEOUT_MS });

      setMessages((previous) =>
        previous.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                status: "complete",
                answer: response.answer,
                citations: response.citations,
                agent_path: response.agent_path,
                trace_url: response.trace_url,
              }
            : message,
        ),
      );
      setAnnouncement(
        `Answer ready with ${response.citations.length} source${response.citations.length === 1 ? "" : "s"}.`,
      );
    } catch (error) {
      if (error instanceof RequestCanceledError || controller.signal.aborted) {
        setMessages((previous) =>
          previous.map((message) =>
            message.id === assistantId ? { ...message, status: "canceled" } : message,
          ),
        );
        setAnnouncement("Request canceled.");
      } else {
        const friendly = userMessage(error);
        const code =
          error instanceof Error && "code" in error
            ? String((error as Record<string, unknown>).code)
            : undefined;
        setMessages((previous) =>
          previous.map((message) =>
            message.id === assistantId
              ? { ...message, status: "error", errorMessage: friendly, errorCode: code }
              : message,
          ),
        );
        setAnnouncement(`The request failed. ${friendly}`);
      }
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
      setLoading(false);
      inputRef.current?.focus();
    }
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void submit();
    }
    if (event.key === "Escape" && loading) {
      event.preventDefault();
      cancelInFlight();
    }
  };

  const remaining = MAX_QUESTION_LENGTH - input.length;
  const canSubmit = input.trim().length > 0 && !loading;
  const hasMessages = messages.length > 0;

  return (
    <div className="flex flex-col gap-5">
      {/* Screen-reader live region */}
      <div aria-live="polite" role="status" className="sr-only">
        {announcement}
      </div>

      {/* Session degradation banner (backend alive but not configured) */}
      {isBackendDegraded && (
        <div
          role="status"
          className="rounded-lg border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning-ink"
        >
          <p className="m-0 font-medium">
            Research engine is not fully configured.{" "}
            <span className="font-normal opacity-80">
              Answers may be limited until provider credentials are set on the backend.
            </span>
          </p>
        </div>
      )}

      {/* Getting-started panel or conversation */}
      {!hasMessages ? (
        <section
          aria-label="Getting started"
          className="rounded-2xl border border-line bg-surface px-5 py-10 text-center shadow-card sm:px-10 sm:py-14"
        >
          {/* Mini orb icon */}
          <div
            aria-hidden
            className="mx-auto mb-5 h-10 w-10 rounded-full"
            style={{
              background:
                "radial-gradient(ellipse at 35% 35%, rgba(221,184,64,0.7) 0%, rgba(200,160,48,0.3) 60%, transparent 100%)",
              boxShadow: "0 0 20px rgba(200,160,48,0.2)",
            }}
          />
          <h2 className="font-display mt-0 mb-2 text-xl font-semibold tracking-tight text-ink">
            Ask a research question
          </h2>
          <p className="mx-auto mb-7 max-w-md text-sm leading-relaxed text-ink-soft">
            Sentinel retrieves SEC filings and market news, extracts the facts, and answers with
            numbered citations you can expand and verify.
          </p>
          <ul className="mx-auto m-0 grid list-none gap-2.5 p-0 text-left sm:max-w-lg sm:grid-cols-2">
            {EXAMPLE_QUESTIONS.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => {
                    setInput(example);
                    inputRef.current?.focus();
                  }}
                  className="transition-enabled h-full w-full rounded-xl border border-line bg-surface-muted px-4 py-3.5 text-left text-sm leading-snug text-ink-soft hover:border-accent/50 hover:bg-accent-soft hover:text-ink"
                  style={{ boxShadow: "none" }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.boxShadow =
                      "0 0 16px rgba(200,160,48,0.08)";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.boxShadow = "none";
                  }}
                >
                  {example}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : (
        <section aria-label="Conversation" className="flex flex-col gap-4">
          {messages.map((message) => (
            <MessageBubble key={message.id} message={message} />
          ))}
          <div ref={bottomRef} />
        </section>
      )}

      {/* Input form */}
      <form onSubmit={submit} className="sticky bottom-4 space-y-2">
        <div className="rounded-2xl border border-line bg-surface p-2.5 shadow-card">
          <label
            htmlFor="question-input"
            className="mb-1.5 block px-1 text-xs font-medium text-ink-faint"
          >
            Your question
          </label>
          <textarea
            id="question-input"
            ref={inputRef}
            value={input}
            onChange={(event) => setInput(event.target.value.slice(0, MAX_QUESTION_LENGTH))}
            onKeyDown={onKeyDown}
            rows={2}
            maxLength={MAX_QUESTION_LENGTH}
            placeholder='e.g. "Compare Apple and Microsoft gross margin for fiscal 2024"'
            aria-describedby="question-hint"
            className="block w-full resize-y rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-sm leading-relaxed text-ink placeholder:text-ink-faint focus:border-line focus:outline-none"
          />
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-0.5 pt-1">
            <div className="flex items-center gap-3 flex-wrap">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-faint transition-enabled hover:text-ink-soft">
                <input
                  type="checkbox"
                  checked={forceAgents}
                  onChange={(event) => setForceAgents(event.target.checked)}
                  className="h-3.5 w-3.5 accent-[var(--accent)]"
                />
                Force multi-agent analysis
              </label>
              <span id="question-hint" className="hidden text-xs text-ink-faint sm:inline">
                Enter to send · Shift+Enter for a new line{loading ? " · Escape cancels" : ""}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {remaining < 500 && (
                <span aria-hidden className="font-mono text-xs text-ink-faint">
                  {remaining}
                </span>
              )}
              {loading ? (
                <button
                  type="button"
                  onClick={cancelInFlight}
                  className="transition-enabled rounded-lg border border-line-strong bg-surface px-4 py-2 text-sm font-medium text-ink-soft hover:border-danger hover:text-danger"
                >
                  Cancel
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="transition-enabled rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent enabled:hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
                  style={canSubmit ? { boxShadow: "0 0 12px rgba(200,160,48,0.25)" } : undefined}
                >
                  Ask Sentinel
                </button>
              )}
            </div>
          </div>
        </div>

        {/* Bottom row: persistence indicator + clear conversation */}
        {hasMessages && (
          <div className="flex flex-wrap items-center justify-between gap-2 px-1">
            {/* Saved indicator */}
            {storageAvailable && hasSavedMessages ? (
              <p className="m-0 text-[11px] text-ink-faint" aria-live="polite">
                <span aria-hidden>◆</span> <span className="sr-only">Status:</span>
                Saved on this device
              </p>
            ) : (
              <span />
            )}

            {/* Clear conversation */}
            {confirmClear ? (
              <span className="flex items-center gap-2 text-xs">
                <span className="text-ink-soft">Clear conversation?</span>
                <button
                  type="button"
                  onClick={handleClearConfirm}
                  className="transition-enabled rounded border border-danger/40 bg-danger-soft px-2.5 py-1 font-medium text-danger hover:border-danger"
                  aria-label="Confirm clear conversation"
                >
                  Clear
                </button>
                <button
                  type="button"
                  onClick={handleClearCancel}
                  className="transition-enabled rounded border border-line px-2.5 py-1 font-medium text-ink-soft hover:text-ink"
                  aria-label="Cancel clear conversation"
                >
                  Cancel
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={handleClearRequest}
                className="transition-enabled text-[11px] text-ink-faint hover:text-ink-soft"
                aria-label="Clear conversation history"
              >
                Clear conversation
              </button>
            )}
          </div>
        )}

        <p aria-hidden className="px-2 text-center text-[11px] text-ink-faint">
          Answers cite retrieved filings and news only — always verify against the linked sources.
        </p>
      </form>
    </div>
  );
}
