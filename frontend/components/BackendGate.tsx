"use client";

import { createContext, useContext, type ReactNode } from "react";
import { useReadiness, type ReadinessStatus } from "@/lib/readiness";
import { SentinelLogo } from "./SentinelLogo";

// ---------------------------------------------------------------------------
// Context — child components can read the current backend status
// ---------------------------------------------------------------------------

interface BackendContextValue {
  status: ReadinessStatus;
  isBackendDegraded: boolean;
}

const BackendContext = createContext<BackendContextValue>({
  status: "checking",
  isBackendDegraded: false,
});

export function useBackendStatus(): BackendContextValue {
  return useContext(BackendContext);
}

// ---------------------------------------------------------------------------
// BackendGate
// ---------------------------------------------------------------------------

/**
 * Wraps all page content and shows a full-page startup experience if the
 * backend is not yet reachable (Render cold-start, container startup, etc.).
 *
 * States:
 * - "checking"  → transparent pass-through; no flicker for fast backends
 * - "ready"     → render children normally (the common path)
 * - "degraded"  → render children with a non-destructive top banner
 * - "waking"    → startup screen with "Namaste, welcome to Sentinel." + restrained progress
 * - "timeout"   → retry state with technical details behind expandable control
 * - "error"     → gentle error state with retry
 *
 * Privacy: No stack traces, raw responses, provider details, or secret-bearing URLs.
 */
export function BackendGate({ children }: { children: ReactNode }) {
  const { status, elapsedSeconds, progress, detail, retry } = useReadiness();

  const isBackendDegraded = status === "degraded";

  if (status === "checking") {
    // Don't flicker — wait silently for first probe result
    return (
      <BackendContext.Provider value={{ status, isBackendDegraded: false }}>
        <div className="opacity-0 pointer-events-none">{children}</div>
      </BackendContext.Provider>
    );
  }

  if (status === "ready" || status === "degraded") {
    return (
      <BackendContext.Provider value={{ status, isBackendDegraded }}>
        <div className="animate-reveal flex-1 flex flex-col">
          {isBackendDegraded && <DegradedBanner />}
          {children}
        </div>
      </BackendContext.Provider>
    );
  }

  // Startup / Wake screen states
  return (
    <BackendContext.Provider value={{ status, isBackendDegraded: false }}>
      <WakeScreen
        status={status}
        elapsedSeconds={elapsedSeconds}
        progress={progress}
        detail={detail}
        onRetry={retry}
      />
    </BackendContext.Provider>
  );
}

// ---------------------------------------------------------------------------
// DegradedBanner — non-destructive session banner
// ---------------------------------------------------------------------------

function DegradedBanner() {
  return (
    <div
      role="status"
      aria-live="polite"
      className="border-b border-warning/30 bg-warning-soft px-4 py-2.5 text-sm text-warning-ink"
    >
      <div className="mx-auto flex max-w-5xl items-center gap-2">
        <SignalIcon className="h-4 w-4 shrink-0 text-warning" aria-hidden />
        <p className="m-0 font-medium">
          Research engine is running in unconfigured mode.{" "}
          <span className="font-normal opacity-85">
            Answers may be limited until provider credentials are set on the backend.
          </span>
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// WakeScreen — full-page cold-start / startup / timeout experience
// ---------------------------------------------------------------------------

interface WakeScreenProps {
  status: "waking" | "timeout" | "error";
  elapsedSeconds: number;
  progress: number;
  detail: string | null;
  onRetry: () => void;
}

function WakeScreen({ status, elapsedSeconds, progress, detail, onRetry }: WakeScreenProps) {
  const isWaking = status === "waking";
  const isTimeout = status === "timeout";

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-background px-4"
      aria-label="Sentinel research engine starting"
    >
      {/* Live region for screen readers */}
      <div aria-live="polite" role="status" className="sr-only">
        {isWaking
          ? `Research engine starting. Elapsed time: ${elapsedSeconds} seconds.`
          : isTimeout
            ? "Sentinel is taking longer than expected. You can retry."
            : "Unable to connect to the research engine."}
      </div>

      {/* Centered card with geometric border & restrained elevation */}
      <div className="relative z-10 flex w-full max-w-md flex-col items-center rounded-2xl border border-line bg-surface p-8 text-center shadow-card sm:p-10 animate-fade-up">
        {/* Geometric Sentinel Brand Mark */}
        <div className="mb-5 flex h-14 w-14 items-center justify-center rounded-xl border border-accent/20 bg-accent-soft">
          <SentinelLogo variant="symbol" size={32} />
        </div>

        {/* Sentinel Brand Title */}
        <p className="mb-2 font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-accent">
          SENTINEL FINANCIAL INTELLIGENCE
        </p>

        {/* Primary headline & copy */}
        {isWaking ? (
          <>
            <h1 className="font-display mb-2.5 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Namaste, welcome to Sentinel.
            </h1>
            <p className="mb-6 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine is starting. This usually takes about one minute.
            </p>
          </>
        ) : isTimeout ? (
          <>
            <h1 className="font-display mb-2.5 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Sentinel is taking longer than expected.
            </h1>
            <p className="mb-6 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine may still be starting up. Click below to check again.
            </p>
          </>
        ) : (
          <>
            <h1 className="font-display mb-2.5 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Unable to connect.
            </h1>
            <p className="mb-6 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine could not be reached. Please check your connection and try again.
            </p>
          </>
        )}

        {/* Progress section with elapsed time */}
        {isWaking && (
          <div
            className="mb-6 w-full max-w-xs"
            role="progressbar"
            aria-valuenow={Math.round(progress * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Starting: ${elapsedSeconds} seconds elapsed`}
          >
            <div className="mb-2 flex items-center justify-between font-mono text-xs text-ink-faint">
              <span>Warming engines</span>
              <span className="tabular-nums">{elapsedSeconds}s</span>
            </div>
            <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-muted border border-line">
              <div
                className="h-full rounded-full bg-accent transition-all duration-1000"
                style={{ width: `${Math.max(4, Math.round(progress * 100))}%` }}
              />
            </div>
          </div>
        )}

        {/* Retry button */}
        {(isTimeout || status === "error") && (
          <button
            type="button"
            onClick={onRetry}
            className="transition-enabled rounded-xl bg-accent px-6 py-2.5 text-sm font-semibold text-on-accent hover:bg-accent-strong shadow-sm focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            Retry connection
          </button>
        )}

        {/* Expandable technical details — safe and sanitised (no stack traces, no secrets, no URLs) */}
        {(isTimeout || status === "error") && detail && (
          <details className="mt-5 w-full max-w-xs text-left">
            <summary className="cursor-pointer text-xs font-medium text-ink-faint transition-enabled hover:text-ink-soft [&::-webkit-details-marker]:hidden">
              Technical details
            </summary>
            <p className="mt-2 rounded-xl border border-line bg-surface-muted p-3 text-xs leading-relaxed text-ink-soft font-mono">
              {detail}
            </p>
          </details>
        )}

        {/* Research disclaimer */}
        <p className="mt-6 border-t border-line/60 pt-4 text-[11px] leading-relaxed text-ink-faint">
          Sentinel is a financial research tool for public filings and market news. Not investment
          advice.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Icons
// ---------------------------------------------------------------------------

function SignalIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M1 12a9.5 9.5 0 0 1 14 0"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M3.5 9.5a6.5 6.5 0 0 1 9 0"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <path
        d="M6 7a3.5 3.5 0 0 1 4 0"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
      />
      <circle cx="8" cy="14" r="1" fill="currentColor" />
    </svg>
  );
}
