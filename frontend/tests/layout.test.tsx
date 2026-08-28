import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import RootLayout from "@/app/layout";
import { errorEnvelope, jsonResponse, stubFetch } from "./helpers";

/**
 * Layout-level accessibility contracts: skip navigation, landmarks, the
 * research-only disclaimer, and the header status pill's degradation states.
 */
describe("RootLayout", () => {
  it("provides a skip link that targets the main landmark plus a disclaimer footer", async () => {
    stubFetch((url) => {
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

    expect(await screen.findByText(/backend ready/i)).toBeInTheDocument();
    expect(
      screen.getByText(/does not make investment recommendations or trade decisions/i),
    ).toBeInTheDocument();
  });

  it("announces degraded backends instead of hiding the problem", async () => {
    stubFetch(() => errorEnvelope(503, "not_ready", "Not configured."));
    render(
      <RootLayout>
        <p>Page body</p>
      </RootLayout>,
    );

    expect(await screen.findByText(/backend degraded/i)).toBeInTheDocument();
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

    expect(await screen.findByText(/backend offline/i)).toBeInTheDocument();
  });
});
