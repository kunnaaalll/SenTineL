"use client";

import { FormEvent, KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  askAgentsQuery,
  askQuery,
  QUERY_TIMEOUT_MS,
  RequestCanceledError,
  userMessage,
} from "@/lib/api";
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
 * evidence. History lives in component state only (v1 scope — no
 * persistence, no accounts). In-flight requests can be canceled by button or
 * Escape; submitting a new question cancels the previous request first.
 */
export function ChatWindow() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [forceAgents, setForceAgents] = useState(false);
  const [announcement, setAnnouncement] = useState("");

  const abortRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [messages]);

  const cancelInFlight = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  useEffect(() => cancelInFlight, [cancelInFlight]);

  const submit = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = input.trim();
    if (question.length === 0 || loading) return;

    cancelInFlight();
    setInput("");

    const assistantId = newId();
    setMessages((previous) => [
      ...previous,
      { id: newId(), role: "user", question, status: "complete" },
      {
        id: assistantId,
        role: "assistant",
        question,
        status: "pending",
        forcedAgents: forceAgents,
      },
    ]);
    setLoading(true);
    setAnnouncement("Researching your question. This can take up to a minute.");

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const history = messages
        .filter((m) => m.status === "complete" && (m.role === "user" || m.answer))
        .map((m) => ({
          role: m.role as "user" | "assistant",
          content: m.role === "user" ? m.question : (m.answer ?? ""),
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
        const code = error instanceof Error && "code" in error ? String(error.code) : undefined;
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

  return (
    <div className="flex flex-col gap-5">
      {/* Screen-reader announcements for loading / success / failure states */}
      <div aria-live="polite" role="status" className="sr-only">
        {announcement}
      </div>

      {messages.length === 0 ? (
        <section
          aria-label="Getting started"
          className="rounded-xl border border-line bg-surface px-5 py-8 text-center shadow-card sm:px-10 sm:py-12"
        >
          <h2 className="mt-0 text-xl font-semibold tracking-tight text-ink">
            Ask a research question
          </h2>
          <p className="mx-auto mb-6 max-w-md text-sm leading-relaxed text-ink-soft">
            Sentinel retrieves SEC filings and market news, extracts the facts, and answers with
            numbered citations you can expand and verify.
          </p>
          <ul className="mx-auto m-0 grid list-none gap-2 p-0 text-left sm:max-w-lg sm:grid-cols-2">
            {EXAMPLE_QUESTIONS.map((example) => (
              <li key={example}>
                <button
                  type="button"
                  onClick={() => {
                    setInput(example);
                    inputRef.current?.focus();
                  }}
                  className="h-full w-full rounded-lg border border-line bg-surface-muted px-3.5 py-3 text-left text-sm leading-snug text-ink-soft transition-enabled hover:border-accent hover:bg-accent-soft hover:text-ink"
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
            disabled={false}
            className="block w-full resize-y rounded-lg border border-transparent bg-transparent px-2 py-1.5 text-sm leading-relaxed text-ink placeholder:text-ink-faint focus:border-line focus:outline-none"
          />
          <div className="flex flex-wrap items-center justify-between gap-2 px-1 pb-0.5 pt-1">
            <div className="flex items-center gap-3">
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
                  className="rounded-lg border border-line-strong bg-surface px-4 py-2 text-sm font-medium text-ink-soft transition-enabled hover:border-danger hover:text-danger"
                >
                  Cancel
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!canSubmit}
                  className="rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent transition-enabled enabled:hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50"
                >
                  Ask Sentinel
                </button>
              )}
            </div>
          </div>
        </div>
        <p aria-hidden className="px-2 text-center text-[11px] text-ink-faint">
          Answers cite retrieved filings and news only — always verify against the linked sources.
        </p>
      </form>
    </div>
  );
}
