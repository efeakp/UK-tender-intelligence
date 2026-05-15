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
    get_source_meta,
)
from app.models.tender import SourcesResponse, SourceStatus
from app.services.scheduler import next_run_time

router = APIRouter(prefix="/sources", tags=["Sources"])


@router.get("", response_model=SourcesResponse, summary="Source health and stats")
async def get_sources():
    fat_meta = get_source_meta(CACHE_KEY_FAT_META)
    cf_meta = get_source_meta(CACHE_KEY_CF_META)

    tenders = cache.get(CACHE_KEY_TENDERS) or []
    total_cached = len(tenders)

    return SourcesResponse(
        sources=[
            SourceStatus(
                name="Find a Tender",
                healthy=fat_meta.healthy,
                last_fetched=fat_meta.last_fetched,
                tender_count=fat_meta.tender_count,
                error=fat_meta.error,
            ),
            SourceStatus(
                name="Contracts Finder",
                healthy=cf_meta.healthy,
                last_fetched=cf_meta.last_fetched,
                tender_count=cf_meta.tender_count,
                error=cf_meta.error,
            ),
        ],
        total_cached=total_cached,
        cache_ttl_minutes=settings.cache_ttl_minutes,
        next_scheduled_refresh=next_run_time(),
    )
