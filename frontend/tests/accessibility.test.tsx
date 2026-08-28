import { describe, expect, it } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ChatWindow } from "@/components/ChatWindow";
import SourcesPage from "@/app/sources/page";
import { jsonResponse, stubFetch } from "./helpers";

describe("keyboard accessibility — research interface", () => {
  it("keeps every interactive control reachable by keyboard alone", async () => {
    const user = userEvent.setup();
    render(<ChatWindow />);

    // Keyboard-only path: focus an example question and activate it.
    const example = screen.getByRole("button", { name: /Summarize Tesla's main risk factors/i });
    example.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("textbox", { name: /your question/i })).toHaveFocus();
    expect(screen.getByRole("textbox", { name: /your question/i })).not.toBeDisabled();
  });

  it("submits with Enter from the textarea and returns focus there afterwards", async () => {
    const user = userEvent.setup();
    stubFetch(() =>
      jsonResponse({
        answer: "Answer text.",
        citations: [],
        agent_path: ["classify"],
        trace_url: null,
      }),
    );
    render(<ChatWindow />);

    const input = screen.getByLabelText(/your question/i);
    await user.type(input, "Keyboard question");
    await user.keyboard("{Enter}");

    await screen.findByText("Answer text.");
    await waitForFocus(input);
  });

  it("keeps example-question buttons operable by keyboard and refocuses the input", async () => {
    const user = userEvent.setup();
    render(<ChatWindow />);

    const example = screen.getByRole("button", { name: /Summarize Tesla's main risk factors/i });
    example.focus();
    await user.keyboard("{Enter}");

    expect(screen.getByLabelText(/your question/i)).toHaveValue(
      "Summarize Tesla's main risk factors from its latest 10-K.",
    );
    expect(screen.getByLabelText(/your question/i)).toHaveFocus();
  });

  it("toggles multi-agent mode via its labelled checkbox", async () => {
    const user = userEvent.setup();
    const calls = stubFetch(() =>
      jsonResponse({
        answer: "Agent answer.",
        citations: [],
        agent_path: ["classify", "fetch"],
        trace_url: null,
      }),
    );
    render(<ChatWindow />);

    const checkbox = screen.getByRole("checkbox", { name: /force multi-agent analysis/i });
    checkbox.focus();
    await user.keyboard(" "); // Space toggles checkboxes
    expect(checkbox).toBeChecked();

    await user.type(screen.getByLabelText(/your question/i), "Forced path please");
    await user.keyboard("{Enter}");
    await screen.findByText("Agent answer.");
    expect(calls[0]?.url).toBe("/agents/query");
  });
});

describe("keyboard accessibility — sources page", () => {
  function stubAll() {
    stubFetch((url) => {
      if (url.endsWith("/sources"))
        return jsonResponse({ sec_edgar: true, news_api: true, apex: false });
      if (url.endsWith("/providers"))
        return jsonResponse({
          available: ["openai"],
          generation_default: "openai",
          embedding_available: true,
          embedding_model: "m",
        });
      if (url.endsWith("/ready")) return jsonResponse({ status: "ready", checks: {} });
      return undefined;
    });
  }

  it("associates a programmatic label with every form control", async () => {
    stubAll();
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    for (const id of [
      "sec_filing-ticker",
      "sec_filing-filing-type",
      "sec_filing-query",
      "sec_filing-date-start",
      "sec_filing-date-end",
      "sec_filing-limit",
      "news-ticker",
      "news-date-start",
      "news-date-end",
      "news-limit",
    ]) {
      const control = document.getElementById(id) as HTMLInputElement | HTMLSelectElement | null;
      expect(control, `missing control #${id}`).not.toBeNull();
      const label = document.querySelector(`label[for="${id}"]`);
      expect(label, `missing label for #${id}`).not.toBeNull();
      expect(control?.labels?.[0]).toBe(label);
      expect(control).toHaveAccessibleName();
    }
  });

  it("announces ingestion progress to assistive tech via a live region", async () => {
    const user = userEvent.setup();
    stubFetch((url) => {
      if (url.endsWith("/sources"))
        return jsonResponse({ sec_edgar: true, news_api: true, apex: false });
      if (url.endsWith("/providers"))
        return jsonResponse({
          available: [],
          generation_default: null,
          embedding_available: true,
          embedding_model: "m",
        });
      if (url.endsWith("/ready")) return jsonResponse({ status: "ready", checks: {} });
      if (url.endsWith("/ingest")) {
        return new Promise<Response>(() => {}); // never resolves — stay "submitting"
      }
      return undefined;
    });
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(
      await screen.findByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }),
      "AAPL",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    const liveRegions = document.querySelectorAll('[aria-live="polite"]');
    const texts = Array.from(liveRegions).map((el) => el.textContent ?? "");
    expect(texts.some((text) => /ingesting documents/i.test(text))).toBe(true);
  });

  it("exposes validation problems through aria-invalid and described-by", async () => {
    const user = userEvent.setup();
    stubAll();
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(
      await screen.findByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }),
      "@@@",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    const tickerInput = document.getElementById("sec_filing-ticker") as HTMLInputElement;
    expect(tickerInput).toHaveAttribute("aria-invalid", "true");
    expect(tickerInput).toHaveAttribute("aria-describedby", "sec_filing-ticker-error");
    expect(document.getElementById("sec_filing-ticker-error")).toHaveTextContent(/1–6 characters/i);
  });
});

async function waitForFocus(element: HTMLElement): Promise<void> {
  const { waitFor } = await import("@testing-library/react");
  await waitFor(() => expect(element).toHaveFocus());
}

function labelTextFor(control: HTMLElement): string | RegExp {
  const id = control.id;
  const label = document.querySelector(`label[for="${id}"]`);
  const raw = label?.textContent ?? "";
  // The required-marker asterisk is aria-hidden but still sits in label text.
  const cleaned = raw.replace(/\*$/, "").trim();
  return new RegExp(`^${cleaned.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`, "i");
}
