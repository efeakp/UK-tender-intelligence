"""
GET /tenders       — filtered, paginated, scored tender list
GET /tenders/{id}  — single tender detail
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.config import settings
from app.dependencies import cache, CACHE_KEY_TENDERS, get_last_refresh_time
from app.models.tender import Tender, TenderListResponse, ProcurementRecord
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
    q:              Optional[str]  = Query(None,       description="Search across title, authority, description"),
    source:         Optional[str]  = Query(None,       description="Filter by source"),
    scope:          Optional[str]  = Query(None,       description="Filter by matched business scope label"),
    category:       Optional[str]  = Query(None,       description="Filter by notice category"),
    min_score:      int            = Query(default=5,  ge=0, le=10, description="Minimum relevance score"),
    region:         Optional[str]  = Query(None,       description="Filter by NUTS delivery region code (e.g. UKE for Yorkshire, UKD for North West)"),
    competitor_win: Optional[bool] = Query(None,       description="Filter to competitor wins only (true) or exclude them (false)"),
    cpv:            Optional[str]  = Query(None,       description="Filter by CPV code prefix (e.g. 71314 matches all 71314xxx codes)"),
    page:           int            = Query(default=1,  ge=1),
    page_size:      int            = Query(default=25, ge=1, le=2000, description="Results per page"),
    sort_by:        str            = Query(default="score", description="score | deadline | published | value"),
    sort_dir:       str            = Query(default="desc",  description="asc | desc"),
):
    tenders = _get_cached_tenders()
    tenders = apply_filters(tenders, q=q, source=source, scope=scope, category=category, min_score=min_score, region=region, competitor_win=competitor_win, cpv=cpv)
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


@router.post("/fetch/s2w/{ocid}", summary="Fetch a specific Sell2Wales notice directly and add to cache")
async def fetch_s2w_notice(ocid: str):
    """
    Fetch a specific Sell2Wales notice by OCID (e.g. ocds-kuma6s-XXXXXXXX) and add it to the cache.
    """
    from app.services.sell2wales import fetch_notice_by_ocid
    from app.services.scorer import score_tender

    tender = await fetch_notice_by_ocid(ocid)
    if not tender:
        raise HTTPException(
            status_code=404,
            detail=f"Notice with OCID '{ocid}' not found on Sell2Wales",
        )
    scored = score_tender(tender)
    scored.manually_added = True
    tenders = cache.get(CACHE_KEY_TENDERS) or []
    existing_ids = {t.id for t in tenders}
    if scored.id not in existing_ids:
        tenders.append(scored)
        cache.set(CACHE_KEY_TENDERS, tenders, ttl_minutes=settings.cache_ttl_minutes)
        logger.info("S2W direct fetch: added '%s' (score=%d)", scored.title, scored.score)
        return {"status": "added", "tender": scored}
    return {"status": "already_cached", "tender": scored}


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


@router.get("/{tender_id}/record", response_model=ProcurementRecord, summary="Get full procurement lifecycle for a FaT tender")
async def get_tender_record(
    tender_id: str,
    ocid: Optional[str] = Query(None, description="OCID passed directly from the client — bypasses cache lookup so post-refresh requests still work"),
):
    """
    Fetch the full procurement lifecycle (all notices in the OCID family) for a Find a Tender notice.
    Only available for tenders with an OCID (FaT source). Returns all related notice entries in
    chronological order.
    """
    from app.services.find_a_tender import fetch_record_by_ocid

    if not ocid:
        # Fall back to cache lookup when ocid not provided
        tenders = _get_cached_tenders()
        tender = next((t for t in tenders if t.id == tender_id), None)
        if not tender:
            raise HTTPException(status_code=404, detail=f"Tender '{tender_id}' not found in cache — try again or pass ?ocid= directly")
        if not tender.ocid:
            raise HTTPException(
                status_code=422,
                detail=f"Tender '{tender_id}' has no OCID — procurement record not available",
            )
        ocid = tender.ocid

    record_data = await fetch_record_by_ocid(ocid)
    if not record_data:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch procurement record for OCID '{ocid}' from Find a Tender",
        )
    return ProcurementRecord(**record_data)


@router.get("/{tender_id}", response_model=Tender, summary="Get single tender")
async def get_tender(tender_id: str):
    tenders = _get_cached_tenders()
    for t in tenders:
        if t.id == tender_id:
            return t
    raise HTTPException(status_code=404, detail=f"Tender '{tender_id}' not found")
