import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  askAgentsQuery,
  askQuery,
  BackendTimeoutError,
  BackendUnavailableError,
  getHealth,
  getProviders,
  getReady,
  getSources,
  ingestSource,
  RequestCanceledError,
  userMessage,
} from "@/lib/api";
import {
  errorEnvelope,
  fetchThatHonorsAbort,
  jsonResponse,
  queryResponseFixture,
  stubFetch,
} from "./helpers";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("askQuery", () => {
  it("posts to /query with a JSON body and returns the parsed response", async () => {
    const calls = stubFetch(() => jsonResponse(queryResponseFixture()));

    const response = await askQuery({ question: "What was Apple's revenue in fiscal 2024?" });

    expect(response.answer).toContain("$391,035 million");
    expect(response.citations).toHaveLength(1);
    expect(response.agent_path).toEqual(["classify", "rewrite", "embed", "retrieve", "generate"]);
    expect(calls[0]?.url).toBe("/query");
    expect(calls[0]?.init.method).toBe("POST");
    expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
      question: "What was Apple's revenue in fiscal 2024?",
    });
  });

  it("rejects malformed success payloads that are not an answer shape", async () => {
    stubFetch(() => jsonResponse({ unexpected: true }));
    await expect(askQuery({ question: "hi" })).rejects.toMatchObject({
      name: "ApiError",
      code: "malformed_response",
    });
  });

  it("rejects non-JSON bodies as a malformed response", async () => {
    stubFetch(
      () =>
        new Response("<html>gateway error</html>", {
          status: 200,
          headers: { "content-type": "text/html" },
        }),
    );
    await expect(askQuery({ question: "hi" })).rejects.toMatchObject({
      code: "malformed_response",
    });
  });
});

describe("askAgentsQuery", () => {
  it("targets /agents/query and preserves the forced agent path", async () => {
    const calls = stubFetch(() =>
      jsonResponse(
        queryResponseFixture({
          agent_path: ["classify", "fetch", "extract", "compare", "synthesize"],
        }),
      ),
    );

    const response = await askAgentsQuery({ question: "Compare AAPL and MSFT revenue for FY2024" });

    expect(calls[0]?.url).toBe("/agents/query");
    expect(response.agent_path).toContain("compare");
  });
});

describe("error normalization", () => {
  it.each([
    [503, "no_embedding_provider"],
    [503, "vector_store_not_ready"],
    [503, "no_llm_provider"],
  ])("surfaces %s %s as an ApiError with the backend code", async (status, code) => {
    stubFetch(() => errorEnvelope(status, code, "Not configured."));
    await expect(askQuery({ question: "hi" })).rejects.toMatchObject({
      name: "ApiError",
      status,
      code,
    });
  });

  it("maps the ready-probe 503 not_ready envelope onto the thrown error", async () => {
    stubFetch(() => errorEnvelope(503, "not_ready", "Degraded.", { embedding: false }));
    const error = await getReady().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(503);
    expect((error as ApiError).code).toBe("not_ready");
  });

  it("falls back to a status-based message when the body is not the envelope", async () => {
    stubFetch(() => new Response("", { status: 502 }));
    try {
      await ingestSource({ source_type: "sec_filing", ticker: "AAPL" });
      expect.unreachable("expected rejection");
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError);
      expect((error as ApiError).code).toBe("http_error");
      expect((error as ApiError).message).toMatch(/upstream/i);
    }
  });

  it("wraps fetch TypeErrors as BackendUnavailableError", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    await expect(getSources()).rejects.toBeInstanceOf(BackendUnavailableError);
  });

  it("treats a pre-aborted caller signal as cancellation without calling fetch", async () => {
    const calls = stubFetch(() => jsonResponse(queryResponseFixture()));
    const controller = new AbortController();
    controller.abort();
    await expect(
      askQuery({ question: "hi" }, { signal: controller.signal }),
    ).rejects.toBeInstanceOf(RequestCanceledError);
    expect(calls).toHaveLength(0);
  });

  it("propagates caller aborts mid-flight as RequestCanceledError", async () => {
    stubFetch(fetchThatHonorsAbort());
    const controller = new AbortController();
    const pending = askQuery({ question: "hi" }, { signal: controller.signal });
    queueMicrotask(() => controller.abort());
    await expect(pending).rejects.toBeInstanceOf(RequestCanceledError);
  });
});

describe("timeouts", () => {
  it("aborts the request when the timeout elapses first", async () => {
    vi.useFakeTimers();
    stubFetch(fetchThatHonorsAbort());

    try {
      const pending = askQuery({ question: "hi" }, { timeoutMs: 1_000 }).then(
        (value) => value,
        (error: unknown) => error,
      );
      await vi.advanceTimersByTimeAsync(1_500);
      expect(await pending).toBeInstanceOf(BackendTimeoutError);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("status endpoints", () => {
  it("parses /health liveness probe", async () => {
    stubFetch(() =>
      jsonResponse({
        status: "ok",
        version: "0.1.0-rc1",
        env: "staging",
        commit_sha: "git-a1b2c3d4",
      }),
    );
    const health = await getHealth();
    expect(health).toEqual({
      status: "ok",
      version: "0.1.0-rc1",
      env: "staging",
      commit_sha: "git-a1b2c3d4",
    });
  });

  it("parses /sources availability flags", async () => {
    stubFetch(() => jsonResponse({ sec_edgar: true, news_api: false, apex: false }));
    const sources = await getSources();
    expect(sources).toEqual({ sec_edgar: true, news_api: false, apex: false });
  });

  it("parses /providers chain state", async () => {
    const calls = stubFetch(() =>
      jsonResponse({
        available: ["openai", "ollama"],
        generation_default: "openai",
        embedding_available: true,
        embedding_model: "text-embedding-3-small",
      }),
    );
    const providers = await getProviders();
    expect(providers.available).toEqual(["openai", "ollama"]);
    expect(providers.embedding_model).toBe("text-embedding-3-small");
    expect(calls[0]?.url).toBe("/providers");
  });

  it("parses /ingest summaries including partial failures", async () => {
    const calls = stubFetch(() =>
      jsonResponse({
        documents_fetched: 2,
        documents_ingested: 1,
        chunks_indexed: 42,
        chunks_truncated_for_metadata: 0,
        documents_failed: 1,
        failures: [
          { source_id: "SEC:X:8-K:2024-05-01", stage: "embed", error: "dimension mismatch" },
        ],
        embedding_provider: "openai",
        embedding_model: "text-embedding-3-small",
        duration_ms: 5127.3,
        ok: false,
      }),
    );

    const result = await ingestSource({
      source_type: "sec_filing",
      ticker: "aapl",
      filing_type: "10-K",
      date_range: ["2024-01-01", "2024-12-31"],
      limit: 5,
    });

    expect(result.documents_ingested).toBe(1);
    expect(result.failures[0]?.stage).toBe("embed");
    // Ticker is sent verbatim; uppercasing stays the backend's job.
    expect(JSON.parse(String(calls[0]?.init.body))).toMatchObject({
      source_type: "sec_filing",
      ticker: "aapl",
    });
  });
});

describe("userMessage", () => {
  it("renders curated copy for known degradation codes", () => {
    expect(userMessage(new ApiError(503, "vector_store_not_ready", "raw"))).toMatch(
      /PINECONE_API_KEY/,
    );
    expect(userMessage(new ApiError(503, "not_ready", "raw"))).toMatch(/not fully configured/);
  });

  it("prefers curated copy for known codes and hides long raw text otherwise", () => {
    expect(userMessage(new ApiError(400, "invalid_source", "raw detail"))).toMatch(
      /not registered on the backend/,
    );
    const longRaw = "x".repeat(400);
    expect(userMessage(new ApiError(500, "weird_code", longRaw))).toBe(
      "The request failed. Please try again.",
    );
  });

  it("returns an empty string for cancellations so the UI stays quiet", () => {
    expect(userMessage(new RequestCanceledError())).toBe("");
    expect(userMessage(new BackendUnavailableError())).toMatch(/Could not reach/);
  });
});
