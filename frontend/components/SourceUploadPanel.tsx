"use client";

import { FormEvent, useState } from "react";
import {
  ApiError,
  INGEST_TIMEOUT_MS,
  ingestSource,
  IngestionResponse,
  SourceType,
  userMessage,
} from "@/lib/api";

const TICKER_PATTERN = /^[A-Za-z][A-Za-z0-9.\-]{0,5}$/;
const FILING_TYPE_PATTERN = /^[A-Za-z0-9\-]{1,12}$/;

const FILING_TYPES = ["10-K", "10-Q", "8-K", "S-1", "20-F"] as const;

type FieldErrors = Partial<
  Record<"ticker" | "query" | "filingType" | "dateStart" | "dateEnd" | "limit", string>
>;

interface IngestFormState {
  ticker: string;
  query: string;
  filingType: string;
  dateStart: string;
  dateEnd: string;
  limit: string;
}

const EMPTY_FORM: IngestFormState = {
  ticker: "",
  query: "",
  filingType: "",
  dateStart: "",
  dateEnd: "",
  limit: "5",
};

interface SourceUploadPanelProps {
  sourceType: Extract<SourceType, "sec_filing" | "news">;
}

/**
 * Ingestion form for one source type. Validation mirrors the backend's
 * `IngestRequest` constraints (backend/api/schemas.py) so users get instant
 * feedback; the backend remains the authority. The backend contract exposes
 * news ingestion via ticker (its adapter maps keywords internally), so the
 * news variant asks for a ticker and a topic hint is not sent as free text.
 */
export function SourceUploadPanel({ sourceType }: SourceUploadPanelProps) {
  const isSec = sourceType === "sec_filing";
  const [form, setForm] = useState<IngestFormState>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<IngestionResponse | null>(null);
  const [failureMessage, setFailureMessage] = useState<string | null>(null);
  const [failureCode, setFailureCode] = useState<string | null>(null);

  const setField = (field: keyof IngestFormState) => (value: string) => {
    setForm((previous) => ({ ...previous, [field]: value }));
    setErrors((previous) => ({ ...previous, [field]: undefined }));
  };

  /** Mirrors backend IngestRequest validation; returns user-facing messages. */
  const validate = (): FieldErrors => {
    const found: FieldErrors = {};
    const ticker = form.ticker.trim();
    const query = form.query.trim();

    if (!ticker && !query && !isSec) {
      found.ticker = "A ticker is required for news ingestion.";
    }
    if (ticker && !TICKER_PATTERN.test(ticker)) {
      found.ticker = "Use 1–6 characters: letters, digits, dots, or dashes (e.g. AAPL, BRK.B).";
    }
    if (isSec && query && query.length < 2) {
      found.query = "Keyword search needs at least 2 characters.";
    }
    if (isSec && query.length > 500) {
      found.query = "Keep keyword search under 500 characters.";
    }
    if (form.filingType.trim() && !FILING_TYPE_PATTERN.test(form.filingType.trim())) {
      found.filingType = "Filing types are 1–12 letters/digits (e.g. 10-K).";
    }
    for (const field of ["dateStart", "dateEnd"] as const) {
      const value = form[field];
      if (value && Number.isNaN(Date.parse(`${value}T00:00:00Z`))) {
        found[field] = "Use a valid date.";
      }
    }
    if (
      form.dateStart &&
      form.dateEnd &&
      !found.dateStart &&
      !found.dateEnd &&
      form.dateStart > form.dateEnd
    ) {
      found.dateEnd = "End date must be on or after the start date.";
    }
    const limit = Number.parseInt(form.limit, 10);
    if (!Number.isInteger(limit) || limit < 1 || limit > 25) {
      found.limit = "Documents per run must be between 1 and 25.";
    }
    return found;
  };

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setResult(null);
    setFailureMessage(null);
    setFailureCode(null);

    const found = validate();
    setErrors(found);
    if (Object.values(found).some((message) => message)) {
      setFailureMessage("Please fix the highlighted fields before ingesting.");
      return;
    }

    setSubmitting(true);
    try {
      const body: Parameters<typeof ingestSource>[0] = { source_type: sourceType };
      const ticker = form.ticker.trim();
      const query = form.query.trim();
      if (ticker) body.ticker = ticker;
      // Backend contract: SEC filings accept an EDGAR full-text `query`;
      // news ingestion is ticker-driven.
      if (isSec && query) body.query = query;
      const filingType = form.filingType.trim();
      if (filingType) body.filing_type = filingType.toUpperCase();
      if (form.dateStart && form.dateEnd) {
        body.date_range = [form.dateStart, form.dateEnd];
      } else if (form.dateStart) {
        body.date_range = [form.dateStart, new Date().toISOString().slice(0, 10)];
      }
      body.limit = Number.parseInt(form.limit, 10);

      const response = await ingestSource(body, { timeoutMs: INGEST_TIMEOUT_MS });
      setResult(response);
      setForm((previous) => ({ ...previous, filingType: "" }));
    } catch (error) {
      if (error instanceof ApiError) {
        setFailureMessage(userMessage(error));
        setFailureCode(error.code);
      } else {
        setFailureMessage(userMessage(error));
      }
    } finally {
      setSubmitting(false);
    }
  };

  const title = isSec ? "SEC filings" : "Market news";
  const description = isSec
    ? "Fetch filings from SEC EDGAR by ticker or full-text search, then chunk, embed, and index them for research."
    : "Ingest recent market headlines for one ticker. News coverage follows the provider's history window.";

  return (
    <section
      aria-labelledby={`${sourceType}-panel-title`}
      className="rounded-xl border border-line bg-surface p-4 shadow-card sm:p-5"
    >
      <h3 id={`${sourceType}-panel-title`} className="mt-0 mb-1 text-base font-semibold text-ink">
        Ingest {title}
      </h3>
      <p className="mb-4 text-sm leading-relaxed text-ink-faint">{description}</p>

      <form onSubmit={onSubmit} noValidate className="space-y-3">
        <div className={isSec ? "grid gap-3 sm:grid-cols-2" : ""}>
          <TextField
            id={`${sourceType}-ticker`}
            label="Ticker"
            required={!isSec}
            placeholder="AAPL"
            value={form.ticker}
            error={errors.ticker}
            onChange={setField("ticker")}
          />
          {isSec && (
            <div>
              <label
                htmlFor={`${sourceType}-filing-type`}
                className="mb-1 block text-xs font-medium text-ink-soft"
              >
                Filing type
              </label>
              <select
                id={`${sourceType}-filing-type`}
                value={form.filingType}
                onChange={(event) => setField("filingType")(event.target.value)}
                aria-invalid={errors.filingType ? true : undefined}
                aria-describedby={errors.filingType ? `${sourceType}-filing-type-error` : undefined}
                className="block w-full rounded-lg border border-line bg-surface-raised px-3 py-2 text-sm text-ink transition-enabled focus:border-accent"
              >
                <option value="">Any</option>
                {FILING_TYPES.map((type) => (
                  <option key={type} value={type}>
                    {type}
                  </option>
                ))}
              </select>
              {errors.filingType && (
                <FieldError id={`${sourceType}-filing-type-error`} message={errors.filingType} />
              )}
            </div>
          )}
        </div>

        {isSec && (
          <TextField
            id={`${sourceType}-query`}
            label="Full-text search (optional — use instead of a ticker)"
            placeholder='e.g. "data center energy"'
            value={form.query}
            error={errors.query}
            onChange={setField("query")}
          />
        )}

        <div className="grid gap-3 sm:grid-cols-3">
          <TextField
            id={`${sourceType}-date-start`}
            label="Filed from"
            type="date"
            value={form.dateStart}
            error={errors.dateStart}
            onChange={setField("dateStart")}
          />
          <TextField
            id={`${sourceType}-date-end`}
            label="Filed to"
            type="date"
            value={form.dateEnd}
            error={errors.dateEnd}
            onChange={setField("dateEnd")}
          />
          <TextField
            id={`${sourceType}-limit`}
            label="Max documents"
            type="number"
            min={1}
            max={25}
            value={form.limit}
            error={errors.limit}
            onChange={setField("limit")}
          />
        </div>

        {failureMessage && (
          <div
            role="alert"
            className="rounded-lg border border-danger/30 bg-danger-soft px-3 py-2.5"
          >
            <p className="m-0 text-sm font-medium text-danger">{failureMessage}</p>
            {failureCode && failureCode !== "unknown_error" && (
              <p className="mb-0 mt-1 font-mono text-xs text-danger/80">
                error code: {failureCode}
              </p>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting}
          aria-busy={submitting}
          className="inline-flex items-center gap-2 rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-on-accent transition-enabled enabled:hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-60"
        >
          {submitting && (
            <svg
              viewBox="0 0 16 16"
              aria-hidden
              className="h-4 w-4 animate-spin motion-reduce:hidden"
            >
              <circle
                cx="8"
                cy="8"
                r="6"
                stroke="currentColor"
                strokeWidth="2"
                fill="none"
                opacity="0.25"
              />
              <path
                d="M14 8a6 6 0 0 0-6-6"
                stroke="currentColor"
                strokeWidth="2"
                fill="none"
                strokeLinecap="round"
              />
            </svg>
          )}
          {submitting ? "Ingesting…" : isSec ? "Ingest filings" : "Ingest news"}
        </button>
        <p aria-live="polite" role="status" className="sr-only">
          {submitting ? "Ingesting documents. This can take a minute." : ""}
        </p>
      </form>

      {result && (
        <div
          aria-label="Ingestion result"
          className={`mt-4 rounded-lg border px-3 py-3 ${
            result.ok ? "border-success/40 bg-success-soft" : "border-warning/40 bg-warning-soft"
          }`}
        >
          <p className="m-0 flex items-center gap-1.5 text-sm font-semibold text-ink">
            {result.ok ? (
              <>
                <svg viewBox="0 0 16 16" aria-hidden className="h-4 w-4 text-success">
                  <path
                    d="m3 8.5 3.2 3.2L13 5"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    fill="none"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
                Ingested {result.documents_ingested} of {result.documents_fetched} document
                {result.documents_fetched === 1 ? "" : "s"}
              </>
            ) : (
              <>Finished with problems</>
            )}
          </p>
          <dl className="mb-0 mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-soft">
            <Stat label="Chunks indexed" value={result.chunks_indexed.toLocaleString()} />
            {result.chunks_truncated_for_metadata > 0 && (
              <Stat
                label="Truncated for metadata cap"
                value={result.chunks_truncated_for_metadata.toLocaleString()}
              />
            )}
            {result.embedding_provider && (
              <Stat
                label="Embeddings"
                value={`${result.embedding_provider}${result.embedding_model ? ` · ${result.embedding_model}` : ""}`}
              />
            )}
            <Stat label="Duration" value={`${(result.duration_ms / 1000).toFixed(1)}s`} />
          </dl>

          {(result.failures.length > 0 || result.documents_failed > 0) && (
            <div className="mt-3 rounded-md border border-danger/30 bg-danger-soft px-3 py-2">
              <p className="m-0 mb-1.5 text-xs font-semibold uppercase tracking-wide text-danger">
                Failed documents · {result.documents_failed}
              </p>
              <ul className="m-0 list-none space-y-1 p-0 text-xs text-ink-soft">
                {result.failures.map((failure, index) => (
                  <li key={`${failure.source_id}-${index}`} className="font-mono">
                    {failure.source_id || "(unknown source)"} — stage {failure.stage}:{" "}
                    {failure.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center gap-1">
      <dt className="font-medium">{label}</dt>
      <dd className="m-0 font-mono">{value}</dd>
    </div>
  );
}

function FieldError({ id, message }: { id: string; message: string }) {
  return (
    <p id={id} className="mb-0 mt-1 text-xs font-medium text-danger">
      {message}
    </p>
  );
}

interface TextFieldProps {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  type?: "text" | "date" | "number";
  placeholder?: string;
  required?: boolean;
  min?: number;
  max?: number;
}

function TextField({
  id,
  label,
  value,
  onChange,
  error,
  type = "text",
  placeholder,
  required,
  min,
  max,
}: TextFieldProps) {
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-ink-soft">
        {label}
        {required && (
          <span aria-hidden className="ml-0.5 text-danger">
            *
          </span>
        )}
      </label>
      <input
        id={id}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        min={min}
        max={max}
        required={required}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        className={`block w-full rounded-lg border bg-surface-raised px-3 py-2 text-sm text-ink transition-enabled focus:border-accent ${
          error ? "border-danger" : "border-line"
        }`}
      />
      {error && <FieldError id={`${id}-error`} message={error} />}
    </div>
  );
}
