"""Metrics route exposing runtime operational statistics (docs/OPERATIONS.md)."""

from typing import Any

from fastapi import APIRouter, Request

from observability.metrics import METRICS

router = APIRouter(tags=["observability"])


@router.get(
    "/metrics",
    summary="Get operational metrics snapshot",
    description=(
        "Returns request latencies, query statistics, ingestion results, and provider usage."
    ),
)
async def get_metrics(_: Request) -> dict[str, Any]:
    return METRICS.get_snapshot()
