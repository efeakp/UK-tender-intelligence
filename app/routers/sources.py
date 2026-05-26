"""
GET /sources — per-source health, last fetch time, and tender counts.
"""

from fastapi import APIRouter

from app.config import settings
from app.dependencies import (
    cache,
    CACHE_KEY_TENDERS,
    CACHE_KEY_FAT_META,
    CACHE_KEY_CF_META,
    CACHE_KEY_S2W_META,
    CACHE_KEY_PCS_META,
    CACHE_KEY_PROACTIS_META,
    CACHE_KEY_YORTENDER_META,
    CACHE_KEY_INTEND_META,
    get_source_meta,
)
from app.models.tender import SourcesResponse, SourceStatus
from app.services.scheduler import next_run_time

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get("", response_model=SourcesResponse, summary="Source health and stats")
async def get_sources():
    tenders = cache.get(CACHE_KEY_TENDERS) or []

    _all_sources = [
        ("Find a Tender",           CACHE_KEY_FAT_META),
        ("Contracts Finder",        CACHE_KEY_CF_META),
        ("Sell2Wales",              CACHE_KEY_S2W_META),
        ("Public Contracts Scotland", CACHE_KEY_PCS_META),
        ("Proactis",                CACHE_KEY_PROACTIS_META),
        ("Yortender",               CACHE_KEY_YORTENDER_META),
        ("In-Tend",                 CACHE_KEY_INTEND_META),
    ]

    return SourcesResponse(
        sources=[
            SourceStatus(
                name=name,
                healthy=meta.healthy,
                last_fetched=meta.last_fetched,
                tender_count=meta.tender_count,
                error=meta.error,
            )
            for name, key in _all_sources
            for meta in [get_source_meta(key)]
        ],
        total_cached=len(tenders),
        cache_ttl_minutes=settings.cache_ttl_minutes,
        next_scheduled_refresh=next_run_time(),
    )
