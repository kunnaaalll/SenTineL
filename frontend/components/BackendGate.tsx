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
 * Wraps all page content and shows a full-page wake-up experience if the
 * backend is not yet reachable (Render cold-start, container startup, etc.).
 *
 * States:
 * - "checking"  → transparent pass-through; no flicker for fast backends
 * - "ready"     → render children normally (the common path)
 * - "degraded"  → render children with a non-destructive top banner
 * - "waking"    → full-page Namaste welcome + progress + live announcements
 * - "timeout"   → full-page retry state
 * - "error"     → full-page gentle error state
 *
 * Privacy: No stack traces, raw backend responses, provider errors, or
 * secret-bearing URLs are ever surfaced to the user.
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
        <div className="animate-reveal">
          {isBackendDegraded && <DegradedBanner />}
          {children}
        </div>
      </BackendContext.Provider>
    );
  }

  // Wake screen states
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
// WakeScreen — full-page cold-start / error experience
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
      className="fixed inset-0 z-50 flex items-center justify-center bg-background"
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

      {/* Decorative coordinate / aperture background */}
      <ConstellationBackground />

      {/* Centered card */}
      <div className="relative z-10 flex w-full max-w-md flex-col items-center px-6 py-10 text-center animate-fade-up">
        {/* Animated Brand Aperture Node */}
        <div className="relative mb-6">
          <div
            aria-hidden
            className="animate-orb-breathe flex h-20 w-20 items-center justify-center rounded-full bg-accent-soft border border-accent/30"
            style={{
              boxShadow: "0 0 36px rgba(194, 94, 62, 0.2), 0 0 72px rgba(194, 94, 62, 0.08)",
            }}
          >
            <SentinelLogo variant="symbol" size={36} />
          </div>
        </div>

        {/* Sentinel Brand Title */}
        <p className="mb-1 text-xs font-bold uppercase tracking-[0.2em] text-accent">SENTINEL</p>

        {/* Primary headline */}
        {isWaking ? (
          <>
            <h1 className="font-display mb-3 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Namaste, welcome to Sentinel.
            </h1>
            <p className="mb-8 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine is starting. This usually takes about one minute.
            </p>
          </>
        ) : isTimeout ? (
          <>
            <h1 className="font-display mb-3 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Sentinel is taking longer than expected.
            </h1>
            <p className="mb-8 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine may still be starting up on Render. Click below to check again.
            </p>
          </>
        ) : (
          <>
            <h1 className="font-display mb-3 text-2xl font-bold tracking-tight text-ink sm:text-3xl">
              Unable to connect.
            </h1>
            <p className="mb-8 max-w-xs text-sm leading-relaxed text-ink-soft">
              The research engine could not be reached. Please check your connection and try again.
            </p>
          </>
        )}

        {/* Progress section */}
        {isWaking && (
          <div
            className="mb-8 w-full max-w-xs"
            role="progressbar"
            aria-valuenow={Math.round(progress * 100)}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label={`Starting: ${elapsedSeconds} seconds elapsed`}
          >
            {/* Time label */}
            <p className="mb-2 text-center font-mono text-xs tabular-nums text-ink-faint">
              Starting… {elapsedSeconds}s
            </p>
            {/* Progress bar track */}
            <div className="h-1 w-full overflow-hidden rounded-full bg-line">
              <div
                className="h-full rounded-full bg-accent transition-all duration-1000"
                style={{ width: `${Math.round(progress * 100)}%` }}
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

        {/* Expandable technical details — safe and sanitised */}
        {(isTimeout || status === "error") && detail && (
          <details className="mt-6 w-full max-w-xs text-left">
            <summary className="cursor-pointer text-xs font-medium text-ink-faint transition-enabled hover:text-ink-soft [&::-webkit-details-marker]:hidden">
              Technical details
            </summary>
            <p className="mt-2 rounded-xl border border-line bg-surface-muted p-3 text-xs leading-relaxed text-ink-soft font-mono">
              {detail}
            </p>
          </details>
        )}

        {/* Research disclaimer */}
        <p className="mt-8 text-[11px] leading-relaxed text-ink-faint">
          Sentinel is a financial research tool for public filings and market news.
          <br />
          Not investment advice.
        </p>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ConstellationBackground — decorative coordinate motif
// ---------------------------------------------------------------------------

function ConstellationBackground() {
  const dots: { cx: number; cy: number; r: number; opacity: number }[] = [
    { cx: 15, cy: 20, r: 1.2, opacity: 0.4 },
    { cx: 82, cy: 12, r: 0.8, opacity: 0.3 },
    { cx: 35, cy: 78, r: 1.0, opacity: 0.35 },
    { cx: 68, cy: 65, r: 1.4, opacity: 0.45 },
    { cx: 90, cy: 40, r: 0.9, opacity: 0.3 },
    { cx: 8, cy: 55, r: 1.1, opacity: 0.32 },
    { cx: 50, cy: 92, r: 0.7, opacity: 0.25 },
    { cx: 72, cy: 88, r: 1.2, opacity: 0.38 },
    { cx: 25, cy: 45, r: 0.8, opacity: 0.3 },
    { cx: 60, cy: 30, r: 1.0, opacity: 0.34 },
    { cx: 92, cy: 75, r: 0.9, opacity: 0.28 },
    { cx: 40, cy: 15, r: 1.3, opacity: 0.4 },
  ];

  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden>
      {/* Outer constellation ring */}
      <div
        className="animate-constellation absolute inset-0 m-auto h-[520px] w-[520px] opacity-15"
        style={{ top: "50%", left: "50%", transform: "translate(-50%, -50%)" }}
      >
        <svg viewBox="0 0 100 100" className="h-full w-full">
          <circle
            cx="50"
            cy="50"
            r="48"
            fill="none"
            stroke="var(--accent)"
            strokeWidth="0.4"
            strokeDasharray="2 6"
          />
          {dots.map((d, i) => (
            <circle key={i} cx={d.cx} cy={d.cy} r={d.r} fill="var(--accent)" opacity={d.opacity} />
          ))}
        </svg>
      </div>
      {/* Inner ring (counter-rotating) */}
      <div
        className="animate-constellation-rev absolute opacity-10"
        style={{
          top: "50%",
          left: "50%",
          transform: "translate(-50%, -50%)",
          width: "320px",
          height: "320px",
        }}
      >
        <svg viewBox="0 0 100 100" className="h-full w-full">
          <circle
            cx="50"
            cy="50"
            r="48"
            fill="none"
            stroke="var(--ink)"
            strokeWidth="0.5"
            strokeDasharray="1 4"
          />
        </svg>
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
