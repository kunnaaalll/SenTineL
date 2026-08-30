"use client";

const STEP_LABELS: Record<string, string> = {
  classify: "Classify",
  rewrite: "Rewrite query",
  embed: "Embed question",
  retrieve: "Retrieve evidence",
  generate: "Generate answer",
  fetch: "Gather sources",
  extract: "Extract facts",
  compare: "Compare entities",
  synthesize: "Synthesize answer",
};

export function stepLabel(step: string): string {
  return STEP_LABELS[step] ?? step;
}

interface AgentTraceViewerProps {
  /** Ordered pipeline steps from QueryResponse.agent_path. */
  agentPath: string[];
  traceUrl: string | null;
}

/**
 * Collapsible visualization of which agents ran, in order, for one answer.
 * Native <details>/<summary> keeps expand/collapse keyboard-accessible with
 * zero custom focus management.
 */
export function AgentTraceViewer({ agentPath, traceUrl }: AgentTraceViewerProps) {
  if (agentPath.length === 0) return null;

  return (
    <details className="group rounded-lg border border-line bg-surface-muted">
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-ink-soft transition-enabled hover:text-ink [&::-webkit-details-marker]:hidden">
        <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 shrink-0">
          <path
            d="M2 8h3m6 0h3M8 2v3m0 6v3"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
          <circle cx="8" cy="8" r="2" stroke="currentColor" strokeWidth="1.5" fill="none" />
        </svg>
        <span>
          Pipeline · {agentPath.length} step{agentPath.length === 1 ? "" : "s"}
        </span>
        <span className="ml-auto text-[11px] font-normal text-ink-faint group-open:hidden">
          Show
        </span>
        <span className="ml-auto hidden text-[11px] font-normal text-ink-faint group-open:inline">
          Hide
        </span>
      </summary>

      <div className="border-t border-line px-3 py-3">
        <ol className="m-0 flex flex-wrap items-center gap-x-1 gap-y-1.5 p-0 pl-1 list-none">
          {agentPath.map((step, i) => (
            <li key={`${step}-${i}`} className="flex items-center gap-1">
              {i > 0 && (
                <svg viewBox="0 0 16 16" aria-hidden className="h-3 w-3 text-accent/40">
                  <path
                    d="m5 3 5 5-5 5"
                    stroke="currentColor"
                    strokeWidth="2"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
              <span className="rounded-full border border-accent/30 bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent">
                {stepLabel(step)}
              </span>
            </li>
          ))}
        </ol>

        {traceUrl && (
          <p className="mb-0 mt-3">
            <a
              href={traceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(event) => event.stopPropagation()}
              className="text-sm font-medium text-accent underline decoration-accent/40 underline-offset-2 transition-enabled hover:text-accent-strong hover:decoration-accent"
            >
              View full trace
              <span aria-hidden> ↗</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </p>
        )}
      </div>
    </details>
  );
}
