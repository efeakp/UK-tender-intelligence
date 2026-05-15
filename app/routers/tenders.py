"""
GET /tenders       — filtered, paginated, scored tender list
GET /tenders/{id}  — single tender detail
"""
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from app.config import settings
from app.dependencies import cache, CACHE_KEY_TENDERS
from app.models.tender import (
    Tender,
    TenderListResponse,
    TenderSource,
    ScopeTag,
)

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


def _tender_matches_scope(tender: Tender, scope: str) -> bool:
    """
    Check whether a tender matches a requested scope.
    matched_scopes is now List[str] containing service scope labels directly.
    all_matched_scopes in __dict__ is kept in sync for backwards compat.
    """
    scopes = tender.matched_scopes or []
    return scope in scopes


def _tender_matches_source(tender: Tender, source: str) -> bool:
    """
    Compare tender source against filter value robustly.
    Handles both enum instance and plain string comparisons.
    """
    tender_source = tender.source.value if hasattr(tender.source, "value") else str(tender.source)
    return tender_source == source


@router.get("", response_model=TenderListResponse, summary="List tenders")
async def list_tenders(
    q:          Optional[str] = Query(None,       description="Search across title, authority, description"),
    source:     Optional[str] = Query(None,       description="Filter by source: 'Find a Tender' or 'Contracts Finder'"),
    scope:      Optional[str] = Query(None,       description="Filter by matched business scope label"),
    category:   Optional[str] = Query(None,       description="Filter by notice category e.g. Opportunity, Awarded Contract"),
    min_score:  int           = Query(default=3,  ge=0, le=10,   description="Minimum relevance score"),
    page:       int           = Query(default=1,  ge=1),
    page_size:  int           = Query(default=25, ge=1, le=2000, description="Results per page"),
    sort_by:    str           = Query(default="score", description="score | deadline | published | value"),
    sort_dir:   str           = Query(default="desc",  description="asc | desc"),
):
    tenders = _get_cached_tenders()

    # ── Filtering ─────────────────────────────────────────────────────────────

    if q:
        q_lower = q.lower()
        tenders = [
            t for t in tenders
            if q_lower in t.title.lower()
            or q_lower in t.authority.lower()
            or q_lower in t.description.lower()
        ]

    if source:
        # Compare as strings to avoid TenderSource enum vs plain string mismatch
        tenders = [t for t in tenders if _tender_matches_source(t, source)]

    if scope:
        # Covers all 5 scope areas including the two new plain-string scopes
        tenders = [t for t in tenders if _tender_matches_scope(t, scope)]

    if category:
        # Handle unified category names that map across FaT and CF aliases
        CATEGORY_ALIASES = {
            "Future Opportunity": {"Future Opportunity", "Pipeline"},
            "Early Engagement":   {"Early Engagement", "Planning"},
            "Opportunity":        {"Opportunity", "Tender", "Open Opportunity"},
            "Awarded Contract":   {"Awarded Contract", "Award", "Contract"},
        }
        allowed = CATEGORY_ALIASES.get(category, {category})
        tenders = [t for t in tenders if t.category in allowed]

    tenders = [t for t in tenders if t.score >= min_score]

    # ── Sorting ───────────────────────────────────────────────────────────────
    reverse = sort_dir.lower() != "asc"
    sort_key_map = {
        "score":     lambda t: t.score,
        "deadline":  lambda t: t.deadline     or _epoch(),
        "published": lambda t: t.published    or _epoch(),
        "value":     lambda t: t.value_amount or 0.0,
    }
    key_fn = sort_key_map.get(sort_by, sort_key_map["score"])
    tenders = sorted(tenders, key=key_fn, reverse=reverse)

    # ── Pagination ────────────────────────────────────────────────────────────
    total        = len(tenders)
    start        = (page - 1) * page_size
    page_tenders = tenders[start: start + page_size]

    entry = cache._store.get(CACHE_KEY_TENDERS)
    last_refreshed = entry.expires_at if entry else None

    return TenderListResponse(
        total=total,
        returned=len(page_tenders),
        page=page,
        page_size=page_size,
        last_refreshed=last_refreshed,
        tenders=page_tenders,
    )


@router.post("/fetch/{notice_id}", summary="Fetch a specific FaT notice directly and add to cache")
async def fetch_fat_notice(notice_id: str):
    """
    Fetch a specific Find a Tender notice by its notice ID (e.g. 037689-2026)
    and add it to the cache. Use this to retrieve pipeline notices that may
    have been missed by the paginated refresh.
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


def _epoch():
    from datetime import datetime, timezone
    return datetime.fromtimestamp(0, tz=timezone.utc)