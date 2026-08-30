import { describe, expect, it, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import RootLayout from "@/app/layout";
import { errorEnvelope, jsonResponse, stubFetch } from "./helpers";

/**
 * Layout-level accessibility contracts: skip navigation, landmarks, the
 * research-only disclaimer, and the header status pill's degradation states.
 *
 * NOTE: RootLayout now includes BackendGate, which also probes /health.
 * The fetch stub must handle both /health and /ready.
 */
describe("RootLayout", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("provides a skip link that targets the main landmark plus a disclaimer footer", async () => {
    // Stub both /health (for BackendGate) and /ready (for StatusBar)
    stubFetch((url) => {
      if (url.endsWith("/health")) return jsonResponse({ status: "ok" });
      if (url.endsWith("/ready"))
        return jsonResponse({ status: "ready", checks: { embedding_provider: true } });
      return undefined;
    });

    const { container } = render(
      <RootLayout>
        <p>Page body</p>
      </RootLayout>,
    );

    const skip = screen.getByRole("link", { name: /skip to content/i });
    expect(skip).toHaveAttribute("href", "#main-content");
    expect(container.querySelector("#main-content")).not.toBeNull();
    expect(container.querySelector("nav[aria-label='Primary']")).not.toBeNull();

    // New pill label is just "Ready" (condensed from "Backend ready")
    expect(await screen.findByText(/^Ready$/i)).toBeInTheDocument();
    // New footer disclaimer wording
    expect(
      screen.getByText(/does not provide investment advice or make trading decisions/i),
    ).toBeInTheDocument();
  });

  it("announces degraded backends instead of hiding the problem", async () => {
    stubFetch((url) => {
      if (url.endsWith("/health")) return jsonResponse({ status: "ok" });
      return errorEnvelope(503, "not_ready", "Not configured.");
    });
    render(
      <RootLayout>
        <p>Page body</p>
      </RootLayout>,
    );

    // New pill label is "Degraded" (condensed from "Backend degraded")
    expect(await screen.findByText(/^Degraded$/i)).toBeInTheDocument();
  });

  it("announces unreachable backends instead of hiding the problem", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    render(
      <RootLayout>
        <p>Page body</p>
      </RootLayout>,
    );

    // New pill label is "Offline" (condensed from "Backend offline")
    // The BackendGate will show the wake screen, so we look in the wake screen area too
    await waitFor(
      () => {
        const text = document.body.textContent ?? "";
        // Either the status pill says "Offline" or the wake screen is showing
        const hasOffline = /offline/i.test(text);
        const hasWakeScreen = /namaste|research engine is starting/i.test(text);
        expect(hasOffline || hasWakeScreen).toBe(true);
      },
      { timeout: 3000 },
    );
  });
});
