"""Pinecone-backed VectorStore (spec sections 8.1-8.2).

- One index; dev/prod isolation via namespaces derived from SENTINEL_ENV
  (settings.namespace).
- Per-vector metadata: text, source_id, source_type, section, ticker,
  date (ISO string — lexicographic order == chronological, so $gte/$lte
  range filters work on strings), page_or_position, entities, footnotes,
  title. None values are stripped (Pinecone rejects them).
- Metadata is size-capped below Pinecone's documented ~40KB per-vector limit
  (`fit_metadata`): oversized payloads first drop footnotes, then truncate the
  chunk text with an explicit marker. The ingestion pipeline reports when
  this fires.
- delete_source() implements delete-before-reingest by source_id.
- The Pinecone Index object is injectable (`index=`) so tests can run the
  full add/search path against an in-memory fake without network access.
"""

import json
from copy import deepcopy
from datetime import date

from config.settings import Settings, get_settings
from models.schemas import Chunk, RetrievedChunk
from retrieval.base import VectorStore

# Pinecone documents a ~40KB per-vector metadata cap; we enforce a default
# ceiling with headroom so JSON serialization overhead can't tip us over.
PINECONE_METADATA_LIMIT_BYTES = 40_000
DEFAULT_METADATA_CAP_BYTES = 38_000

# Metadata keys flattened out of Chunk.metadata into top-level filterable fields.
_FLATTENED_KEYS = {
    "text",
    "source_id",
    "source_type",
    "section",
    "page_or_position",
    "entities",
    "footnotes",
}
_FILTER_KEYS = {"ticker", "source_type", "date_range"}


def _iso(value) -> str:
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def build_pinecone_filter(filters: dict | None) -> dict | None:
    """Translate canonical filters into a Pinecone filter expression."""
    if not filters:
        return None
    unknown = set(filters) - _FILTER_KEYS
    if unknown:
        raise ValueError(
            f"Unsupported filter keys: {sorted(unknown)}; supported: {sorted(_FILTER_KEYS)}"
        )
    expr: dict = {}
    if filters.get("ticker"):
        expr["ticker"] = {"$eq": str(filters["ticker"])}
    if filters.get("source_type"):
        expr["source_type"] = {"$eq": str(filters["source_type"])}
    date_range = filters.get("date_range")
    if date_range:
        if len(date_range) != 2:
            raise ValueError("date_range must be [start, end]")
        cond: dict[str, str] = {}
        if date_range[0]:
            cond["$gte"] = _iso(date_range[0])
        if date_range[1]:
            cond["$lte"] = _iso(date_range[1])
        if cond:
            expr["date"] = cond
    return expr or None


def to_metadata(chunk: Chunk) -> dict:
    metadata = {
        "text": chunk.text,
        "source_id": chunk.source_id,
        "source_type": chunk.source_type,
        "section": chunk.section or "",
        "page_or_position": chunk.page_or_position,
        "entities": list(chunk.entities),
        "footnotes": list(chunk.footnotes),
        "title": chunk.metadata.get("title"),
        "ticker": chunk.metadata.get("ticker"),
        "cik": chunk.metadata.get("cik"),
        "accession_number": chunk.metadata.get("accession_number"),
        "url": chunk.metadata.get("url"),
    }
    raw_date = chunk.metadata.get("date") or chunk.metadata.get("published_date")
    if raw_date:
        metadata["date"] = _iso(raw_date)
    # Pinecone rejects None-valued metadata keys and enforces a per-vector size
    # cap (~40KB); drop Nones here. Table chunks keep their text whole per spec
    # section 7 — realistically well under the cap.
    return {k: v for k, v in metadata.items() if v is not None}


def from_metadata(chunk_id: str, score: float, metadata: dict) -> RetrievedChunk:
    passthrough = {k: v for k, v in metadata.items() if k not in _FLATTENED_KEYS}
    passthrough["footnotes"] = list(metadata.get("footnotes", []))
    return RetrievedChunk(
        chunk_id=chunk_id,
        source_id=metadata.get("source_id", ""),
        source_type=metadata.get("source_type", ""),
        section=metadata.get("section") or None,
        page_or_position=metadata.get("page_or_position", ""),
        text=metadata.get("text", ""),
        entities=list(metadata.get("entities", [])),
        metadata=passthrough,
        score=float(score),
    )


# --------------------------------------------------------------------------
# Metadata size guard (audit risk #3: Pinecone ~40KB per-vector cap)
# --------------------------------------------------------------------------

_TRUNCATION_MARKER = " …[truncated]"


def metadata_size_bytes(metadata: dict) -> int:
    """Serialized JSON size Pinecone would store (UTF-8 bytes)."""
    return len(json.dumps(metadata, ensure_ascii=False, default=str).encode("utf-8"))


def fit_metadata(metadata: dict, max_bytes: int = DEFAULT_METADATA_CAP_BYTES) -> tuple[dict, bool]:
    """Shrink `metadata` under max_bytes; returns (fitted, was_truncated).

    Reduction order preserves what retrieval needs most:
    1. drop footnotes (nice-to-have context, recoverable from the source)
    2. truncate the chunk text with an explicit marker
    3. truncate any remaining oversized string values
    4. last resort: drop text entirely rather than fail the upsert

    The original dict is never mutated.
    """
    if metadata_size_bytes(metadata) <= max_bytes:
        return metadata, False

    fitted = deepcopy(metadata)
    fitted.pop("footnotes", None)

    def oversize() -> int:
        return metadata_size_bytes(fitted) - max_bytes

    # 2. Truncate chunk text. Budget math is approximate for multi-byte text,
    # so shrink 10% past the computed target and loop until it fits.
    text = fitted.get("text")
    if isinstance(text, str):
        while True:
            overshoot = oversize()
            if overshoot <= 0 or not text:
                break
            keep_chars = len(text.encode("utf-8")) - overshoot - len(_TRUNCATION_MARKER.encode())
            keep_chars = int(keep_chars * 0.9)
            if keep_chars <= 0:
                fitted.pop("text", None)
                break
            text = text[:keep_chars].rstrip() + _TRUNCATION_MARKER
            fitted["text"] = text
    if metadata_size_bytes(fitted) <= max_bytes:
        return fitted, True

    # 3. Cap any remaining long string values at 1000 chars.
    for key, value in list(fitted.items()):
        if isinstance(value, str) and len(value) > 1000:
            fitted[key] = value[:1000] + _TRUNCATION_MARKER
            if metadata_size_bytes(fitted) <= max_bytes:
                return fitted, True
    if metadata_size_bytes(fitted) <= max_bytes:
        return fitted, True

    # 4. Give up on text entirely — the upsert must succeed even for a
    # pathologically large non-text field.
    fitted.pop("text", None)
    if metadata_size_bytes(fitted) <= max_bytes:
        return fitted, True
    raise ValueError(
        f"Vector metadata cannot be reduced under {max_bytes} bytes "
        f"(currently {metadata_size_bytes(fitted)}); refusing to upsert"
    )


def _field(obj, name, default=None):
    """Read `name` from either a dict or an SDK response object."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class PineconeVectorStore(VectorStore):
    def __init__(
        self,
        *,
        api_key: str | None = None,
        index_name: str | None = None,
        namespace: str | None = None,
        dimension: int | None = None,
        index=None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.api_key = api_key or self.settings.pinecone_api_key
        self.index_name = index_name or self.settings.pinecone_index_name
        self.namespace = namespace or self.settings.namespace  # dev/prod isolation
        self.dimension = dimension or self.settings.embedding_dimension
        self._index = index  # injectable for tests / alternate backends

    @property
    def index(self):
        if self._index is None:
            from pinecone import Pinecone  # deferred: heavy import, only needed live

            if not self.api_key:
                raise RuntimeError(
                    "PINECONE_API_KEY is not set; configure it in .env to use "
                    "PineconeVectorStore against the real service."
                )
            self._index = Pinecone(api_key=self.api_key).Index(self.index_name)
        return self._index

    def ensure_index(self) -> None:
        """Provision the index (serverless) if it doesn't exist yet. Idempotent."""
        from pinecone import Pinecone, ServerlessSpec

        if not self.api_key:
            raise RuntimeError("PINECONE_API_KEY is not set; cannot ensure index.")
        pc = Pinecone(api_key=self.api_key)
        existing = {i["name"] for i in pc.list_indexes()}
        if self.index_name not in existing:
            pc.create_index(
                name=self.index_name,
                dimension=self.dimension,
                metric="cosine",
                spec=ServerlessSpec(
                    cloud=self.settings.pinecone_cloud, region=self.settings.pinecone_region
                ),
            )

    def is_ready(self) -> bool:
        """True when a real upsert/search could proceed (key present or an
        index was injected). Used by the API's readiness check and by routes
        that must fail fast with 503 instead of mid-pipeline errors."""
        return bool(self.api_key or self._index is not None)

    # -- VectorStore interface ---------------------------------------------------

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(
                f"chunks/vectors length mismatch: {len(chunks)} chunks vs {len(vectors)} vectors"
            )
        cap = min(self.settings.pinecone_metadata_cap_bytes, PINECONE_METADATA_LIMIT_BYTES)
        items = []
        for c, v in zip(chunks, vectors, strict=True):
            metadata, _ = fit_metadata(to_metadata(c), cap)
            items.append({"id": c.chunk_id, "values": v, "metadata": metadata})
        self.index.upsert(vectors=items, namespace=self.namespace)

    def delete_source(self, source_id: str) -> None:
        """Drop every vector of `source_id` (delete-before-reingest policy)."""
        self.index.delete(
            delete_all=True,
            namespace=self.namespace,
            filter={"source_id": {"$eq": source_id}},
        )

    def search(
        self, query_vector: list[float], top_k: int = 5, filters: dict | None = None
    ) -> list[RetrievedChunk]:
        response = self.index.query(
            vector=query_vector,
            top_k=top_k,
            namespace=self.namespace,
            filter=build_pinecone_filter(filters),
            include_metadata=True,
        )
        matches = (
            response.get("matches", [])
            if isinstance(response, dict)
            else getattr(response, "matches", [])
        )
        results = []
        for match in matches:
            results.append(
                from_metadata(
                    chunk_id=_field(match, "id", ""),
                    score=_field(match, "score", 0.0),
                    metadata=_field(match, "metadata", {}) or {},
                )
            )
        return results
