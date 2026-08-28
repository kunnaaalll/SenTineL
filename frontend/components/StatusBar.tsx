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
 * Polls GET /ready once on mount and then once a minute; every failure mode
 * (degraded 503, network error) renders as an explicit state — never a blank.
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
      <span className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs text-ink-faint">
        Checking backend…
      </span>
    );
  }

  if (state.kind === "ready") {
    return (
      <span
        className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-medium text-success"
        title="The backend is configured and can answer questions."
      >
        <CheckIcon className="h-3.5 w-3.5" aria-hidden />
        Backend ready
      </span>
    );
  }

  const label = state.kind === "degraded" ? "Backend degraded" : "Backend offline";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full border border-line bg-surface px-2.5 py-1 text-xs font-medium text-warning"
      title={state.detail}
    >
      <WarningIcon className="h-3.5 w-3.5" aria-hidden />
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
