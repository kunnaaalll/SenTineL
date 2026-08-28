import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SourcesPage from "@/app/sources/page";
import { errorEnvelope, jsonResponse, stubFetch } from "./helpers";

function stubStatus({
  sources = { sec_edgar: true, news_api: true, apex: false },
  providers = {
    available: ["openai", "ollama"],
    generation_default: "openai",
    embedding_available: true,
    embedding_model: "text-embedding-3-small",
  },
  ready = { status: "ready", checks: { embedding_provider: true, vector_store: true } },
}: {
  sources?: Record<string, boolean>;
  providers?: Record<string, unknown>;
  ready?: Record<string, unknown>;
} = {}) {
  return stubFetch((url) => {
    if (url.endsWith("/sources")) return jsonResponse(sources);
    if (url.endsWith("/providers")) return jsonResponse(providers);
    if (url.endsWith("/ready")) return jsonResponse(ready);
    if (url.endsWith("/ingest")) {
      return jsonResponse({
        documents_fetched: 1,
        documents_ingested: 1,
        chunks_indexed: 42,
        chunks_truncated_for_metadata: 0,
        documents_failed: 0,
        failures: [],
        embedding_provider: "openai",
        embedding_model: "text-embedding-3-small",
        duration_ms: 5127.3,
        ok: true,
      });
    }
    return undefined;
  });
}

describe("availability display", () => {
  it("shows each source's live availability with text labels, not just color", async () => {
    stubStatus({ sources: { sec_edgar: true, news_api: false, apex: false } });
    render(<SourcesPage />);

    expect(await screen.findByText("SEC EDGAR")).toBeInTheDocument();
    expect(screen.getByText("Available")).toBeInTheDocument();
    // News and APEX are both down here — two explicit labels, not just color.
    expect(screen.getAllByText("Unavailable")).toHaveLength(2);
    expect(screen.getByText(/needs news_api_key configured on the backend/i)).toBeInTheDocument();
    // APEX is disabled by design and says so.
    expect(screen.getByText(/optional adapter — disabled by design\./i)).toBeInTheDocument();
  });

  it("summarizes provider chain state and readiness", async () => {
    stubStatus();
    render(<SourcesPage />);

    expect(await screen.findByText("openai")).toBeInTheDocument();
    expect(screen.getByText(/default for generation/i)).toBeInTheDocument();
    expect(screen.getByText(/embeddings via/i)).toHaveTextContent("text-embedding-3-small");
    expect(screen.getByText("Ready")).toBeInTheDocument();
  });

  it("names failing readiness checks when degraded", async () => {
    stubStatus({
      ready: { status: "degraded", checks: { embedding_provider: false, vector_store: true } },
    });
    render(<SourcesPage />);

    expect(await screen.findByText("Degraded")).toBeInTheDocument();
    expect(screen.getByText(/waiting on: embedding_provider/i)).toBeInTheDocument();
  });

  it("renders an explicit outage banner when every probe fails", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    render(<SourcesPage />);

    expect(
      await screen.findByText(
        /cannot reach the sentinel backend, so availability cannot be checked/i,
      ),
    ).toBeInTheDocument();
  });
});

describe("news ingestion gating", () => {
  it("overlays an explanation when news is unavailable but keeps SEC usable", async () => {
    stubStatus({ sources: { sec_edgar: true, news_api: false, apex: false } });
    render(<SourcesPage />);

    await screen.findByText(/news ingestion is unavailable/i);
    expect(await screen.findByRole("button", { name: /ingest filings/i })).toBeEnabled();
  });
});

describe("SEC ingestion form", () => {
  it("validates ticker format, date order, and limit range before submitting", async () => {
    const user = userEvent.setup();
    const calls = stubStatus();
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(
      await screen.findByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }),
      "!!",
    );
    await user.type(
      screen.getByLabelText(/filed from/i, { selector: "#sec_filing-date-start" }),
      "2024-12-31",
    );
    await user.type(
      screen.getByLabelText(/filed to/i, { selector: "#sec_filing-date-end" }),
      "2024-01-01",
    );
    await user.clear(screen.getByLabelText(/max documents/i, { selector: "#sec_filing-limit" }));
    await user.type(
      screen.getByLabelText(/max documents/i, { selector: "#sec_filing-limit" }),
      "99",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    expect(await screen.findByText(/use 1–6 characters/i)).toBeInTheDocument();
    expect(screen.getByText(/end date must be on or after the start date/i)).toBeInTheDocument();
    expect(screen.getByText(/between 1 and 25/i)).toBeInTheDocument();
    expect(calls.filter((call) => call.url.endsWith("/ingest"))).toHaveLength(0);
    expect(screen.getByRole("alert")).toHaveTextContent(/fix the highlighted fields/i);
  });

  it("submits a well-formed request and renders the ingestion summary", async () => {
    const user = userEvent.setup();
    const calls = stubStatus();
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(screen.getByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }), "AAPL");
    await user.selectOptions(
      screen.getByLabelText(/filing type/i, { selector: "#sec_filing-filing-type" }),
      "10-K",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    const summary = await screen.findByLabelText(/ingestion result/i);
    expect(summary).toHaveTextContent(/ingested 1 of 1 document/i);
    expect(summary).toHaveTextContent(/chunks indexed\s*42/i);
    expect(summary).toHaveTextContent(/5\.1s/i);

    const ingestCall = calls.find((call) => call.url.endsWith("/ingest"));
    expect(ingestCall).toBeDefined();
    expect(JSON.parse(String(ingestCall?.init.body))).toEqual({
      source_type: "sec_filing",
      ticker: "AAPL",
      filing_type: "10-K",
      limit: 5,
    });
  });

  it("lists per-document failures inside a successful run", async () => {
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
      if (url.endsWith("/ingest"))
        return jsonResponse({
          documents_fetched: 2,
          documents_ingested: 1,
          chunks_indexed: 7,
          chunks_truncated_for_metadata: 0,
          documents_failed: 1,
          failures: [
            { source_id: "SEC:AAPL:10-K:2024", stage: "embed", error: "dimension mismatch" },
          ],
          embedding_provider: "openai",
          embedding_model: null,
          duration_ms: 1000,
          ok: false,
        });
      return undefined;
    });
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(
      await screen.findByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }),
      "MSFT",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    const summary = await screen.findByLabelText(/ingestion result/i);
    expect(summary).toHaveTextContent(/finished with problems/i);
    expect(summary).toHaveTextContent(/failed documents · 1/i);
    expect(summary).toHaveTextContent(/stage embed: dimension mismatch/i);
  });

  it("surfaces backend degradation errors safely when ingest returns 503", async () => {
    const user = userEvent.setup();
    stubFetch((url) => {
      if (url.endsWith("/sources"))
        return jsonResponse({ sec_edgar: true, news_api: true, apex: false });
      if (url.endsWith("/providers"))
        return jsonResponse({
          available: [],
          generation_default: null,
          embedding_available: false,
          embedding_model: null,
        });
      if (url.endsWith("/ready")) return errorEnvelope(503, "not_ready", "Not configured.");
      return errorEnvelope(
        503,
        "no_embedding_provider",
        "No embedding-capable LLM provider is available.",
      );
    });
    render(<SourcesPage />);
    await screen.findByText("Ingest SEC filings");

    await user.type(
      await screen.findByLabelText(/^ticker$/i, { selector: "#sec_filing-ticker" }),
      "NVDA",
    );
    await user.click(screen.getByRole("button", { name: /ingest filings/i }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/no embedding provider is configured on the backend/i);
    expect(alert).toHaveTextContent(/error code: no_embedding_provider/);
  });
});

describe("news ingestion form", () => {
  it("requires a ticker and posts a news-source request", async () => {
    const user = userEvent.setup();
    const calls = stubStatus();
    render(<SourcesPage />);
    await screen.findByText(/ingest market news/i);

    await user.click(screen.getByRole("button", { name: /ingest news/i }));
    expect(await screen.findByText(/a ticker is required for news ingestion/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^ticker/i, { selector: "#news-ticker" }), "TSLA");
    await user.type(
      screen.getByLabelText(/filed from/i, { selector: "#news-date-start" }),
      "2025-01-01",
    );
    await user.type(
      screen.getByLabelText(/filed to/i, { selector: "#news-date-end" }),
      "2025-01-31",
    );
    await user.click(screen.getByRole("button", { name: /ingest news/i }));

    await screen.findByLabelText(/ingestion result/i);
    await waitFor(() => {
      const ingestCall = calls.find((call) => call.url.endsWith("/ingest"));
      expect(JSON.parse(String(ingestCall?.init.body))).toEqual({
        source_type: "news",
        ticker: "TSLA",
        date_range: ["2025-01-01", "2025-01-31"],
        limit: 5,
      });
    });
  });

  it("keeps the spinner accessible during submission (aria-busy)", async () => {
    const user = userEvent.setup();
    let resolveIngest!: (response: Response) => void;
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
      if (url.endsWith("/ingest")) {
        return new Promise<Response>((resolve) => {
          resolveIngest = resolve;
        });
      }
      return undefined;
    });
    render(<SourcesPage />);
    await screen.findByText(/ingest market news/i);

    await user.type(await screen.findByLabelText(/^ticker/i, { selector: "#news-ticker" }), "TSLA");
    await user.click(screen.getByRole("button", { name: /ingest news/i }));

    const busyButton = await screen.findByRole("button", { name: /ingesting…/i });
    expect(busyButton).toBeDisabled();
    expect(busyButton).toHaveAttribute("aria-busy", "true");

    await waitFor(() => expect(typeof resolveIngest).toBe("function"));
    resolveIngest(
      jsonResponse({
        documents_fetched: 3,
        documents_ingested: 3,
        chunks_indexed: 12,
        chunks_truncated_for_metadata: 0,
        documents_failed: 0,
        failures: [],
        embedding_provider: "openai",
        embedding_model: null,
        duration_ms: 2000,
        ok: true,
      }),
    );
    expect(await screen.findByLabelText(/ingestion result/i)).toBeInTheDocument();
  });
});
