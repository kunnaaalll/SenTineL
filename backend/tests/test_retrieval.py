"""Phase 1 retrieval tests (spec section 15).

Exercises the VectorStore contract through the Pinecone adapter against an
in-memory fake index — add/search round trip, metadata filtering, namespace
isolation, and the filter-expression translation. No network, no API key.
"""

import math
from datetime import date

import pytest

from models.schemas import Chunk
from retrieval.pinecone_store import PineconeVectorStore, build_pinecone_filter, to_metadata

V_REVENUE = [1.0, 0.1, 0.0]
V_DEBT = [0.0, 1.0, 0.05]
V_RISK = [0.05, 0.0, 1.0]


# --------------------------------------------------------------------------
# Fake Pinecone index (implements only the surface the store uses)
# --------------------------------------------------------------------------


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _matches(expr: dict, metadata: dict) -> bool:
    for key, cond in expr.items():
        value = metadata.get(key)
        if isinstance(cond, dict):
            if "$eq" in cond and value != cond["$eq"]:
                return False
            if "$gte" in cond and (value is None or str(value) < cond["$gte"]):
                return False
            if "$lte" in cond and (value is None or str(value) > cond["$lte"]):
                return False
        elif value != cond:
            return False
    return True


class FakeIndex:
    def __init__(self):
        self.vectors: dict[str, dict[str, tuple[list[float], dict]]] = {}

    def upsert(self, vectors, namespace=""):
        bucket = self.vectors.setdefault(namespace, {})
        for item in vectors:
            bucket[item["id"]] = (item["values"], item["metadata"])

    def query(self, vector, top_k, namespace="", filter=None, include_metadata=False):
        scored: list[dict] = []
        for vec_id, (values, metadata) in self.vectors.get(namespace, {}).items():
            if filter and not _matches(filter, metadata):
                continue
            scored.append({"id": vec_id, "score": _cosine(vector, values), "metadata": metadata})
        scored.sort(key=lambda m: -m["score"])
        return {"matches": scored[:top_k]}


def _store(
    namespace: str = "dev", index: FakeIndex | None = None
) -> tuple[PineconeVectorStore, FakeIndex]:
    index = index or FakeIndex()
    store = PineconeVectorStore(index=index, namespace=namespace)
    return store, index


def _chunk(
    chunk_id: str,
    text: str,
    *,
    ticker="AAPL",
    published="2024-11-01",
    section=None,
    source_type="sec_filing",
    entities=None,
    footnotes=None,
    source_id=None,
) -> Chunk:
    metadata = {"ticker": ticker, "title": f"{ticker} filing"}
    if published:
        metadata["published_date"] = published
    if footnotes:
        metadata["footnotes"] = footnotes
    return Chunk(
        chunk_id=chunk_id,
        source_id=source_id or f"SEC:{ticker}:10-K:{published}",
        source_type=source_type,
        section=section,
        page_or_position=f"chars 0-{len(text)}",
        text=text,
        entities=list(entities or []),
        metadata=metadata,
    )


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_add_search_round_trip():
    store, _ = _store()
    chunks = [
        _chunk(
            "rev-1",
            "Revenue grew across all segments.",
            section="Item 7",
            entities=["AAPL", "FY2024"],
        ),
        _chunk("debt-1", "Total long-term debt obligations increased."),
        _chunk(
            "risk-1", "Risk factors include supply chain exposure.", footnotes=["(1) See Note 12."]
        ),
    ]
    store.add(chunks, [V_REVENUE, V_DEBT, V_RISK])

    results = store.search(V_REVENUE, top_k=2)
    assert len(results) == 2
    top = results[0]
    assert top.chunk_id == "rev-1"
    assert top.score > 0.99
    # full chunk reconstruction from stored metadata
    assert top.text == "Revenue grew across all segments."
    assert top.source_id == "SEC:AAPL:10-K:2024-11-01"
    assert top.section == "Item 7"
    assert top.entities == ["AAPL", "FY2024"]
    assert top.page_or_position.startswith("chars ")
    assert top.metadata["ticker"] == "AAPL"


def test_scores_sorted_descending():
    store, _ = _store()
    store.add(
        [_chunk("a", "revenue"), _chunk("b", "debt"), _chunk("c", "risk")],
        [V_REVENUE, V_DEBT, V_RISK],
    )
    scores = [r.score for r in store.search(V_REVENUE, top_k=3)]
    assert scores == sorted(scores, reverse=True)


def test_section_none_survives_round_trip():
    store, _ = _store()
    store.add([_chunk("nosec", "plain text", section=None)], [[1.0, 0.0, 0.0]])
    assert store.search([1.0, 0.0, 0.0])[0].section is None


def test_footnotes_restored_from_metadata():
    store, _ = _store()
    store.add(
        [_chunk("fn", "text with note", footnotes=["(1) Note twelve details."])], [[1.0, 0.0, 0.0]]
    )
    retrieved = store.search([1.0, 0.0, 0.0])[0]
    assert retrieved.footnotes == ["(1) Note twelve details."]
    # footnotes live IN chunk.metadata per spec section 7 ("attached as chunk
    # metadata"), not as a separate flattened field
    assert retrieved.metadata["footnotes"] == ["(1) Note twelve details."]


# --------------------------------------------------------------------------
# Metadata filtering (spec section 8.2)
# --------------------------------------------------------------------------


def test_ticker_filter_excludes_other_issuers():
    store, _ = _store()
    store.add(
        [
            _chunk("aapl", "apple revenue", ticker="AAPL"),
            _chunk("msft", "microsoft revenue", ticker="MSFT"),
        ],
        [V_REVENUE, V_REVENUE],
    )
    results = store.search(V_REVENUE, top_k=5, filters={"ticker": "AAPL"})
    assert [r.chunk_id for r in results] == ["aapl"]


def test_source_type_filter():
    store, _ = _store()
    store.add(
        [_chunk("filing", "filing text"), _chunk("news", "news text", source_type="news")],
        [V_REVENUE, V_REVENUE],
    )
    results = store.search(V_REVENUE, top_k=5, filters={"source_type": "news"})
    assert [r.chunk_id for r in results] == ["news"]


def test_date_range_filter_inclusive_bounds():
    store, _ = _store()
    store.add(
        [
            _chunk("early", "q1 filing", published="2024-01-05"),
            _chunk("mid", "q2 filing", published="2024-06-01"),
            _chunk("late", "next-year filing", published="2025-02-01"),
        ],
        [V_REVENUE, V_DEBT, V_RISK],
    )
    results = store.search(
        [1.0, 0.0, 0.0], top_k=10, filters={"date_range": ["2024-01-01", "2024-12-31"]}
    )
    assert {r.chunk_id for r in results} == {"early", "mid"}


def test_combined_filters():
    store, _ = _store()
    store.add(
        [_chunk("aapl-filing", "text one"), _chunk("msft-filing", "text two", ticker="MSFT")],
        [V_REVENUE, V_REVENUE],
    )
    results = store.search(
        V_REVENUE, top_k=5, filters={"ticker": "AAPL", "source_type": "sec_filing"}
    )
    assert [r.chunk_id for r in results] == ["aapl-filing"]


# --------------------------------------------------------------------------
# Namespace isolation (spec section 8.2: dev vs prod)
# --------------------------------------------------------------------------


def test_namespace_isolation():
    index = FakeIndex()
    dev_store, _ = _store(namespace="dev", index=index)
    prod_store, _ = _store(namespace="prod", index=index)

    dev_store.add([_chunk("dev-only", "development ingest")], [V_REVENUE])

    assert len(dev_store.search(V_REVENUE)) == 1
    assert prod_store.search(V_REVENUE) == []


# --------------------------------------------------------------------------
# Filter translation + validation
# --------------------------------------------------------------------------


def test_build_pinecone_filter_translation():
    expr = build_pinecone_filter(
        {
            "ticker": "AAPL",
            "source_type": "sec_filing",
            "date_range": ["2024-01-01", date(2024, 12, 31)],
        }
    )
    assert expr == {
        "ticker": {"$eq": "AAPL"},
        "source_type": {"$eq": "sec_filing"},
        "date": {"$gte": "2024-01-01", "$lte": "2024-12-31"},
    }


def test_build_pinecone_filter_empty_is_none():
    assert build_pinecone_filter(None) is None
    assert build_pinecone_filter({}) is None


def test_build_pinecone_filter_partial_date_range():
    assert build_pinecone_filter({"date_range": ["2024-01-01", None]}) == {
        "date": {"$gte": "2024-01-01"}
    }
    assert build_pinecone_filter({"date_range": [None, "2024-06-30"]}) == {
        "date": {"$lte": "2024-06-30"}
    }


def test_unknown_filter_key_raises():
    with pytest.raises(ValueError):
        build_pinecone_filter({"issuer": "AAPL"})


def test_bad_date_range_shape_raises():
    with pytest.raises(ValueError):
        build_pinecone_filter({"date_range": ["2024-01-01"]})


# --------------------------------------------------------------------------
# Hygiene
# --------------------------------------------------------------------------


def test_add_length_mismatch_raises():
    store, _ = _store()
    with pytest.raises(ValueError):
        store.add([_chunk("one", "text"), _chunk("two", "text")], [[1.0, 0.0, 0.0]])


def test_none_metadata_values_stripped_before_upsert():
    bare = Chunk(
        chunk_id="bare", source_id="s", source_type="sec_filing", text="t", entities=[], metadata={}
    )
    stored = to_metadata(bare)
    assert all(v is not None for v in stored.values())
    assert "ticker" not in stored and "date" not in stored
