"""Abstract vector store interface (spec section 8.1).

Same adapter pattern as data_sources/base.py: the pipeline and agents program
against VectorStore; Pinecone is one implementation behind it.
"""

from abc import ABC, abstractmethod

from models.schemas import Chunk, RetrievedChunk


class VectorStore(ABC):
    @abstractmethod
    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Index chunks with their embedding vectors.
        len(chunks) must equal len(vectors)."""

    @abstractmethod
    def search(
        self, query_vector: list[float], top_k: int = 5, filters: dict | None = None
    ) -> list[RetrievedChunk]:
        """kNN search, best matches first.

        filters keys (spec section 8.2): ticker, source_type, date_range —
        e.g. {"ticker": "AAPL", "source_type": "sec_filing",
              "date_range": ["2024-01-01", "2024-12-31"]}"""

    @abstractmethod
    def delete_source(self, source_id: str) -> None:
        """Remove every vector belonging to `source_id`.

        Backs the ingestion pipeline's delete-before-reingest policy (audit
        risk #2): re-ingesting a document whose chunk boundaries shifted must
        not leave stale vectors orphaned alongside new ones."""

    def is_ready(self) -> bool:
        """True when the store can accept reads/writes right now. Default True;
        backends with external credentials override this."""
        return True
