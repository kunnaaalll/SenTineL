/**
 * Typed browser client for the Sentinel backend API (docs/API.md).
 *
 * Design rules:
 * - The base URL is configurable: NEXT_PUBLIC_API_BASE_URL (build time) or
 *   the default relative "/backend", which the Next.js server proxies to
 *   BACKEND_ORIGIN at runtime. Values are public URLs only — no credential
 *   ever appears here, and the browser never calls external providers.
 * - Every request carries a timeout plus caller cancellation via AbortSignal.
 * - All failures normalize into typed errors with user-safe messages
 *   (`userMessage()`); raw envelope text is treated as untrusted and only
 *   surfaced through the curated map below.
 * - 503 / timeout / malformed-response / network-failure states each have a
 *   dedicated error type so the UI can react precisely.
 */

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const RAW_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "";

/** Base URL every request is joined onto; never has a trailing slash. */
export const API_BASE_URL: string = RAW_BASE.trim().replace(/\/+$/, "");

/** Agent-team queries can legitimately take a while; keep generous bounds. */
export const QUERY_TIMEOUT_MS = 90_000;
/** Ingestion fetches, chunks, embeds, and indexes whole filings. */
export const INGEST_TIMEOUT_MS = 300_000;
/** Cheap status probes. */
export const STATUS_TIMEOUT_MS = 10_000;

// ---------------------------------------------------------------------------
// Response models (mirror backend/models/schemas.py + backend/api/schemas.py)
// ---------------------------------------------------------------------------

export interface Citation {
  source_id: string;
  title: string;
  excerpt: string;
  url: string | null;
  chunk_id: string | null;
  score: number | null;
  section: string | null;
  page_or_position: string | null;
}

export interface QueryResponse {
  answer: string;
  citations: Citation[];
  agent_path: string[];
  trace_url: string | null;
}

export interface QueryFilters {
  ticker?: string;
  source_type?: string;
  date_start?: string;
  date_end?: string;
}

export interface QueryRequest {
  question: string;
  top_k?: number;
  filters?: QueryFilters;
}

export interface IngestionFailure {
  source_id: string;
  stage: string;
  error: string;
}

export interface IngestionResponse {
  documents_fetched: number;
  documents_ingested: number;
  chunks_indexed: number;
  chunks_truncated_for_metadata: number;
  documents_failed: number;
  failures: IngestionFailure[];
  embedding_provider: string | null;
  embedding_model: string | null;
  duration_ms: number;
  ok: boolean;
}

export type SourceType = "sec_filing" | "news";

export interface IngestRequest {
  source_type?: SourceType;
  ticker?: string;
  filing_type?: string;
  query?: string;
  date_range?: [string, string];
  limit?: number;
}

export interface SourcesResponse {
  sec_edgar: boolean;
  news_api: boolean;
  apex: boolean;
}

export interface ProvidersResponse {
  available: string[];
  generation_default: string | null;
  embedding_available: boolean;
  embedding_model: string | null;
}

export interface HealthResponse {
  status: string;
  version: string;
  env: string;
  commit_sha?: string;
}

export interface ReadyResponse {
  status: string;
  checks: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

/** The backend answered with its standard JSON error envelope (or none). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/** The caller (or a navigation) aborted the request before completion. */
export class RequestCanceledError extends Error {
  constructor() {
    super("Request canceled.");
    this.name = "RequestCanceledError";
  }
}

/** No response arrived within the configured budget. */
export class BackendTimeoutError extends Error {
  constructor(timeoutMs: number) {
    super(`The backend did not respond within ${Math.round(timeoutMs / 1000)}s.`);
    this.name = "BackendTimeoutError";
  }
}

/** fetch() itself failed — connection refused, DNS, TLS, offline, CORS. */
export class BackendUnavailableError extends Error {
  constructor() {
    super("Could not reach the Sentinel backend.");
    this.name = "BackendUnavailableError";
  }
}

/**
 * Curated, user-safe rendering of any thrown value. Envelope messages from
 * the backend are already written for humans (docs/API.md) and are passed
 * through; everything else maps to actionable copy that leaks nothing about
 * credentials or internals.
 */
const CODE_MESSAGES: Record<string, string> = {
  not_ready:
    "The research backend is running but not fully configured yet — it needs provider credentials and a vector store before questions can be answered.",
  no_embedding_provider:
    "No embedding provider is configured on the backend. Set OPENAI_API_KEY (or an Ollama embedding model) there first.",
  vector_store_not_ready:
    "The vector store is not configured on the backend. Set PINECONE_API_KEY there first.",
  no_llm_provider:
    "No language-model provider is available on the backend, so answers cannot be generated right now.",
  ingestion_unavailable: "The ingestion pipeline is not available on the backend.",
  invalid_source: "That data source is not registered on the backend.",
  source_fetch_failed:
    "The upstream data source could not be reached. It may be rate-limiting or down — try again shortly.",
  validation_error: "The request was rejected as invalid. Check the highlighted fields and retry.",
  internal_error: "An unexpected error occurred inside the backend. Check its logs.",
};

export function userMessage(error: unknown): string {
  if (error instanceof RequestCanceledError) return "";
  if (error instanceof BackendTimeoutError) {
    return "The backend took too long to respond. The request was canceled — try again.";
  }
  if (error instanceof BackendUnavailableError) {
    return "Could not reach the Sentinel backend. Confirm it is running (`make run` or the compose stack) and reachable from this origin.";
  }
  if (error instanceof ApiError) {
    const mapped = CODE_MESSAGES[error.code];
    if (mapped) return mapped;
    // Backend envelope messages are human-facing by contract; anything else
    // falls back to a generic line rather than raw transport detail.
    const text = error.message.trim();
    return text.length > 0 && text.length <= 300 ? text : "The request failed. Please try again.";
  }
  return "Something went wrong. Please try again.";
}

// ---------------------------------------------------------------------------
// Transport core
// ---------------------------------------------------------------------------

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

function assertObject(payload: unknown): void {
  if (payload === null || typeof payload !== "object" || Array.isArray(payload)) {
    throw new ApiError(0, "malformed_response", "The backend returned a malformed response.");
  }
}

function apiErrorFromStatus(status: number): ApiError {
  const fallbacks: Record<number, string> = {
    400: "The request was invalid.",
    404: "The backend endpoint was not found.",
    502: "The backend could not reach an upstream service.",
    503: "The backend is degraded or not ready yet.",
    504: "A gateway timed out talking to the backend.",
  };
  return new ApiError(
    status,
    "http_error",
    fallbacks[status] ?? "The request failed. Please try again.",
  );
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, timeoutMs = STATUS_TIMEOUT_MS } = options;
  if (signal?.aborted) {
    throw new RequestCanceledError();
  }

  // One controller owns the wire: either our timeout fires or the caller's
  // signal aborts — both land as the same underlying abort.
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const abortFromCaller = () => controller.abort();
  signal?.addEventListener("abort", abortFromCaller);

  try {
    let response: Response;
    try {
      response = await fetch(`${API_BASE_URL}${path}`, {
        method,
        headers: body !== undefined ? { "content-type": "application/json" } : undefined,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
        cache: "no-store",
      });
    } catch (transportError: unknown) {
      if (controller.signal.aborted) {
        if (timedOut) throw new BackendTimeoutError(timeoutMs);
        throw new RequestCanceledError();
      }
      // TypeError from fetch covers network/DNS/refused/CORS failures.
      throw new BackendUnavailableError();
    }

    let payload: unknown;
    let bodyUnparseable = false;
    try {
      payload = await response.json();
    } catch {
      // Empty bodies and non-JSON error pages (proxies, gateways) land here.
      bodyUnparseable = true;
    }

    if (!response.ok) {
      if (bodyUnparseable) {
        throw apiErrorFromStatus(response.status);
      }
      // Standard envelope: {"error":{"code","message","details"}}. Anything
      // else degrades to a status-based message.
      if (
        payload !== null &&
        typeof payload === "object" &&
        !Array.isArray(payload) &&
        "error" in payload
      ) {
        const envelope = payload as { error?: { code?: unknown; message?: unknown } };
        const code =
          typeof envelope.error?.code === "string" ? envelope.error.code : "unknown_error";
        const message =
          typeof envelope.error?.message === "string" && envelope.error.message.trim().length > 0
            ? envelope.error.message
            : apiErrorFromStatus(response.status).message;
        throw new ApiError(response.status, code, message);
      }
      throw apiErrorFromStatus(response.status);
    }

    if (bodyUnparseable) {
      throw new ApiError(
        response.status,
        "malformed_response",
        "The backend returned a malformed response (body was not JSON).",
      );
    }

    assertObject(payload);
    return payload as T;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", abortFromCaller);
  }
}

// ---------------------------------------------------------------------------
// Endpoint wrappers
// ---------------------------------------------------------------------------

function assertQueryResponse(value: QueryResponse): QueryResponse {
  if (
    typeof value.answer !== "string" ||
    !Array.isArray(value.citations) ||
    !Array.isArray(value.agent_path)
  ) {
    throw new ApiError(0, "malformed_response", "The backend returned a malformed answer.");
  }
  return value;
}

/** POST /query — automatic simple-vs-multi-hop routing. */
export async function askQuery(
  body: QueryRequest,
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<QueryResponse> {
  return assertQueryResponse(
    await request<QueryResponse>("/query", { method: "POST", body, ...options }),
  );
}

/** POST /agents/query — forced multi-agent path. */
export async function askAgentsQuery(
  body: QueryRequest,
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<QueryResponse> {
  return assertQueryResponse(
    await request<QueryResponse>("/agents/query", { method: "POST", body, ...options }),
  );
}

/** POST /ingest — fetch → chunk → embed → index a batch of documents. */
export async function ingestSource(
  body: IngestRequest,
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<IngestionResponse> {
  const value = await request<IngestionResponse>("/ingest", {
    method: "POST",
    body,
    ...options,
  });
  if (typeof value.documents_ingested !== "number" || typeof value.chunks_indexed !== "number") {
    throw new ApiError(0, "malformed_response", "The backend returned a malformed ingest result.");
  }
  return value;
}

/** GET /sources — which data-source adapters are usable right now. */
export async function getSources(
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<SourcesResponse> {
  return request<SourcesResponse>("/sources", options);
}

/** GET /providers — LLM provider chain state. */
export async function getProviders(
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<ProvidersResponse> {
  return request<ProvidersResponse>("/providers", options);
}

/** GET /health — liveness probe (always 200 while the process lives). */
export async function getHealth(
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<HealthResponse> {
  return request<HealthResponse>("/health", options);
}

/**
 * GET /ready — readiness probe. NOTE: returns 503 with code `not_ready`
 * while the backend lacks providers/vector store; callers that want the
 * degraded detail should catch {@link ApiError} and inspect `status`/`code`.
 */
export async function getReady(
  options: Pick<RequestOptions, "signal" | "timeoutMs"> = {},
): Promise<ReadyResponse> {
  return request<ReadyResponse>("/ready", options);
}
