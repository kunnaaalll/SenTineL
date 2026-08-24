# Sentinel — Production Audit (Phase 0)

**Date:** 2026-08-23
**Scope:** Baseline audit of the repository against `SENTINEL_SPEC.md`, plus the Phase 0 foundation work (tooling, CI, dependency strategy) that follows from it.
**Method:** Full read of every source/test/config file; secret scan; offline test-suite run; live read-only verification of the EDGAR full-text-search endpoint shape.

> **Phase 0 outcome (same day):** items P0-2, H-2, H-3, M-2 and part of H-1 were implemented immediately — root `pyproject.toml` (ruff format+lint, mypy, pytest config), `backend/requirements-dev.txt` + pinned `requirements-lock.txt`, `.pre-commit-config.yaml`, GitHub Actions CI (`.github/workflows/ci.yml`), Makefile gates (`fmt/lint/typecheck/check/lock/hooks`), README setup docs, hardened `.gitignore`, `backend/__init__.py` removed (source-root semantics), plus the `select_filings` ragged-array fix with regression test. Gate status: ruff format ✓ clean · ruff check ✓ clean · mypy ✓ no issues · pytest ✓ **48 passed** offline. Remaining blockers below are unchanged otherwise.

---

## 1. Current implementation status

**Phase 1 (data layer, backend only) is implemented and passing.**

| Area | Status | Files |
|---|---|---|
| Data source interface | ✅ Done | `backend/data_sources/base.py` |
| SEC EDGAR adapter | ✅ Done | `backend/data_sources/sec_edgar.py` |
| Financial chunker | ✅ Done | `backend/ingestion/financial_chunker.py` |
| Vector store interface | ✅ Done | `backend/retrieval/base.py` |
| Pinecone store | ✅ Done | `backend/retrieval/pinecone_store.py` |
| Config / settings | ✅ Done | `backend/config/settings.py`, `adapters.yaml` |
| Shared schemas | ✅ Done (Phase-1 subset) | `backend/models/schemas.py` |
| Tests (ingestion, retrieval) | ✅ 47/47 pass offline, ~0.18s, zero network | `backend/tests/` |
| LLM providers, chains, agents, API, observability | ⬜ Not started (Phases 2–4) | `.gitkeep` placeholders only |

Test baseline at audit time: **47 passed**, fully offline (fake HTTP session for EDGAR, in-memory fake index for Pinecone). No secrets are present anywhere in the repo (scanned; only empty template values in `.env.example`).

## 2. Specification-to-code gap analysis

Consistent with the phased plan (`SENTINEL_SPEC.md` §16):

- Spec §6.1/§6.2 (adapter ABC + SEC EDGAR) — matches. Rate-limit throttle (~8 req/s), descriptive User-Agent, ticker→CIK resolution, submissions filtering, full-text-search path all present. The EFTS endpoint URL and response shape were verified against the live API during this audit and are compatible.
- Spec §7 step 1–2 (fetch + chunk) — done. Steps 3–5 (entity extraction, embedding, store orchestration) intentionally deferred: they depend on the LLM provider layer (Phase 2). `Chunk.entities` stays empty by design; `ingestion/entity_extractor.py` and `ingestion/pipeline.py` do not exist yet. **Sequencing decision to confirm** (see §8).
- Spec §8 (Pinecone) — matches: namespace-per-environment, metadata filters (ticker / source_type / date range), dimension 1536, injectable index for offline tests.
- Spec §5 schemas — `RawDocument`/`Chunk`/`RetrievedChunk` match with two documented deviations: `Chunk.metadata` carries footnotes + document-level fields (required by spec §7's footnote rule), and `RetrievedChunk.score` defaults to `0.0`. `AgentState`/`QueryResponse` deferred to agent phases.
- Spec §15 tests — `test_ingestion.py` and `test_retrieval.py` exist and cover the required behaviors (tables atomic, overlap, footnotes-as-metadata, round trip, filtering, namespaces). `test_agents.py`/`test_api.py` arrive with their phases.
- Spec §4 layout — mirrors the spec; empty dirs are placeholders, not gaps.

## 3. Architecture risks

1. **Import layout is coupled to a `sys.path` hack.** Modules import absolutely (`from models.schemas import …`) and only `tests/conftest.py` puts `backend/` on the path. The stray empty `backend/__init__.py` makes `backend` both a package *and* a source root — under `uvicorn`/Docker this breaks without an explicit `PYTHONPATH`, and it confuses type checkers/packagers that derive two module names for one file. *(Addressed in Phase 0: `backend/__init__.py` removed; source-root semantics made explicit in tooling config. A full src-layout refactor is deliberately NOT done now — no evidence of breakage yet, and imports would churn across every file.)*
2. **Deterministic chunk IDs vs. re-ingestion drift.** IDs hash `(source_id, section, ordinal)`; if section labels or block boundaries shift between filings/versions, old vectors survive as orphans alongside new ones. Needs a delete-by-source or namespace-wipe policy when the ingestion pipeline lands (Phase 2).
3. **Pinecone per-vector metadata cap (~40KB)** — full chunk text rides in metadata, and table chunks are atomic by design. A giant table can exceed the cap at upsert time. Add a size guard/truncation policy when embeddings land.
4. **No HTTP retry/backoff layer.** SEC enforces rate limits aggressively (403 bans on abuse) and returns `Retry-After`; the adapter self-throttles but has no retry on transient failure and does not honor `Retry-After`.
5. `get_settings()` is `lru_cache`d — env changes mid-process are invisible. Acceptable for CLI/API startup; noted so future tests don't fight it.

Minor correctness issues found (no behavior change made in Phase 0):
- `_table_to_markdown` iterates `find_all("tr")`, which includes rows of nested tables → nested content appears both inside its parent cell and again as extra rows (content duplication, edge case).
- `select_filings()` indexes parallel arrays positionally and would raise `IndexError` if EDGAR ever returns ragged arrays.
- EFTS queries are force-wrapped in quotes (`q="…"`), turning every search into exact phrase matching.
- `${VAR}` expansion in `adapters.yaml` substitutes missing env vars with `""` silently — misconfiguration is masked.

## 4. Security risks

- ✅ No committed secrets; `.env` ignored; TLS verification untouched; `yaml.safe_load` used; no eval/exec/pickle.
- ⚠️ Default `SEC_CONTACT_EMAIL=sentinel-operator@example.com` — SEC requires a genuine contact address; leaving the placeholder in production traffic violates SEC fair-access policy and risks IP bans.
- ⚠️ Future FastAPI layer will have no auth (explicit v1 non-goal) — must never be exposed publicly; bind to localhost/private network until auth exists.
- ℹ️ Filing HTML parsed with BeautifulSoup `html.parser` for text extraction only — no execution context, low risk.
- ⚠️ Until git is initialized, `.gitignore` protection for `.env` is theoretical — verify with `git status` immediately after init.

## 5. Testing gaps

- No tests for `config.settings` (env precedence, `${VAR}` expansion, `enabled_adapters`).
- No tests for the no-API-key guard on `PineconeVectorStore.index` or `ensure_index` idempotency (needs a fake PC client).
- Untested helpers: `_cik_to_ticker` reverse mapping, `pack_sentences` long-sentence wrap path, `_overlap_tail` boundary cases.
- No coverage measurement configured.
- `test_agents.py` / `test_api.py` — pending later phases (per plan).

## 6. Deployment gaps

- **No Git repository initialized** — no history, no branching, nothing for CI/hooks to attach to. This is the single biggest production blocker.
- **No dependency lock** — `requirements.txt` uses ranges; two installs today can differ. Irreproducible builds/deploys.
- No CI pipeline; no formatting/lint/type gates.
- Dockerfiles/compose/Terraform absent — acceptable (spec Phase 5), listed for completeness. When written, the import-layout decision (risk #1) determines `PYTHONPATH` handling.
- Python minor drift: venv is 3.11.14, system python3.11 is 3.11.16. Lock + CI pinning resolves this.

## 7. Recommended implementation order

1. **Phase 0 (this task):** root `pyproject.toml` (ruff format+lint, mypy, pytest), dev requirements, pinned lock file, pre-commit config, GitHub Actions CI, Makefile targets, README setup docs, `.gitignore` hardening, remove `backend/__init__.py`.
2. **User action:** `git init` + first commit + push to remote (host TBD, assumed GitHub private).
3. **Phase 2:** LLM provider layer (`base`/`engine`/openai/ollama) + naive RAG chain + `/query` API + ingestion pipeline orchestrator + entity extractor + Langfuse wrapper skeleton. Fold in: HTTP retry/backoff with `Retry-After`, Pinecone metadata-size guard, delete-before-reingest policy.
4. Phases 3–6 per spec.

## 8. Explicit decisions requiring confirmation

1. **Git hosting:** initialize git locally now? Assume GitHub (private) as remote since CI is GitHub Actions — confirm.
2. **Dependency lock style:** single pip-freeze lockfile covering runtime+dev (`requirements-lock.txt`) — confirm, or switch to pip-tools/uv workflow.
3. **Entity-extractor timing:** confirm it lands in Phase 2 (with the LLM layer it depends on), not as a standalone Phase 1 addendum.
4. **Import layout:** keep source-root style (with explicit tooling/PYTHONPATH support) through Phase 4; revisit src-layout at containerization (Phase 5) — confirm.
5. **SEC contact email:** provide the real address for `.env` before any live EDGAR usage.

## 9. Prioritized blockers

| Priority | Item |
|---|---|
| **Critical** | P0-1 No VCS initialized — blocks history, backup, CI, hooks. |
| **Critical** | P0-2 Unpinned dependencies — irreproducible builds/deploys. |
| **High** | H-1 Import/sys.path coupling + `backend/__init__.py` dual-naming hazard (breaks containerization). |
| **High** | H-2 No CI running tests/lint/types. |
| **High** | H-3 No formatting/lint/type gates; no coverage measurement. |
| **High** | H-4 No HTTP retry/backoff or `Retry-After` handling (SEC ban risk); Pinecone 40KB metadata-cap exposure. |
| **Medium** | M-1 Nested-table duplication bug in `_table_to_markdown`. |
| **Medium** | M-2 `select_filings` ragged-array `IndexError` risk. |
| **Medium** | M-3 EFTS phrase-quote forcing; `${VAR}` silent-empty expansion. |
| **Medium** | M-4 Missing settings/adapters-config unit tests. |
| **Low** | L-1 Placeholder SEC contact email default. |
| **Low** | L-2 `RetrievedChunk.score` default deviation; docstring drift on nested tables; optional lxml parser perf. |

*(H-1 partially addressed in Phase 0; full resolution deferred to containerization.)*
