"use client";

import { Citation } from "@/lib/api";

/** Human labels for the source_id prefixes the backend emits ("SEC:AAPL:…"). */
const SOURCE_TYPE_LABELS: Record<string, string> = {
  SEC: "SEC filing",
  NEWS: "Market news",
  TRANSCRIPT: "Earnings call",
  APEX_PORTFOLIO: "Portfolio data",
};

export function sourceTypeLabel(citation: Citation): string {
  const prefix = citation.source_id.split(":", 1)[0]?.toUpperCase() ?? "";
  return SOURCE_TYPE_LABELS[prefix] ?? "Source";
}

export function citationSection(citation: Citation): string | null {
  if (citation.section) return citation.section;
  const match = /^\[([^\]]+)\]/.exec(citation.excerpt || "");
  return match ? match[1] : null;
}

export function citationDate(citation: Citation): string | null {
  const match = /(\d{4}-\d{2}-\d{2})/.exec(`${citation.title} ${citation.source_id}`);
  return match?.[1] ?? null;
}

interface CitationCardProps {
  citation: Citation;
  /** 0-based index; displayed as the 1-based [n] marker it answers. */
  index: number;
  expanded: boolean;
  onToggle: () => void;
  /** Heading receives focus when the card is expanded from a [n] marker. */
  headingRef?: React.Ref<HTMLParagraphElement>;
}

/**
 * Expandable evidence card for one retrieved chunk. Toggle is a real button
 * (Enter/Space work natively); state is mirrored through aria-expanded so
 * screen readers announce it.
 */
export function CitationCard({
  citation,
  index,
  expanded,
  onToggle,
  headingRef,
}: CitationCardProps) {
  const buttonId = `citation-${index}-button`;
  const panelId = `citation-${index}-panel`;
  const date = citationDate(citation);
  const section = citationSection(citation);
  const scorePct =
    typeof citation.score === "number" && citation.score > 0
      ? `${Math.round(citation.score * 100)}% match`
      : null;

  return (
    <li className="rounded-lg border border-line bg-surface-raised shadow-card">
      <h4 className="m-0">
        <button
          type="button"
          id={buttonId}
          aria-expanded={expanded}
          aria-controls={panelId}
          onClick={onToggle}
          className="flex w-full items-center gap-2.5 rounded-t-lg px-3 py-2.5 text-left transition-enabled hover:bg-surface-muted"
        >
          <span
            aria-hidden
            className="inline-flex h-5 min-w-5 shrink-0 items-center justify-center rounded border border-line bg-accent-soft px-1 font-mono text-[11px] font-semibold text-accent"
          >
            {index + 1}
          </span>
          <span
            ref={headingRef}
            tabIndex={expanded ? -1 : undefined}
            className="min-w-0 flex-1 truncate text-sm font-medium text-ink"
          >
            {citation.title || "Untitled source"}
            {section ? (
              <span className="ml-1.5 text-xs font-normal text-ink-faint">
                — {section.replace(/^Item\s+/i, "Item ")}
              </span>
            ) : null}
          </span>
          <svg
            viewBox="0 0 16 16"
            aria-hidden
            className={`h-4 w-4 shrink-0 text-ink-faint ${expanded ? "rotate-180" : ""}`}
          >
            <path
              d="m4 6 4 4 4-4"
              stroke="currentColor"
              strokeWidth="1.5"
              fill="none"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </h4>

      {expanded && (
        <div
          id={panelId}
          role="region"
          aria-labelledby={buttonId}
          className="border-t border-line px-3 py-3"
        >
          <p className="m-0 mb-2.5 text-sm leading-relaxed text-ink-soft">
            {citation.excerpt || "No excerpt available."}
          </p>
          <dl className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ink-faint">
            <div className="flex items-center gap-1">
              <dt className="sr-only">Source type</dt>
              <dd className="m-0 rounded bg-surface-muted px-1.5 py-0.5 font-medium text-ink-soft">
                {sourceTypeLabel(citation)}
              </dd>
            </div>
            {date && (
              <div className="flex items-center gap-1">
                <dt className="font-medium">Date</dt>
                <dd className="m-0 font-mono">{date}</dd>
              </div>
            )}
            {citation.section && (
              <div className="flex min-w-0 items-center gap-1">
                <dt className="shrink-0 font-medium">Section</dt>
                <dd className="m-0 truncate">{citation.section}</dd>
              </div>
            )}
            {scorePct && (
              <div className="flex items-center gap-1">
                <dt className="sr-only">Retrieval relevance</dt>
                <dd className="m-0">{scorePct}</dd>
              </div>
            )}
          </dl>
          {citation.url ? (
            <a
              href={citation.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-sm font-medium text-accent underline decoration-accent/40 underline-offset-2 transition-enabled hover:text-accent-strong hover:decoration-accent"
            >
              View source document
              <span aria-hidden> ↗</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          ) : (
            <span className="text-xs italic text-ink-faint">No public link for this source.</span>
          )}
        </div>
      )}
    </li>
  );
}
