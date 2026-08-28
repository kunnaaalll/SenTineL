import { vi } from "vitest";

export interface RecordedCall {
  url: string;
  init: RequestInit;
}

type FetchHandler = (url: string, init: RequestInit) => Response | Promise<Response> | undefined;

/**
 * Replace global fetch with a scripted handler and record every call.
 * Handlers returning `undefined` fail loudly — tests never silently hit an
 * unmocked URL. All responses must come from here; nothing touches a network.
 */
export function stubFetch(handler: FetchHandler): RecordedCall[] {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input.toString();
    const recordedInit: RequestInit = init ?? {};
    calls.push({ url, init: recordedInit });
    const response = await handler(url, recordedInit);
    if (response === undefined) {
      throw new Error(`tests: no scripted response for ${url}`);
    }
    return response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export function errorEnvelope(
  status: number,
  code: string,
  message: string,
  details?: unknown,
): Response {
  return jsonResponse(
    { error: { code, message, ...(details !== undefined ? { details } : {}) } },
    status,
  );
}

/**
 * A fetch implementation for cancellation tests: the pending request rejects
 * with the signal's reason as soon as it is aborted — like real fetch().
 */
export function fetchThatHonorsAbort(): (_url: string, init: RequestInit) => Promise<Response> {
  return (_url, init) =>
    new Promise<Response>((_resolve, reject) => {
      const signal = init.signal;
      if (!signal) {
        reject(new Error("tests: expected an AbortSignal"));
        return;
      }
      if (signal.aborted) {
        reject(signal.reason);
        return;
      }
      signal.addEventListener("abort", () => reject(signal.reason), { once: true });
    });
}

// ---------------------------------------------------------------------------
// Fixture payloads mirroring backend contracts (docs/API.md)
// ---------------------------------------------------------------------------

export function citationFixture(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    source_id: "SEC:AAPL:10-K:2024-11-01",
    title: "Apple Inc. 10-K filed 2024-11-01",
    excerpt: "Total net sales were $391,035 million during fiscal 2024.",
    url: "https://www.sec.gov/Archives/edgar/data/320193/example.htm",
    chunk_id: "9f83e2aa10b34d5c",
    score: 0.9134,
    section: "Item 7 - Management's Discussion and Analysis",
    page_or_position: "chars 1200-2100",
    ...overrides,
  };
}

export function queryResponseFixture(overrides: Record<string, unknown> = {}) {
  return {
    answer: "Apple's fiscal 2024 total net sales were $391,035 million [1].",
    citations: [citationFixture()],
    agent_path: ["classify", "rewrite", "embed", "retrieve", "generate"],
    trace_url: null,
    ...overrides,
  };
}
