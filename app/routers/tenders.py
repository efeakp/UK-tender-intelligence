"""
GET /tenders       — filtered, paginated, scored tender list
GET /tenders/{id}  — single tender detail
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.config import settings
from app.dependencies import cache, CACHE_KEY_TENDERS, get_last_refresh_time
from app.models.tender import Tender, TenderListResponse
from app.services.filtering import apply_filters, apply_sort

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tenders", tags=["Tenders"])


def _get_cached_tenders() -> list[Tender]:
    tenders = cache.get(CACHE_KEY_TENDERS)
    if tenders is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Tender data not yet available. "
                "POST /refresh to populate the cache, or wait for the scheduled refresh."
            ),
        )
    return tenders


@router.get("", response_model=TenderListResponse, summary="List tenders")
async def list_tenders(
    q:          Optional[str] = Query(None,       description="Search across title, authority, description"),
    source:     Optional[str] = Query(None,       description="Filter by source"),
    scope:      Optional[str] = Query(None,       description="Filter by matched business scope label"),
    category:   Optional[str] = Query(None,       description="Filter by notice category"),
    min_score:  int           = Query(default=5,  ge=0, le=10, description="Minimum relevance score"),
    page:       int           = Query(default=1,  ge=1),
    page_size:  int           = Query(default=25, ge=1, le=2000, description="Results per page"),
    sort_by:    str           = Query(default="score", description="score | deadline | published | value"),
    sort_dir:   str           = Query(default="desc",  description="asc | desc"),
):
    tenders = _get_cached_tenders()
    tenders = apply_filters(tenders, q=q, source=source, scope=scope, category=category, min_score=min_score)
    tenders = apply_sort(tenders, sort_by=sort_by, sort_dir=sort_dir)

    total        = len(tenders)
    start        = (page - 1) * page_size
    page_tenders = tenders[start: start + page_size]

    return TenderListResponse(
        total=total,
        returned=len(page_tenders),
        page=page,
        page_size=page_size,
        last_refreshed=get_last_refresh_time(),
        tenders=page_tenders,
    )


@router.post("/fetch/{notice_id}", summary="Fetch a specific FaT notice directly and add to cache")
async def fetch_fat_notice(notice_id: str):
    """
    Fetch a specific Find a Tender notice by its notice ID (e.g. 037689-2026)
    and add it to the cache.
    """
    from app.services.find_a_tender import fetch_notice_by_id
    from app.services.scorer import score_tender

    tender = await fetch_notice_by_id(notice_id)
    if not tender:
        raise HTTPException(
            status_code=404,
            detail=f"Notice {notice_id} not found on Find a Tender"
        )
    scored = score_tender(tender)
    scored.manually_added = True
    tenders = cache.get(CACHE_KEY_TENDERS) or []
    existing_ids = {t.id for t in tenders}
    if scored.id not in existing_ids:
        tenders.append(scored)
        cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)
        logger.info("Direct fetch: added '%s' (score=%d)", scored.title, scored.score)
        return {"status": "added", "tender": scored}
    return {"status": "already_cached", "tender": scored}


@router.get("/{tender_id}", response_model=Tender, summary="Get single tender")
async def get_tender(tender_id: str):
    tenders = _get_cached_tenders()
    for t in tenders:
        if t.id == tender_id:
            return t
    raise HTTPException(status_code=404, detail=f"Tender '{tender_id}' not found")
