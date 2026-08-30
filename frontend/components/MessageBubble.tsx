"use client";

import { useRef, useState } from "react";
import { Citation } from "@/lib/api";
import { AnswerMarkdown, isInsufficientEvidence, splitLimitations } from "./AnswerMarkdown";
import { CitationCard } from "./CitationCard";
import { AgentTraceViewer } from "./AgentTraceViewer";

/**
 * One conversation turn. Assistant messages carry their own status so the
 * transcript shows exactly what happened: pending skeleton, grounded answer
 * with sources and pipeline trace, explicit refusal, backend error, or a
 * user-initiated cancellation.
 */
export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  /** The question this turn answers (assistant messages only). */
  question?: string;
  status: "pending" | "complete" | "error" | "canceled";
  answer?: string;
  citations?: Citation[];
  agent_path?: string[];
  trace_url?: string | null;
  /** True when submitted through /agents/query (forced multi-agent path). */
  forcedAgents?: boolean;
  errorCode?: string;
  errorMessage?: string;
}

export function MessageBubble({ message }: { message: ChatMessage }) {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);
  const citationHeadingRefs = useRef<(HTMLSpanElement | null)[]>([]);

  if (message.role === "user") {
    return (
      <article aria-label="Your question" className="flex justify-end">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-md bg-surface-raised border border-line-strong px-4 py-2.5 text-sm leading-relaxed text-ink sm:max-w-[75%]">
          {message.question}
        </div>
      </article>
    );
  }

  // ------------------------------------------------------------------ pending
  if (message.status === "pending") {
    return (
      <article aria-label="Sentinel is researching your question">
        <div
          className="rounded-2xl rounded-bl-md border border-accent/20 bg-surface px-4 py-3.5 shadow-card"
          style={{ borderLeftWidth: "2px", borderLeftColor: "var(--accent)" }}
        >
          <p role="status" className="m-0 flex items-center gap-2 text-sm text-ink-soft">
            <span
              aria-hidden
              className="animate-pulse-dot inline-block h-2 w-2 rounded-full bg-accent"
            />
            Searching SEC filings and market news…
          </p>
          <div aria-hidden className="mt-3 space-y-2.5">
            <div className="animate-shimmer h-3 w-11/12 rounded-full" />
            <div
              className="animate-shimmer h-3 w-9/12 rounded-full"
              style={{ animationDelay: "0.2s" }}
            />
            <div
              className="animate-shimmer h-3 w-10/12 rounded-full"
              style={{ animationDelay: "0.4s" }}
            />
          </div>
        </div>
      </article>
    );
  }

  // ------------------------------------------------------------------ canceled
  if (message.status === "canceled") {
    return (
      <article aria-label="Request canceled">
        <div className="rounded-2xl rounded-bl-md border border-dashed border-line-strong bg-surface-muted px-4 py-3 text-sm text-ink-soft">
          <p className="m-0 font-medium">Request canceled.</p>
          <p className="mb-0 mt-1 text-xs text-ink-faint">
            No answer was produced for “{truncate(message.question ?? "")}”. Ask again whenever
            you&apos;re ready.
          </p>
        </div>
      </article>
    );
  }

  // --------------------------------------------------------------------- error
  if (message.status === "error") {
    // Cold-start and transient errors shown as calm amber, not alarming red
    const isColdStart =
      message.errorCode === "backend_unavailable" ||
      message.errorCode === "backend_timeout" ||
      message.errorCode === "service_unavailable";
    return (
      <article aria-label="The request failed">
        <div
          className={`rounded-2xl rounded-bl-md border px-4 py-3 ${
            isColdStart
              ? "border-warning/30 bg-warning-soft"
              : "border-line-strong bg-surface-muted"
          }`}
        >
          <p
            className={`m-0 flex items-start gap-2 text-sm font-medium ${isColdStart ? "text-warning-ink" : "text-ink-soft"}`}
          >
            <svg viewBox="0 0 16 16" aria-hidden className="mt-0.5 h-4 w-4 shrink-0">
              <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
              <path d="M8 4.8v4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              <circle cx="8" cy="11.2" r="0.9" fill="currentColor" />
            </svg>
            {message.errorMessage}
          </p>
          {message.errorCode && message.errorCode !== "unknown_error" && (
            <p className="mb-0 mt-2 font-mono text-[11px] text-ink-faint">
              code: {message.errorCode}
            </p>
          )}
        </div>
      </article>
    );
  }

  // ------------------------------------------------------------------ complete
  const citations = message.citations ?? [];
  const agentPath = message.agent_path ?? [];
  const answer = message.answer ?? "";
  const { main, limitations } = splitLimitations(answer);
  const insufficient = isInsufficientEvidence(answer, citations.length);

  /** Markers toggle like the card headers; focus moves only when opening. */
  const expandFromMarker = (citationIndex: number) => {
    setExpandedIndex((previous) => (previous === citationIndex ? null : citationIndex));
    requestAnimationFrame(() => {
      if (expandedIndex !== citationIndex) {
        citationHeadingRefs.current[citationIndex]?.focus();
      }
    });
  };

  return (
    <article aria-label="Answer from Sentinel" className="space-y-3">
      {/* Gold left-border accent on assistant messages */}
      <div
        className="rounded-2xl rounded-bl-md border border-line bg-surface px-4 py-3.5 shadow-card"
        style={{ borderLeftWidth: "2px", borderLeftColor: "var(--accent)" }}
      >
        {main.trim().length > 0 ? (
          <AnswerMarkdown text={main} citations={citations} onMarkerClick={expandFromMarker} />
        ) : null}

        {limitations !== null && limitations.trim().length > 0 && (
          <aside
            aria-label="Limitations of this answer"
            className="mt-3 rounded-lg border border-warning/40 bg-warning-soft px-3 py-2.5"
          >
            <p className="m-0 mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-warning-ink">
              <svg viewBox="0 0 16 16" aria-hidden className="h-3.5 w-3.5">
                <path
                  d="M8 2.5 14.5 13h-13L8 2.5Z"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  fill="none"
                  strokeLinejoin="round"
                />
                <path d="M8 6.5v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                <circle cx="8" cy="11.3" r="0.8" fill="currentColor" />
              </svg>
              Limitations
            </p>
            <AnswerMarkdown
              text={limitations}
              citations={citations}
              onMarkerClick={expandFromMarker}
            />
          </aside>
        )}

        {insufficient && (
          <aside
            aria-label="No supporting evidence"
            className="mt-3 rounded-lg border border-warning/40 bg-warning-soft px-3 py-2.5 text-sm text-warning-ink"
          >
            <p className="m-0 flex items-start gap-2 font-medium">
              <svg viewBox="0 0 16 16" aria-hidden className="mt-0.5 h-4 w-4 shrink-0">
                <circle cx="7" cy="7" r="4.5" stroke="currentColor" strokeWidth="1.5" fill="none" />
                <path
                  d="m10.5 10.5 3 3"
                  stroke="currentColor"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                />
              </svg>
              No supporting evidence was found in the ingested corpus.
            </p>
            <p className="mb-0 mt-1.5 pl-6 text-xs">
              Sentinel refuses to guess without sources. Ingest the relevant filings or news first
              from the Sources page, then ask again.
            </p>
          </aside>
        )}
      </div>

      {citations.length > 0 && (
        <section aria-label={`Sources (${citations.length})`}>
          <h3 className="mb-2 mt-0 text-xs font-semibold uppercase tracking-wide text-ink-faint">
            Sources · {citations.length}
          </h3>
          <ul className="m-0 list-none space-y-2 p-0">
            {citations.map((citation, index) => (
              <CitationCard
                key={citation.chunk_id ?? `${citation.source_id}-${index}`}
                citation={citation}
                index={index}
                expanded={expandedIndex === index}
                onToggle={() => setExpandedIndex(expandedIndex === index ? null : index)}
                headingRef={(el) => {
                  citationHeadingRefs.current[index] = el;
                }}
              />
            ))}
          </ul>
        </section>
      )}

      {agentPath.length > 0 && (
        <AgentTraceViewer agentPath={agentPath} traceUrl={message.trace_url ?? null} />
      )}
    </article>
  );
}

function truncate(text: string, max = 120): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}
