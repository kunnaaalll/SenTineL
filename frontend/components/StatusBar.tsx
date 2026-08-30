"use client";

import { useEffect, useState } from "react";
import { ApiError, getReady, STATUS_TIMEOUT_MS, userMessage } from "@/lib/api";

type BackendState =
  | { kind: "loading" }
  | { kind: "ready"; detail?: string }
  | { kind: "degraded"; detail: string }
  | { kind: "offline"; detail: string };

/**
 * Header status pill showing backend health/readiness.
 * Polls GET /ready once on mount and periodically.
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
      <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface-muted px-2.5 py-1 text-[11px] text-ink-faint">
        <span aria-hidden className="animate-pulse-dot h-1.5 w-1.5 rounded-full bg-ink-faint" />
        Checking…
      </span>
    );
  }

  if (state.kind === "ready") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border border-success/30 bg-success-soft px-2.5 py-1 text-[11px] font-semibold text-success"
        title="The backend is configured and ready to answer questions."
      >
        <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-success" />
        Ready
      </span>
    );
  }

  const label = state.kind === "degraded" ? "Degraded" : "Offline";
  const isDegraded = state.kind === "degraded";
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[11px] font-semibold ${
        isDegraded
          ? "border-warning/30 bg-warning-soft text-warning-ink"
          : "border-line bg-surface-muted text-ink-faint"
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
        d="M8 2.5 14.5 13h-13L8 2.5Z"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <path d="M8 6.5v3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
      <circle cx="8" cy="11.3" r="0.8" fill="currentColor" />
    </svg>
  );
}
