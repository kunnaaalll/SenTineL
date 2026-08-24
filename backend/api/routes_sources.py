"""GET /sources — which data sources are usable right now (spec section 12)."""

from fastapi import APIRouter, Request

from api.schemas import SourcesResponse
from config.settings import load_adapters_config

router = APIRouter()


@router.get("/sources", response_model=SourcesResponse)
def sources(request: Request) -> SourcesResponse:
    adapters = list((request.app.state.adapters or {}).values())
    enabled = set(load_adapters_config().keys())

    def usable(adapter_name: str) -> bool:
        if adapter_name not in enabled:
            return False
        for adapter in adapters:  # registry is keyed by source_type; match by name
            if getattr(adapter, "name", "") == adapter_name:
                try:
                    return bool(adapter.is_available())
                except Exception:  # noqa: BLE001 — a broken probe means "not usable"
                    return False
        return False

    return SourcesResponse(
        sec_edgar=usable("sec_edgar"),
        news_api=usable("news_api"),  # Phase 3 registers this adapter
        apex=False,  # optional, disabled by default (spec section 6.4)
    )
