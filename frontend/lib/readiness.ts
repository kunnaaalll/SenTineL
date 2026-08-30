/**
 * lib/readiness.ts
 *
 * Bounded backend readiness probe and polling hook.
 *
 * Rules:
 * - Uses only lib/api.ts wrappers — no credential or provider URL ever here.
 * - Polling is bounded: max POLL_MAX_COUNT probes over MAX_WAIT_MS.
 * - No infinite loop: cleanup on unmount via AbortController + counter guard.
 * - A cold-start (502/503/504/network error on /health) shows the wake screen.
 * - A configured-but-not-ready backend (503 not_ready on /ready) is "degraded",
 *   not a cold start — it does not trigger the full wake screen.
 * - Genuine 4xx errors or unexpected 5xx on /health are surfaced as "error".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  BackendTimeoutError,
  BackendUnavailableError,
  getHealth,
  getReady,
  STATUS_TIMEOUT_MS,
} from "./api";

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Probe interval during active polling (ms). */
export const POLL_INTERVAL_MS = 6_000;

/** Maximum total wait before showing the timeout / retry state (ms). */
export const MAX_WAIT_MS = 120_000;

/** Maximum number of polls before giving up (safety cap). */
export const POLL_MAX_COUNT = Math.ceil(MAX_WAIT_MS / POLL_INTERVAL_MS);

/** How long a single readiness probe may take before being treated as a
 *  cold-start indicator (network-level timeout). */
export const PROBE_TIMEOUT_MS = STATUS_TIMEOUT_MS;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export type ReadinessStatus =
  | "checking" // initial — never rendered to user as a screen
  | "ready" // backend live and serving
  | "waking" // cold-start in progress; wake screen shown
  | "degraded" // process is alive but not fully configured (not_ready)
  | "timeout" // exceeded MAX_WAIT_MS without becoming ready
  | "error"; // unexpected error (not a cold-start indicator)

export interface ReadinessState {
  status: ReadinessStatus;
  /** Seconds elapsed since the first cold-start detection. */
  elapsedSeconds: number;
  /** 0–1 fraction for progress bar; clamped at 1 at timeout. */
  progress: number;
  /** Safe, user-facing detail message — never raw backend text or credentials. */
  detail: string | null;
}

// ---------------------------------------------------------------------------
// Single probe
// ---------------------------------------------------------------------------

/**
 * Performs one readiness probe cycle:
 * 1. GET /health — if it fails with a cold-start signal → "waking"
 * 2. GET /ready  — if it returns 503 not_ready → "degraded"
 *                  if it returns 200 → "ready"
 *                  otherwise → "error"
 *
 * Never throws; always returns a ReadinessStatus.
 */
export async function checkReadiness(signal: AbortSignal): Promise<ReadinessStatus> {
  // Step 1: liveness probe
  try {
    await getHealth({ signal, timeoutMs: PROBE_TIMEOUT_MS });
  } catch (error) {
    if (signal.aborted) return "checking"; // cleanup; caller will ignore this
    if (isColdStartError(error)) return "waking";
    // An unexpected error on /health itself — treat as waking (safest UX)
    return "waking";
  }

  // Step 2: readiness probe
  try {
    await getReady({ signal, timeoutMs: PROBE_TIMEOUT_MS });
    return "ready";
  } catch (error) {
    if (signal.aborted) return "checking";
    if (error instanceof ApiError && error.status === 503 && error.code === "not_ready") {
      return "degraded";
    }
    if (isColdStartError(error)) return "waking";
    return "error";
  }
}

/**
 * Returns true for errors that indicate the backend process is not yet
 * accepting connections — i.e., genuine cold-start / container startup.
 */
function isColdStartError(error: unknown): boolean {
  if (error instanceof BackendUnavailableError) return true;
  if (error instanceof BackendTimeoutError) return true;
  if (error instanceof ApiError) {
    // 502 Bad Gateway, 503 Service Unavailable (without not_ready envelope),
    // 504 Gateway Timeout — all typical Render/proxy cold-start responses
    return error.status === 502 || error.status === 503 || error.status === 504;
  }
  return false;
}

// ---------------------------------------------------------------------------
// useReadiness hook
// ---------------------------------------------------------------------------

export function useReadiness(): ReadinessState & {
  retry: () => void;
} {
  const [status, setStatus] = useState<ReadinessStatus>("checking");
  const [elapsedSeconds, setElapsedSeconds] = useState(0);

  const abortRef = useRef<AbortController | null>(null);
  const pollCountRef = useRef(0);
  const startTimeRef = useRef<number | null>(null);
  const elapsedIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Increment to trigger a restart from timeout/error
  const retryGenRef = useRef(0);
  const [retryGen, setRetryGen] = useState(0);

  const progress = Math.min(elapsedSeconds / (MAX_WAIT_MS / 1000), 1);

  /** Called when transitioning into the waking state to start the elapsed timer. */
  const startElapsedTimer = useCallback(() => {
    if (elapsedIntervalRef.current !== null) return; // already running
    startTimeRef.current = Date.now();
    elapsedIntervalRef.current = setInterval(() => {
      const elapsed = Math.round((Date.now() - (startTimeRef.current ?? Date.now())) / 1000);
      setElapsedSeconds(elapsed);
    }, 1000);
  }, []);

  const stopElapsedTimer = useCallback(() => {
    if (elapsedIntervalRef.current !== null) {
      clearInterval(elapsedIntervalRef.current);
      elapsedIntervalRef.current = null;
    }
  }, []);

  const retry = useCallback(() => {
    abortRef.current?.abort();
    stopElapsedTimer();
    setElapsedSeconds(0);
    setStatus("checking");
    pollCountRef.current = 0;
    startTimeRef.current = null;
    retryGenRef.current += 1;
    setRetryGen(retryGenRef.current);
  }, [stopElapsedTimer]);

  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    abortRef.current = controller;
    pollCountRef.current = 0;

    async function runProbeLoop() {
      // First probe — immediate
      const first = await checkReadiness(controller.signal);
      if (!alive || controller.signal.aborted) return;

      if (first === "ready") {
        setStatus("ready");
        stopElapsedTimer();
        return;
      }

      if (first === "waking") {
        setStatus("waking");
        startElapsedTimer();
        // Bounded polling loop
        while (alive && !controller.signal.aborted) {
          pollCountRef.current += 1;
          if (pollCountRef.current > POLL_MAX_COUNT) {
            setStatus("timeout");
            stopElapsedTimer();
            return;
          }
          await sleep(POLL_INTERVAL_MS, controller.signal);
          if (!alive || controller.signal.aborted) return;

          const next = await checkReadiness(controller.signal);
          if (!alive || controller.signal.aborted) return;

          if (next === "ready") {
            setStatus("ready");
            stopElapsedTimer();
            return;
          }
          if (next === "degraded") {
            setStatus("degraded");
            stopElapsedTimer();
            return;
          }
          if (next === "error") {
            setStatus("error");
            stopElapsedTimer();
            return;
          }
          // still waking — continue loop
        }
        return;
      }

      if (first === "degraded") {
        setStatus("degraded");
        return;
      }

      // error / unexpected
      setStatus("error");
    }

    void runProbeLoop();

    return () => {
      alive = false;
      controller.abort();
      abortRef.current = null;
      stopElapsedTimer();
    };
  }, [retryGen, startElapsedTimer, stopElapsedTimer]);

  // Background health check during session (every 60s) once ready
  useEffect(() => {
    if (status !== "ready" && status !== "degraded") return;

    let alive = true;
    const controller = new AbortController();

    const interval = setInterval(async () => {
      const result = await checkReadiness(controller.signal);
      if (!alive) return;
      if (result === "waking") {
        setStatus("degraded"); // non-destructive — don't blow away chat
      } else if (result === "ready") {
        setStatus("ready");
      }
    }, 60_000);

    return () => {
      alive = false;
      clearInterval(interval);
      controller.abort();
    };
  }, [status]);

  const detail = statusDetail(status);

  return { status, elapsedSeconds, progress, detail, retry };
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise<void>((resolve) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

function statusDetail(status: ReadinessStatus): string | null {
  switch (status) {
    case "waking":
      return "The research engine is warming up. This usually takes about one minute.";
    case "degraded":
      return "The backend is running but not fully configured. Research questions may not be answerable until the provider credentials are set.";
    case "timeout":
      return "The backend took longer than expected to respond. It may still be starting up. Click Retry to check again.";
    case "error":
      return "An unexpected error occurred while connecting to the research engine.";
    default:
      return null;
  }
}
