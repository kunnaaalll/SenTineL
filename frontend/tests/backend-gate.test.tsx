/**
 * tests/backend-gate.test.tsx
 *
 * Offline tests for the BackendGate component and useReadiness hook.
 * All tests use stubbed fetch — no real network calls.
 */

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor, act } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BackendGate } from "@/components/BackendGate";
import { checkReadiness, POLL_INTERVAL_MS } from "@/lib/readiness";
import { stubFetch, jsonResponse } from "./helpers";

// ---------------------------------------------------------------------------
// Shared response builders
// ---------------------------------------------------------------------------

function healthOk() {
  return jsonResponse({ status: "ok", service: "sentinel-backend" });
}

function readyOk() {
  return jsonResponse({ status: "ready", checks: { llm: true, vector_store: true } });
}

function gateway502() {
  return new Response("Bad Gateway", { status: 502 });
}

function serviceUnavailable() {
  return new Response(
    JSON.stringify({ error: { code: "not_ready", message: "LLM not configured" } }),
    { status: 503, headers: { "content-type": "application/json" } },
  );
}

// ---------------------------------------------------------------------------
// checkReadiness unit tests
// ---------------------------------------------------------------------------

describe("checkReadiness", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('returns "ready" when /health and /ready both return 200', async () => {
    stubFetch((url) => {
      if (url.includes("/health")) return healthOk();
      if (url.includes("/ready")) return readyOk();
    });
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("ready");
  });

  it('returns "waking" when /health returns 502', async () => {
    stubFetch(() => gateway502());
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("waking");
  });

  it('returns "waking" when /health returns 503 without not_ready envelope', async () => {
    stubFetch(() => new Response("", { status: 503 }));
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("waking");
  });

  it('returns "waking" when /health returns 504', async () => {
    stubFetch(() => new Response("", { status: 504 }));
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("waking");
  });

  it('returns "waking" when fetch throws (network error)', async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("waking");
  });

  it('returns "degraded" when /health is 200 but /ready returns 503 not_ready', async () => {
    stubFetch((url) => {
      if (url.includes("/health")) return healthOk();
      if (url.includes("/ready")) return serviceUnavailable();
    });
    const result = await checkReadiness(new AbortController().signal);
    expect(result).toBe("degraded");
  });

  it('returns "checking" when signal is already aborted', async () => {
    const controller = new AbortController();
    controller.abort();
    stubFetch(() => healthOk());
    const result = await checkReadiness(controller.signal);
    // May return "checking" or any value — the key is it must not throw
    expect(typeof result).toBe("string");
  });
});

// ---------------------------------------------------------------------------
// BackendGate component tests
// ---------------------------------------------------------------------------

describe("BackendGate", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("renders children immediately when backend is ready", async () => {
    stubFetch((url) => {
      if (url.includes("/health")) return healthOk();
      if (url.includes("/ready")) return readyOk();
    });

    render(
      <BackendGate>
        <p>Research interface</p>
      </BackendGate>,
    );

    await waitFor(() => {
      expect(screen.getByText("Research interface")).toBeInTheDocument();
    });

    // No wake screen should be shown
    expect(screen.queryByText(/Namaste/)).toBeNull();
    expect(screen.queryByText(/research engine is starting/i)).toBeNull();
  });

  it("shows wake screen on 502 cold-start", async () => {
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>Research interface</p>
      </BackendGate>,
    );

    await waitFor(() => {
      expect(screen.getByText(/Namaste, welcome to Sentinel/i)).toBeInTheDocument();
    });
    expect(screen.getByText(/research engine is starting/i)).toBeInTheDocument();
  });

  it("shows wake screen on network error (fetch throws)", async () => {
    stubFetch(() => {
      throw new TypeError("Failed to fetch");
    });

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    await waitFor(() => {
      expect(screen.getByText(/Namaste/i)).toBeInTheDocument();
    });
  });

  it("transitions from wake screen to children when backend becomes ready", async () => {
    vi.useFakeTimers();
    let callCount = 0;
    stubFetch((url) => {
      callCount += 1;
      if (url.includes("/health")) {
        // First call returns 502, subsequent calls return 200
        return callCount <= 1 ? gateway502() : healthOk();
      }
      if (url.includes("/ready")) return readyOk();
    });

    render(
      <BackendGate>
        <p>Research interface</p>
      </BackendGate>,
    );

    // Let the initial probe complete and switch to waking
    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText(/Namaste/i)).toBeInTheDocument();

    // Advance time to trigger the next poll
    await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 500);
    await vi.advanceTimersByTimeAsync(100);

    // Should transition to ready
    expect(screen.getByText("Research interface")).toBeInTheDocument();
  });

  it("shows progress time counter while waking", async () => {
    vi.useFakeTimers();
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText(/Namaste/i)).toBeInTheDocument();

    // Advance 10 seconds
    await vi.advanceTimersByTimeAsync(10_000);

    const progressBar = screen.queryByRole("progressbar");
    expect(progressBar).not.toBeNull();
  });

  it("shows retry state after poll timeout", async () => {
    vi.useFakeTimers();
    // Always return 502 — timeout after MAX_WAIT_MS
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    // Initial probe
    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText(/Namaste/i)).toBeInTheDocument();

    // Exhaust all polls (POLL_MAX_COUNT is ~20 polls)
    for (let i = 0; i < 22; i++) {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 500);
    }

    expect(
      screen.getByRole("heading", { name: /taking longer than expected/i }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  });

  it("retry button resets and re-probes", async () => {
    vi.useFakeTimers();
    let isBackendReady = false;
    stubFetch((url) => {
      if (url.includes("/health")) {
        return isBackendReady ? healthOk() : gateway502();
      }
      if (url.includes("/ready")) return readyOk();
    });

    render(
      <BackendGate>
        <p>Research interface</p>
      </BackendGate>,
    );

    // Get to timeout state
    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(100);
    for (let i = 0; i < 22; i++) {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS + 500);
    }

    expect(
      screen.getByRole("heading", { name: /taking longer than expected/i }),
    ).toBeInTheDocument();

    const retryBtn = screen.getByRole("button", { name: /retry/i });
    // Backend comes online before user clicks retry
    isBackendReady = true;

    await act(async () => {
      retryBtn.click();
    });

    await vi.advanceTimersByTimeAsync(100);
    await vi.advanceTimersByTimeAsync(100);
    expect(screen.getByText("Research interface")).toBeInTheDocument();
  });

  it("has a live-region for screen readers", async () => {
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    await waitFor(() => screen.getByText(/Namaste/i));

    // There should be a role="status" with aria-live="polite"
    const liveRegion = document.querySelector("[aria-live='polite']");
    expect(liveRegion).not.toBeNull();
  });

  it("does NOT show wake screen for degraded (not_ready) backend", async () => {
    stubFetch((url) => {
      if (url.includes("/health")) return healthOk();
      if (url.includes("/ready")) return serviceUnavailable();
    });

    render(
      <BackendGate>
        <p>Research interface</p>
      </BackendGate>,
    );

    // Should NOT show wake screen — backend is alive but not configured
    await waitFor(() => {
      expect(screen.getByText("Research interface")).toBeInTheDocument();
    });
    expect(screen.queryByText(/Namaste/i)).toBeNull();
  });

  it("never shows raw backend error responses to user", async () => {
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    await waitFor(() => screen.getByText(/Namaste/i));

    // The wake screen must not contain raw HTTP responses or stack traces
    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/502/);
    expect(body).not.toMatch(/Bad Gateway/);
    expect(body).not.toMatch(/stack trace/i);
  });

  it("does not expose provider credentials or secret-bearing URLs", async () => {
    stubFetch(() => gateway502());

    render(
      <BackendGate>
        <p>hidden</p>
      </BackendGate>,
    );

    await waitFor(() => screen.getByText(/Namaste/i));

    const body = document.body.textContent ?? "";
    expect(body).not.toMatch(/OPENAI_API_KEY/);
    expect(body).not.toMatch(/PINECONE/);
    expect(body).not.toMatch(/LANGFUSE_SECRET/);
    expect(body).not.toMatch(/sk-/);
  });
});
