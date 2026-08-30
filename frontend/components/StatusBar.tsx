"use client";

import { useEffect, useState } from "react";
import { ApiError, getReady, STATUS_TIMEOUT_MS, userMessage } from "@/lib/api";

type BackendState =
  | { kind: "loading" }
  | { kind: "ready"; detail?: string }
  | { kind: "degraded"; detail: string }
  | { kind: "offline"; detail: string };

/**
 * Header pill showing whether the backend can currently answer questions.
 * Polls GET /ready once on mount and then once a minute. Failures render as
 * explicit states — never a blank. BackendGate handles the wake-up screen;
 * StatusBar is informational context only.
 */
export function StatusBar() {
  const [state, setState] = useState<BackendState>({ kind: "loading" });

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();

    async function probe() {
      try {
        const ready = await getReady({ signal: controller.signal, timeoutMs: STATUS_TIMEOUT_MS });
        if (!alive) return;
        setState({ kind: "ready", detail: ready.status });
      } catch (error) {
        if (!alive || controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 503) {
          setState({ kind: "degraded", detail: userMessage(error) });
        } else {
          setState({ kind: "offline", detail: userMessage(error) });
        }
      }
    }

    void probe();
    const interval = setInterval(probe, 60_000);
    return () => {
      alive = false;
      clearInterval(interval);
      controller.abort();
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-[11px] text-ink-faint">
        <span aria-hidden className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-ink-faint" />
        Checking…
      </span>
    );
  }

  if (state.kind === "ready") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border border-accent/30 bg-accent-soft px-2.5 py-1 text-[11px] font-medium text-accent"
        title="The backend is configured and can answer questions."
      >
        <span
          aria-hidden
          className="h-1.5 w-1.5 rounded-full bg-accent"
          style={{ boxShadow: "0 0 4px rgba(200,160,48,0.6)" }}
        />
        Ready
      </span>
    );
  }

  const label = state.kind === "degraded" ? "Degraded" : "Offline";
  const isDegraded = state.kind === "degraded";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-medium ${
        isDegraded
          ? "border-warning/30 bg-warning-soft text-warning-ink"
          : "border-line bg-surface text-ink-faint"
      }`}
      title={state.detail}
    >
      <WarningIcon className="h-3 w-3" aria-hidden />
      {label}
    </span>
  );
}

export function CheckIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <circle cx="8" cy="8" r="6.5" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="m5.2 8.3 1.9 1.9 3.7-4.4"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function WarningIcon({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" fill="none" className={className} aria-hidden>
      <path
        d="M8 2.2 14.6 13H1.4L8 2.2Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M8 6.4v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.4" r="0.9" fill="currentColor" />
    </svg>
  );
}
