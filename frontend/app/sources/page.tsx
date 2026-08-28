"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  getProviders,
  getReady,
  getSources,
  ProvidersResponse,
  ReadyResponse,
  SourcesResponse,
  STATUS_TIMEOUT_MS,
  userMessage,
} from "@/lib/api";
import { SourceUploadPanel } from "@/components/SourceUploadPanel";
import { CheckIcon, WarningIcon } from "@/components/StatusBar";

interface StatusState {
  sources: SourcesResponse | null;
  providers: ProvidersResponse | null;
  ready: ReadyResponse | null;
  /** Set when the status probes themselves failed (backend unreachable). */
  loadError: string | null;
}

/**
 * Data-source management: live availability of every adapter and LLM
 * provider, backend readiness, and the ingestion forms. All state comes
 * from the backend on load; when the backend is unreachable the page says
 * so explicitly instead of rendering empty shells.
 */
export default function SourcesPage() {
  const [status, setStatus] = useState<StatusState>({
    sources: null,
    providers: null,
    ready: null,
    loadError: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    const opts = { signal: controller.signal, timeoutMs: STATUS_TIMEOUT_MS };

    // allSettled: one failing probe must not blank out the others.
    void Promise.allSettled([getSources(opts), getProviders(opts), getReady(opts)]).then(
      ([sources, providers, ready]) => {
        if (controller.signal.aborted) return;
        setStatus({
          sources: sources.status === "fulfilled" ? sources.value : null,
          providers: providers.status === "fulfilled" ? providers.value : null,
          ready: ready.status === "fulfilled" ? ready.value : null,
          loadError:
            sources.status === "rejected" &&
            providers.status === "rejected" &&
            ready.status === "rejected"
              ? userMessage(
                  // Prefer the most specific of the three failures.
                  [sources.reason, providers.reason, ready.reason].find(
                    (reason) => reason instanceof ApiError,
                  ) ?? sources.reason,
                )
              : null,
        });
      },
    );
    return () => controller.abort();
  }, []);

  const newsAvailable = status.sources?.news_api ?? false;
  const secAvailable = status.sources?.sec_edgar ?? false;

  const failingChecks = status.ready
    ? Object.entries(status.ready.checks)
        .filter(([, value]) => !value)
        .map(([key]) => key)
    : [];

  return (
    <section aria-label="Data sources" className="space-y-6">
      <header>
        <h1 className="mt-0 mb-1 text-xl font-semibold tracking-tight text-ink">Sources</h1>
        <p className="m-0 max-w-2xl text-sm leading-relaxed text-ink-soft">
          Check what Sentinel can reach right now, then pull documents into the research corpus.
          Ingestion runs server-side; this page never touches provider credentials.
        </p>
      </header>

      {status.loadError && (
        <div role="alert" className="rounded-lg border border-danger/30 bg-danger-soft px-4 py-3">
          <p className="m-0 flex items-start gap-2 text-sm font-medium text-danger">
            <WarningIcon className="mt-0.5 h-4 w-4 shrink-0" />
            Cannot reach the Sentinel backend, so availability cannot be checked.
          </p>
          <p className="mb-0 mt-1 pl-6 text-xs text-ink-soft">{status.loadError}</p>
        </div>
      )}

      <div aria-label="Availability" className="grid gap-3 sm:grid-cols-3">
        <AvailabilityCard
          name="SEC EDGAR"
          available={status.sources ? secAvailable : null}
          detail={
            status.sources
              ? secAvailable
                ? "Public filings. No API key needed."
                : "Currently unavailable on the backend."
              : "Unknown until the backend responds."
          }
        />
        <AvailabilityCard
          name="Market news"
          available={status.sources ? newsAvailable : null}
          detail={
            status.sources
              ? newsAvailable
                ? "Provider key configured on the backend."
                : "Needs NEWS_API_KEY configured on the backend."
              : "Unknown until the backend responds."
          }
        />
        <AvailabilityCard
          name="APEX adapter"
          available={status.sources ? status.sources.apex : false}
          detail={
            status.sources && status.sources.apex
              ? "Optional portfolio source enabled."
              : "Optional adapter — disabled by design."
          }
        />
      </div>

      {(status.providers || status.ready) && (
        <div aria-label="Backend capabilities" className="grid gap-3 sm:grid-cols-2">
          {status.providers && (
            <div className="rounded-lg border border-line bg-surface p-3.5 shadow-card">
              <h2 className="mt-0 mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                LLM providers
              </h2>
              <ul className="m-0 mb-2 list-none p-0">
                {status.providers.available.length === 0 ? (
                  <li className="text-sm text-ink-faint">None configured.</li>
                ) : (
                  status.providers.available.map((provider) => (
                    <li
                      key={provider}
                      className="flex items-center gap-1.5 py-0.5 text-sm text-ink"
                    >
                      <CheckIcon className="h-3.5 w-3.5 text-success" />
                      <span className="font-mono">{provider}</span>
                      {provider === status.providers?.generation_default && (
                        <span className="rounded bg-surface-muted px-1.5 py-0.5 text-[11px] font-medium text-ink-soft">
                          default for generation
                        </span>
                      )}
                    </li>
                  ))
                )}
              </ul>
              <p className="m-0 text-xs text-ink-soft">
                {status.providers.embedding_available ? (
                  <>
                    Embeddings via{" "}
                    <span className="font-mono">
                      {status.providers.embedding_model ?? "configured model"}
                    </span>
                  </>
                ) : (
                  "No embedding provider — queries and ingestion are disabled."
                )}
              </p>
            </div>
          )}

          {status.ready && (
            <div className="rounded-lg border border-line bg-surface p-3.5 shadow-card">
              <h2 className="mt-0 mb-2 text-xs font-semibold uppercase tracking-wide text-ink-faint">
                Backend readiness
              </h2>
              <p className="m-0 flex items-start gap-1.5 text-sm">
                {status.ready.status === "ready" ? (
                  <>
                    <CheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                    <span className="font-medium text-success">Ready</span>
                  </>
                ) : (
                  <>
                    <WarningIcon className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                    <span className="font-medium text-warning">Degraded</span>
                  </>
                )}
                <span className="text-xs text-ink-soft">
                  {status.ready.status === "ready"
                    ? "— questions and ingestion are fully served."
                    : `— waiting on: ${failingChecks.length > 0 ? failingChecks.join(", ") : "configuration"}.`}
                </span>
              </p>
            </div>
          )}
        </div>
      )}

      <div
        aria-label="Ingest new documents"
        className={`grid gap-4 ${newsAvailable ? "lg:grid-cols-2" : ""}`}
      >
        <SourceUploadPanel sourceType="sec_filing" />
        <div className={newsAvailable ? undefined : "relative"}>
          <SourceUploadPanel sourceType="news" />
          {!newsAvailable && !status.loadError && (
            <div
              aria-hidden={false}
              role="note"
              className="absolute inset-x-0 bottom-0 rounded-b-xl border-t border-warning/40 bg-warning-soft px-4 py-3 sm:inset-0 sm:flex sm:items-end"
            >
              <p className="m-0 text-sm font-medium text-warning-ink">
                News ingestion is unavailable — the backend has no NEWS_API_KEY configured. SEC
                ingestion is unaffected.
              </p>
            </div>
          )}
        </div>
      </div>
    </section>
  );
}

function AvailabilityCard({
  name,
  available,
  detail,
}: {
  name: string;
  available: boolean | null;
  detail: string;
}) {
  return (
    <div className="rounded-lg border border-line bg-surface p-3.5 shadow-card">
      <div className="flex items-center justify-between gap-2">
        <h2 className="m-0 text-sm font-semibold text-ink">{name}</h2>
        {available === null ? (
          <span className="rounded bg-surface-muted px-1.5 py-0.5 text-[11px] font-medium text-ink-faint">
            Checking…
          </span>
        ) : available ? (
          <span className="inline-flex items-center gap-1 rounded bg-success-soft px-1.5 py-0.5 text-[11px] font-semibold text-success">
            <CheckIcon className="h-3 w-3" />
            Available
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded bg-warning-soft px-1.5 py-0.5 text-[11px] font-semibold text-warning">
            <WarningIcon className="h-3 w-3" />
            Unavailable
          </span>
        )}
      </div>
      <p className="mb-0 mt-2 text-xs leading-relaxed text-ink-faint">{detail}</p>
    </div>
  );
}
