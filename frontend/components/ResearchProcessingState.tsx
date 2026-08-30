"use client";

import { useEffect, useState } from "react";
import { SentinelLogo } from "./SentinelLogo";

export interface ResearchProcessingStateProps {
  /** Explicit stage from backend/agents if available */
  stage?: string;
  /** Whether multi-agent analysis is active */
  forcedAgents?: boolean;
  className?: string;
}

const DEFAULT_STAGES: string[] = [
  "Sentinel is researching",
  "Reviewing filings and market sources",
  "Cross-checking evidence",
  "Preparing a cited answer",
];

/**
 * ResearchProcessingState — Restrained, premium financial research signal.
 *
 * Treatment:
 * - Crisp flat geometric Sentinel S-symbol.
 * - Thin ledger / signal line with a controlled sweep.
 * - Clear editorial typography rotating through truthful research phases.
 * - Zero glowing orbs, bouncing balls, fake percentages, or spinners.
 * - Full reduced-motion and screen-reader accessibility.
 */
export function ResearchProcessingState({
  stage,
  forcedAgents = false,
  className = "",
}: ResearchProcessingStateProps) {
  const [stageIndex, setStageIndex] = useState(0);

  // Rotate neutral stages every 3.5 seconds if no backend-specific stage is supplied
  useEffect(() => {
    if (stage) return;
    const interval = setInterval(() => {
      setStageIndex((prev) => (prev + 1) % DEFAULT_STAGES.length);
    }, 3500);
    return () => clearInterval(interval);
  }, [stage]);

  const activeStageText = stage ?? DEFAULT_STAGES[stageIndex];

  return (
    <div
      role="status"
      aria-live="polite"
      aria-label={activeStageText}
      className={`rounded-2xl rounded-bl-sm border border-line bg-surface p-5 shadow-card ${className}`}
      style={{ borderLeftWidth: "3px", borderLeftColor: "var(--accent)" }}
    >
      {/* Header with Geometric Sentinel Mark and Active Research Status */}
      <div className="flex items-center gap-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-surface-muted border border-line">
          <SentinelLogo variant="symbol" size={16} />
        </div>
        <div className="flex flex-col">
          <div className="flex items-center gap-2">
            <span
              aria-hidden
              className="animate-pulse-dot inline-block h-2 w-2 rounded-full bg-accent"
            />
            <p className="m-0 text-sm font-semibold text-ink leading-none">
              {activeStageText}
              <span className="sr-only">…</span>
            </p>
          </div>
          {forcedAgents && (
            <span className="mt-1 font-mono text-[10px] font-medium tracking-wide uppercase text-accent">
              Multi-agent synthesis pipeline
            </span>
          )}
        </div>
      </div>

      {/* Thin Ledger / Signal Line with Controlled Sweep */}
      <div className="mt-4 space-y-2" aria-hidden="true">
        <div className="relative h-1 w-full overflow-hidden rounded-full bg-surface-muted border border-line/60">
          <div className="animate-signal-sweep absolute inset-y-0 w-1/3 rounded-full bg-gradient-to-r from-transparent via-accent to-transparent" />
        </div>

        {/* Structured Ledger Rule Lines */}
        <div className="flex items-center justify-between pt-1 text-[11px] font-mono text-ink-faint">
          <span>SEC Filings & News</span>
          <span>Citation Verification</span>
          <span>Fact Grounding</span>
        </div>
      </div>
    </div>
  );
}
