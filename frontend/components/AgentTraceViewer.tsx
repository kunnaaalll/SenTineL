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
 */
export function AgentTraceViewer({ agentPath, traceUrl }: AgentTraceViewerProps) {
  if (agentPath.length === 0) return null;

  return (
    <details className="group rounded-xl border border-line bg-surface-muted transition-enabled">
      <summary className="flex cursor-pointer list-none items-center gap-2.5 px-4 py-2.5 text-xs font-semibold uppercase tracking-wider text-ink-soft transition-enabled hover:text-ink [&::-webkit-details-marker]:hidden">
        <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 shrink-0 text-accent">
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
        <span className="ml-auto font-sans text-[11px] font-normal lowercase tracking-normal text-ink-faint group-open:hidden">
          Show
        </span>
        <span className="ml-auto hidden font-sans text-[11px] font-normal lowercase tracking-normal text-ink-faint group-open:inline">
          Hide
        </span>
      </summary>

      <div className="border-t border-line px-4 py-3.5 space-y-3">
        <ol className="m-0 flex flex-wrap items-center gap-x-1.5 gap-y-2 p-0 list-none">
          {agentPath.map((step, i) => (
            <li key={`${step}-${i}`} className="flex items-center gap-1.5">
              {i > 0 && (
                <svg viewBox="0 0 16 16" aria-hidden className="h-3 w-3 text-ink-faint/60">
                  <path
                    d="m5 3 5 5-5 5"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              )}
              <span className="rounded-full border border-accent/25 bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent">
                {stepLabel(step)}
              </span>
            </li>
          ))}
        </ol>

        {traceUrl && (
          <p className="mb-0 mt-3 pt-1 border-t border-line/60">
            <a
              href={traceUrl}
              target="_blank"
              rel="noopener noreferrer"
              onClick={(event) => event.stopPropagation()}
              className="inline-flex items-center gap-1 text-xs font-semibold text-accent hover:text-accent-strong hover:underline"
            >
              <span>View full trace</span>
              <span aria-hidden> ↗</span>
              <span className="sr-only"> (opens in a new tab)</span>
            </a>
          </p>
        )}
      </div>
    </details>
  );
}
