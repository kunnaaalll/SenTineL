# Sentinel Agent Design — Phase 3

How the multi-hop agent team works: routing, the state contract, each node's
guarantees, failure handling, citation discipline, the news adapter, offline
testing strategy, and known limitations.

Companions: `docs/API.md` (endpoint contracts), `docs/ARCHITECTURE.md`
(system overview), `SENTINEL_SPEC.md` sections 5, 6.3, 10, 12.

---

## 1. Routing and the graph

`SentinelQueryService.answer()` (backend `agents/graph.py`) is the single
entry point behind `POST /query` and `POST /agents/query`.

```
classify_query()  — deterministic, zero LLM cost
   │  simple: <2 tickers, no comparison vocabulary, ≤1 period token
   ▼
RagChain.run()            (the Phase 2 path, byte-for-byte untouched)
   agent_path: ["classify", "rewrite", "embed", "retrieve", "generate"]

   │  multi_hop: ≥2 tickers OR comparison vocabulary OR ≥2 period tokens
   │  forced: /agents/query bypasses the verdict, still records "classify"
   ▼
initial_state() ──► fetch ──► extract ──► (compare?) ──► synthesize
   agent_path: ["classify", "fetch", "extract"[, "compare"], "synthesize"]
```

Routing signals (`classify_query`):

- **Tickers** — cashtags (`$MSFT`) plus whole-word matches against the built-in
  ticker set (`AAPL`, `MSFT`, …). Two or more → multi-hop.
- **Comparison vocabulary** — compare/versus/vs./against/"relative to"/
  difference between/year-over-year/over time/across.
- **Periods** — two or more distinct FY/Q/year tokens ("FY2023 Q1", "2023 to
  2024").

The classifier is deliberately heuristic: cheap, debuggable, and impossible to
prompt-inject. The cost of a misroute is bounded — a complex question on the
simple path still gets a cited RAG answer; `/agents/query` is the manual
override.

The conditional edge after extract consults the *data*: `compare` runs only
when extracted facts actually span ≥2 entities or ≥2 periods
(`comparison_warranted`). A single-entity question forced through
`/agents/query` therefore skips the comparison node honestly instead of
producing a one-column fake table.

Loop protection is layered: the topology is acyclic; `graph.invoke` carries
`recursion_limit=25`; every node gets exactly one bounded retry via `_guarded`;
and fetch's live ingestion has its own budget and memory (below). There is no
path by which retries can loop.

## 2. State schema (`agents/state.py`)

`AgentState` is a `TypedDict(total=False)` threaded through every node; nodes
return partial updates that LangGraph merges.

Spec-fixed keys (section 5): `query`, `query_type`, `retrieved_chunks`,
`extracted_facts`, `comparison_table`, `final_answer`, `citations`,
`trace_id`.

Operational extensions:

| Key | Producer | Purpose |
|---|---|---|
| `agent_path` | every node appends | executed steps → `QueryResponse.agent_path`; seeded with `"classify"` |
| `force_agents` | service | `/agents/query` flag |
| `tickers` | fetch planner | planned entities |
| `unavailable_sources` | fetch | human-readable reasons a source contributed nothing (`"news_api: unavailable (missing API key)"`) |
| `node_errors` | `_guarded`, extract | `{node, error, recovered}` records — exception TYPE only, never serialized |
| `ingested_keys` | fetch | `"ticker:source_type"` pairs already live-ingested this run — loop protection |
| `limitations` | all nodes | degradation notes folded into the final answer |
| `trace_urls` | agents | per-agent trace links; best available surfaced as `trace_url` |

`ExtractedFact` (Pydantic, `extra="forbid"`): `entity`, `metric`, `value`
(exact string as reported), `numeric_value` (our conservative parse),
`unit`, `period`, `kind` (`reported|estimate|guidance|qualitative`),
`confidence` (0–1), `statement`, `source_chunk_id`.

## 3. Node guarantees

### Fetch (`fetch_agent.py`)

1. Deterministic plan from the question: tickers, year range → date filter,
   source types (`news` added when news-vocabulary present).
2. **Indexed chunks first**: search the existing store per source type.
3. Live ingestion only for `(ticker, source_type)` combos with zero indexed
   hits — never uncontrolled fetching:
   - budget: `max_live_ingests=2` per query;
   - memory: keys are recorded in `ingested_keys` **before** attempting, so a
     key is tried at most once per run regardless of success;
   - cap: first 2 tickers per source type, small per-ingest limits.
4. Merge SEC + news evidence deduplicated by chunk id, scores preserved,
   capped at 24 chunks for downstream nodes.
5. Unavailable sources (no adapter / missing key / failed ingest) are recorded
   in `unavailable_sources` — partial evidence proceeds, gaps are named later
   in the answer. An embed failure degrades to empty evidence + a limitation
   rather than killing the run.

### Extract (`extract_agent.py`)

One strict `json_mode` call per chunk against a fixed fact schema. Rules the
model cannot break:

- **Provenance is server-forced**: `source_chunk_id` is overwritten with the
  chunk being processed; anything the model supplies is discarded.
- `value` stays verbatim ("$391,035 million"); `numeric_value` comes from our
  local parser ($ signs, commas, accounting negatives, %, scale words) which
  returns `None` rather than guessing scales. We never trust model arithmetic.
- Unknown `kind` degrades to `qualitative`; confidence clamps to [0, 1];
  facts without entity + (value or statement) are rejected as malformed.
- Per-chunk isolation: a malformed reply or provider error skips that chunk
  into `node_errors`.
- **Deterministic floor**: if NO chunk yielded an LLM fact, regex extraction
  produces low-confidence facts so compare/synthesize still have grounded
  material (flagged "structured extraction unavailable; used keyword-level
  facts"). Partial LLM success keeps only its higher-quality facts.

### Compare (`compare_agent.py`)

Pure code, no LLM. Facts group by `(metric, period)` with one cell per entity:

- entities with no fact for a row get an explicit `status="missing"` cell —
  gaps are flagged, never silently omitted;
- same entity/metric/period with different figures → `status="conflict"`;
  textually different but numerically identical variants count as consistent;
- mixed units across a row produce a row note ("units differ across entities
  …") and a global note counting such rows;
- fewer than two entities/periods on input → `warranted=false` with an
  explanation, not a misleading single-column table.

### Synthesize (`synthesize_agent.py`)

Numbered-excerpt prompt containing facts, the comparison table when present,
and every unavailable/limitation note. Discipline:

- inline `[n]` citations are parsed and validated against the real chunk set —
  invented markers are dropped exactly like the simple RAG path;
- an explicit `INSUFFICIENT_EVIDENCE` prefix becomes an honest refusal with no
  citations;
- provider failure falls back to a **deterministic digest** of the extracted
  facts, each bullet citing its own chunk;
- zero evidence skips the LLM call entirely and refuses, naming unavailable
  sources;
- a closing `Limitations:` block surfaces missing/conflicting/stale evidence
  once, deduplicated against what the answer already says.

## 4. Failure handling ladder

Every level prefers a grounded partial answer over an exception:

1. **Per-call**: engine retries transient/rate-limit errors with backoff;
   auth errors fail over providers; invalid requests abort without retry.
2. **Per-node** (`_guarded`): one retry, then the node's degrade function runs
   (fetch → empty evidence + note; extract → deterministic floor; compare →
   no table; synthesize → digest/refusal path) and a `{node, error-type,
   recovered}` record lands in `node_errors`. Exception *messages* stay out of
   state — they can embed prompts or provider payloads.
3. **Framework-level**: if the graph itself throws, the service synthesizes a
   final state; if even that leaves no answer, a last-resort digest of
   whatever evidence survived is returned, or an explicit refusal naming what
   was unavailable.

The API contract holds throughout: 200 with `answer/citations/agent_path/
trace_url`, limitations visible in the answer text; internal diagnostics never
cross the wire.

## 5. Citation guarantees

Identical discipline on both paths, enforced in code (not just prompts):

1. Citations come only from `[n]` markers validated against actually-retrieved
   chunks — out-of-range/duplicate/invented markers are dropped.
2. Facts carry server-forced provenance; digests cite the chunk each fact came
   from.
3. Insufficient evidence ⇒ refusal + zero citations, never a confident guess.
4. Every emitted citation maps 1:1 to a real chunk with source_id, title,
   excerpt, url, score.

## 6. News adapter behavior (`data_sources/news_api.py`)

A `DataSourceAdapter` implementation behind the same interface as SEC EDGAR:

- **Providers**: registry keyed by `NEWS_API_PROVIDER` (default
  `financial_modeling_prep`). Adding one = endpoint + param builder + payload
  parser entry.
- **Queries**: ticker (FMP `tickers=`), keywords (`q=`), date range
  (`from=`/`to=`), page size/pagination up to a hard cap of 10 pages/fetch;
  stops early on empty page, short page, or a provider ignoring the page
  parameter (same first article twice).
- **Normalization**: HTML stripped and entities unescaped without destroying
  financial text; title/url/publisher/author/date preserved into metadata
  (which flows onto vector metadata via the Phase 1 chunker); text sanitized,
  never translated or summarized away.
- **Dedup**: deterministic `source_id = NEWS:{PROVIDER}:{SYMBOL|GENERAL}:
  {sha256(url)[:16]}` makes refetches idempotent; a content hash over
  normalized title + published day collapses syndicated copies.
- **Resilience**: timeout + bounded retries with exponential backoff honoring
  `Retry-After` (seconds or HTTP-date, clamped); **no retry** on auth /
  invalid-request statuses; malformed payloads yield `[]`, never exceptions;
  API keys appear only in request params, never in logs (URLs logged without
  query strings).
- **Availability**: `is_available()` is key-presence-only — false means "not
  configured", so the fetch agent reports it explicitly instead of calling.

## 7. Offline testing strategy

The entire suite (306 tests) runs with no network and scrubbed credentials:

- **ScriptedProvider** plays generation outcomes in sequence (values, raised
  exceptions, repeat-last), covering success/malformed/provider-error paths
  deterministically.
- **FakeVectorStore** is an in-memory cosine store with Pinecone-like
  metadata fitting; tests seed it directly or duplicate results via subclass
  to exercise merge dedup.
- **FakeAdapter / RecordingPipeline** stand in for SEC/news fetching and live
  ingestion; RecordingTracer captures spans/finishes for observability
  assertions.
- Coverage map: news normalization/unavailable-key/retry/timeout/rate-limit/
  malformed/dedup (`test_news_adapter.py`); state helpers, routing, planner,
  each node isolated, guarded retry/degrade, full-graph happy/degraded paths,
  tracing (`test_agents.py`); both routes' API contracts incl. degraded 503s,
  leak hygiene, trace passthrough (`test_api.py`).

## 8. Known limitations

- Routing heuristics can misroute unusual phrasings (mitigation:
  `/agents/query`).
- Live ingestion is intentionally tiny (≤2 ingests × ~5 docs/query); a fresh
  index answers partially and says so.
- News availability is key-presence-only; an expired key surfaces at first
  live call and is reported per-run as an unavailable source.
- Per-agent Langfuse traces are separate traces today, not children of one
  run trace; `trace_url` surfaces the first available link.
- Comparison alignment trusts the extract stage's metric labels; synonyms not
  normalized by the lowercase canonicalizer land in separate rows (visible,
  not silently merged).
- The numeric parser refuses ambiguous scales ("42 widgets", bare "2B" in a
  non-currency context) — those facts carry `numeric_value=null` and are
  excluded from conflict arithmetic rather than mis-scaled.
