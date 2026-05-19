"""
Market intelligence endpoints.

GET  /market/status   — check whether market data is loaded
POST /market/refresh  — fetch CPV-matched awarded contracts (2–5 min)
GET  /market/awards   — list CPV-matched awarded contracts from cache
"""
import logging
import time as _time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.dependencies import (
    cache,
    CACHE_KEY_MARKET_AWARDS,
    CACHE_KEY_MARKET_REFRESHED,
)
from app.models.tender import TenderListResponse
from app.services.filtering import apply_filters, apply_sort

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["Market Intelligence"])

MARKET_CACHE_TTL_MINUTES = 24 * 60  # 24 hours


class MarketStatus(BaseModel):
    populated:      bool
    award_count:    int
    last_refreshed: Optional[str] = None


@router.get("/status", response_model=MarketStatus, summary="Market data status")
async def market_status():
    awards    = cache.get(CACHE_KEY_MARKET_AWARDS) or []
    refreshed = cache.get(CACHE_KEY_MARKET_REFRESHED)
    return MarketStatus(
        populated=len(awards) > 0,
        award_count=len(awards),
        last_refreshed=refreshed.isoformat() if refreshed else None,
    )


@router.get("/awards", response_model=TenderListResponse, summary="CPV-matched awarded contracts")
async def list_market_awards(
    q:              Optional[str]  = Query(None,        description="Search title, authority, description"),
    source:         Optional[str]  = Query(None,        description="Filter by source"),
    scope:          Optional[str]  = Query(None,        description="Filter by service scope"),
    competitor_win: Optional[bool] = Query(None,        description="True = competitor wins only"),
    page:           int            = Query(default=1,   ge=1),
    page_size:      int            = Query(default=50,  ge=1, le=1000),
    sort_by:        str            = Query(default="published", description="published | value | score"),
    sort_dir:       str            = Query(default="desc"),
):
    awards = cache.get(CACHE_KEY_MARKET_AWARDS)
    if not awards:
        raise HTTPException(
            status_code=503,
            detail=(
                "Market awards not yet loaded. "
                "POST /market/refresh to fetch data (typically takes 2–5 minutes)."
            ),
        )
    refreshed = cache.get(CACHE_KEY_MARKET_REFRESHED)
    tenders = apply_filters(
        awards, q=q, source=source, scope=scope, min_score=0,
        competitor_win=competitor_win,
    )
    tenders = apply_sort(tenders, sort_by=sort_by, sort_dir=sort_dir)
    total = len(tenders)
    start = (page - 1) * page_size
    return TenderListResponse(
        total=total,
        returned=len(tenders[start:start + page_size]),
        page=page,
        page_size=page_size,
        last_refreshed=refreshed,
        tenders=tenders[start:start + page_size],
    )


@router.post("/refresh", summary="Fetch CPV-matched awarded contracts (2–5 min)")
async def refresh_market_awards():
    """
    Trigger a market intelligence data refresh.

    Fetches CPV-relevant awarded contracts across all four sources:
      - S2W + PCS: 12 months (monthly API, efficient)
      - CF: 6 months, award stage only (page-capped)
      - FaT: 30 days, award stage only (rate-limited)

    Only notices whose CPV codes match Nordic Energy's taxonomy are retained.
    Results are cached for 24 hours. This operation typically takes 2–5 minutes.
    """
    from app.services.market_awards import fetch_market_awards

    t0 = _time.time()
    try:
        awards = await fetch_market_awards()
        now = datetime.now(timezone.utc)
        cache.set(CACHE_KEY_MARKET_AWARDS,    awards, ttl_minutes=MARKET_CACHE_TTL_MINUTES)
        cache.set(CACHE_KEY_MARKET_REFRESHED, now,    ttl_minutes=MARKET_CACHE_TTL_MINUTES + 60)
        return {
            "success":         True,
            "awards_found":    len(awards),
            "competitor_wins": sum(1 for t in awards if t.competitor_win),
            "duration_seconds": round(_time.time() - t0, 1),
        }
    except Exception as e:
        logger.error("Market awards refresh failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Market refresh failed: {e}")
