"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  askAgentsQuery,
  askQuery,
  QUERY_TIMEOUT_MS,
  RequestCanceledError,
  userMessage,
} from "@/lib/api";
import { useBackendStatus } from "@/components/BackendGate";
import { useConversations } from "@/lib/useConversations";
import { useOptionalConversationsContext } from "@/lib/ConversationsContext";
import { isStorageAvailable } from "@/lib/persistence";
import { MessageBubble, type ChatMessage } from "./MessageBubble";
import { SentinelLogo } from "./SentinelLogo";

const MAX_QUESTION_LENGTH = 4000;
export const MAX_MESSAGES = 200;

interface ExampleCategory {
  title: string;
  tag: string;
  question: string;
}

const EXAMPLE_QUESTIONS: ExampleCategory[] = [
  {
    title: "Fiscal Net Sales",
    tag: "SEC 10-K",
    question: "What was Apple's total net sales in fiscal 2024?",
  },
  {
    title: "Multi-Entity Comparison",
    tag: "Comparative",
    question: "Compare Microsoft and Google cloud revenue growth year over year.",
  },
  {
    title: "Risk Factor Summary",
    tag: "Item 1A",
    question: "Summarize Tesla's main risk factors from its latest 10-K.",
  },
  {
    title: "Market Demand Sentiment",
    tag: "Market News",
    question: "What has been reported recently about NVIDIA's data center demand?",
  },
];

let nextMessageId = 0;
function newId(): string {
  nextMessageId += 1;
  return `m${nextMessageId}_${Date.now()}`;
}

export interface ChatWindowProps {
  conversationsHook?: ReturnType<typeof useConversations>;
}

/**
 * ChatWindow — Sentinel's Primary Financial Research Interface.
 *
 * Features:
 * - Browser-local multi-session chat integration.
 * - Independent scrollable transcript with generous bottom clearance.
 * - Fixed/sticky floating composer anchored at the bottom with safe-area insets.
 * - Keyboard shortcuts: Enter to submit, Shift+Enter for newline, Escape to cancel.
 * - Accessible live regions and clear-conversation inline confirmation.
 */
export function ChatWindow({ conversationsHook }: ChatWindowProps) {
  const optionalContext = useOptionalConversationsContext();
  const internalHook = useConversations();
  const conv = conversationsHook ?? optionalContext ?? internalHook;

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [forceAgents, setForceAgents] = useState(false);
  const [announcement, setAnnouncement] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);
  const [storageAvailable, setStorageAvailable] = useState(false);

  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);
  const activeIdRef = useRef<string | null>(conv.activeConversationId);

  const { isBackendDegraded } = useBackendStatus();

  // Storage check
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setStorageAvailable(isStorageAvailable());
  }, []);

  // Sync messages when active conversation changes
  useEffect(() => {
    activeIdRef.current = conv.activeConversationId;
    if (conv.activeConversation) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setMessages(conv.activeConversation.messages);
    } else {
      setMessages([]);
    }
  }, [conv.activeConversationId, conv.activeConversation]);

  // Derived: whether we have completed messages saved
  const hasSavedMessages =
    storageAvailable &&
    messages.some(
      (m) => m.status === "complete" || m.status === "error" || m.status === "canceled",
    );

  // Auto-scroll on new messages (smooth, non-dislocating scroll)
  useEffect(() => {
    if (messages.length > 0) {
      const timer = setTimeout(() => {
        bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 60);
      return () => clearTimeout(timer);
    }
  }, [messages.length]);

  // Request cancellation
  const cancelInFlight = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => cancelInFlight, [cancelInFlight]);

  // Clear conversation
  const handleClearRequest = () => setConfirmClear(true);
  const handleClearCancel = () => setConfirmClear(false);
  const handleClearConfirm = () => {
    cancelInFlight();
    setMessages([]);
    setConfirmClear(false);
    if (conv.activeConversationId) {
      conv.deleteConversation(conv.activeConversationId);
    }
    setAnnouncement("Conversation cleared.");
    inputRef.current?.focus();
  };

  // Submit question
  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = input.trim();
    if (question.length === 0 || loading) return;

    cancelInFlight();
    setInput("");
    setConfirmClear(false);

    const assistantId = newId();
    const userTurn: ChatMessage = {
      id: newId(),
      role: "user",
      question,
      status: "complete",
    };
    const pendingTurn: ChatMessage = {
      id: assistantId,
      role: "assistant",
      question,
      status: "pending",
      forcedAgents: forceAgents,
    };

    const nextMessages = [...messages, userTurn, pendingTurn];
    const clampedMessages =
      nextMessages.length > MAX_MESSAGES ? nextMessages.slice(-MAX_MESSAGES) : nextMessages;

    setMessages(clampedMessages);
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

      const updated = clampedMessages.map((message) =>
        message.id === assistantId
          ? {
              ...message,
              status: "complete" as const,
              answer: response.answer,
              citations: response.citations,
              agent_path: response.agent_path,
              trace_url: response.trace_url,
            }
          : message,
      );

      setMessages(updated);
      conv.saveActiveMessages(updated);
      setAnnouncement(
        `Answer ready with ${response.citations.length} source${response.citations.length === 1 ? "" : "s"}.`,
      );
    } catch (error) {
      if (error instanceof RequestCanceledError || controller.signal.aborted) {
        const updated = clampedMessages.map((message) =>
          message.id === assistantId ? { ...message, status: "canceled" as const } : message,
        );
        setMessages(updated);
        conv.saveActiveMessages(updated);
        setAnnouncement("Request canceled.");
      } else {
        const friendly = userMessage(error);
        const code =
          error instanceof Error && "code" in error
            ? String((error as Record<string, unknown>).code)
            : undefined;
        const updated = clampedMessages.map((message) =>
          message.id === assistantId
            ? {
                ...message,
                status: "error" as const,
                errorMessage: friendly,
                errorCode: code,
              }
            : message,
        );
        setMessages(updated);
        conv.saveActiveMessages(updated);
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
    <div className="relative flex flex-1 flex-col justify-between">
      {/* Screen-reader live region */}
      <div aria-live="polite" role="status" className="sr-only">
        {announcement}
      </div>

      {/* Storage failure notice */}
      {conv.storageError && (
        <div
          role="status"
          className="mb-4 rounded-xl border border-warning/40 bg-warning-soft px-4 py-3 text-xs text-warning-ink"
        >
          {conv.storageError}
        </div>
      )}

      {/* Session degradation banner (backend alive but unconfigured) */}
      {isBackendDegraded && (
        <div
          role="status"
          className="mb-4 rounded-xl border border-warning/30 bg-warning-soft px-4 py-3 text-sm text-warning-ink"
        >
          <p className="m-0 font-medium">
            Research engine running in unconfigured mode.{" "}
            <span className="font-normal opacity-85">
              Live retrieval and answers may be unavailable until credentials are provided.
            </span>
          </p>
        </div>
      )}

      {/* Main message stream container */}
      <div className="flex-1 pb-10">
        {!hasMessages ? (
          <section
            aria-label="Getting started"
            className="rounded-2xl border border-line bg-surface px-6 py-10 text-center shadow-card sm:px-12 sm:py-14"
          >
            {/* Sentinel brand mark */}
            <div className="mx-auto mb-5 flex justify-center">
              <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-accent-soft border border-accent/20">
                <SentinelLogo variant="symbol" size={32} />
              </div>
            </div>

            <h2 className="font-display mt-0 mb-2 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Ask a research question
            </h2>
            <p className="mx-auto mb-8 max-w-lg text-sm leading-relaxed text-ink-soft">
              Query public SEC filings, earnings transcripts, and market news. Sentinel extracts
              verified facts and synthesizes cited answers with traceable agent reasoning.
            </p>

            {/* Categorized example questions */}
            <ul className="mx-auto m-0 grid list-none gap-3 p-0 text-left sm:max-w-2xl sm:grid-cols-2">
              {EXAMPLE_QUESTIONS.map((example) => (
                <li key={example.question}>
                  <button
                    type="button"
                    onClick={() => {
                      setInput(example.question);
                      inputRef.current?.focus();
                    }}
                    className="transition-enabled group flex h-full w-full flex-col justify-between rounded-xl border border-line bg-surface-muted p-4 text-left hover:border-accent/50 hover:bg-accent-soft focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    <span className="mb-2 inline-flex self-start rounded-full bg-surface px-2 py-0.5 font-mono text-[10px] font-medium tracking-wide uppercase text-accent border border-line">
                      {example.tag}
                    </span>
                    <span className="text-sm font-medium leading-snug text-ink transition-enabled group-hover:text-accent-strong">
                      {example.question}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ) : (
          <section aria-label="Conversation" className="flex flex-col gap-5">
            {messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))}
            <div ref={bottomRef} className="h-4" />
          </section>
        )}
      </div>

      {/* Fixed / Sticky Question Composer */}
      <div className="sticky bottom-0 z-30 w-full pt-2 pb-[calc(1rem+env(safe-area-inset-bottom,0px))] bg-gradient-to-t from-background via-background/95 to-transparent">
        <form onSubmit={submit} className="space-y-2">
          <div className="rounded-2xl border border-line bg-surface/98 backdrop-blur-xs p-3 shadow-float transition-enabled focus-within:border-accent/60">
            <label
              htmlFor="question-input"
              className="mb-1.5 block px-1 text-xs font-semibold uppercase tracking-wider text-ink-faint"
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

            <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-0.5 pt-1.5">
              <div className="flex items-center gap-3 flex-wrap">
                <label className="flex cursor-pointer items-center gap-1.5 text-xs text-ink-soft transition-enabled hover:text-ink">
                  <input
                    type="checkbox"
                    checked={forceAgents}
                    onChange={(event) => setForceAgents(event.target.checked)}
                    className="h-3.5 w-3.5 rounded border-line text-accent accent-[var(--accent)] focus:ring-accent"
                  />
                  <span>Force multi-agent analysis</span>
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
                    className="transition-enabled rounded-lg border border-danger/30 bg-danger-soft px-4 py-2 text-sm font-medium text-danger hover:bg-danger hover:text-white"
                  >
                    Cancel
                  </button>
                ) : (
                  <button
                    type="submit"
                    disabled={!canSubmit}
                    className="transition-enabled rounded-xl border border-accent/40 bg-accent-soft px-4 py-2 text-xs font-semibold text-accent-strong enabled:hover:bg-accent enabled:hover:text-black enabled:hover:border-accent disabled:cursor-not-allowed disabled:opacity-40 shadow-sm"
                  >
                    Ask Sentinel
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Persistence status and Clear action row */}
          {hasMessages && (
            <div className="flex flex-wrap items-center justify-between gap-2 px-1 text-xs">
              {storageAvailable && hasSavedMessages ? (
                <p
                  className="m-0 flex items-center gap-1.5 text-[11px] text-ink-faint"
                  aria-live="polite"
                >
                  <span className="h-1.5 w-1.5 rounded-full bg-accent opacity-75" aria-hidden />
                  <span className="sr-only">Status:</span>
                  Saved on this device
                </p>
              ) : (
                <span />
              )}

              {confirmClear ? (
                <span className="flex items-center gap-2">
                  <span className="text-ink-soft">Clear conversation?</span>
                  <button
                    type="button"
                    onClick={handleClearConfirm}
                    className="transition-enabled rounded border border-danger/40 bg-danger-soft px-2.5 py-0.5 text-xs font-semibold text-danger hover:bg-danger hover:text-white"
                    aria-label="Confirm clear conversation"
                  >
                    Clear
                  </button>
                  <button
                    type="button"
                    onClick={handleClearCancel}
                    className="transition-enabled rounded border border-line bg-surface px-2.5 py-0.5 text-xs font-medium text-ink-soft hover:text-ink"
                    aria-label="Cancel clear conversation"
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={handleClearRequest}
                  className="transition-enabled text-[11px] text-ink-faint hover:text-ink-soft hover:underline"
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
    </div>
  );
}
