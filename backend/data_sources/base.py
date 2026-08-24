"""Abstract data source interface (spec section 6.1).

Every adapter — SEC EDGAR, news providers, the optional APEX bridge —
implements this exact surface. The ingestion pipeline and agents talk only to
DataSourceAdapter, never to a concrete source.
"""

from abc import ABC, abstractmethod

from models.schemas import RawDocument


class DataSourceAdapter(ABC):
    """One external content source, normalized to RawDocuments."""

    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """True if this source is usable right now (key present, endpoint up).

        Must never raise — availability checks degrade gracefully so the
        pipeline continues without a source (spec section 6.3)."""

    @abstractmethod
    def fetch(self, query_params: dict) -> list[RawDocument]:
        """Fetch documents matching query_params.

        query_params shape is adapter-specific, e.g. {ticker, filing_type,
        date_range} for SEC EDGAR (spec section 6.2)."""
