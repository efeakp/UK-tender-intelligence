"""
TED (Tenders Electronic Daily) endpoints.

GET  /ted/status   — check whether notice data is loaded
POST /ted/refresh  — fetch EU energy notices (~1–2 min)
GET  /ted/notices  — list/filter cached TED notices
"""
import logging
import time as _time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.ted import get_store, fetch_ted_notices

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ted", tags=["TED (EU Procurement)"])


class TEDStatus(BaseModel):
    populated:      bool
    notice_count:   int
    last_refreshed: Optional[str] = None


@router.get("/status", response_model=TEDStatus, summary="TED data status")
async def ted_status():
    store = get_store()
    return TEDStatus(
        populated=len(store["notices"]) > 0,
        notice_count=store["total"],
        last_refreshed=store["fetched_at"],
    )


@router.get("/notices", summary="List TED energy notices")
async def list_ted_notices(
    q:          Optional[str] = Query(None,       description="Search title, authority, description"),
    scope:      Optional[str] = Query(None,       description="Filter by service scope tag"),
    category:   Optional[str] = Query(None,       description="Opportunity | Future Opportunity | Awarded Contract"),
    country:    Optional[str] = Query(None,       description="Filter by buyer country (e.g. Germany, France)"),
    min_score:  int           = Query(default=0,  ge=0, le=10),
    sort_by:    str           = Query(default="score", description="score | published | deadline | value"),
    sort_dir:   str           = Query(default="desc"),
    page:       int           = Query(default=1,  ge=1),
    page_size:  int           = Query(default=50, ge=1, le=500),
):
    store = get_store()
    notices = store["notices"]

    if not notices:
        raise HTTPException(
            status_code=503,
            detail=(
                "TED data not yet loaded. "
                "POST /ted/refresh to fetch EU energy notices (~1–2 minutes)."
            ),
        )

    results = notices

    if q and q.strip():
        q_lower = q.strip().lower()
        results = [
            n for n in results
            if q_lower in (n.get("title") or "").lower()
            or q_lower in (n.get("authority") or "").lower()
            or q_lower in (n.get("description") or "").lower()
        ]

    if scope and scope != "All":
        results = [n for n in results if scope in (n.get("matched_scopes") or [])]

    if category and category != "All":
        results = [n for n in results if n.get("category") == category]

    if country and country != "All":
        results = [n for n in results if country.lower() in (n.get("ted_country") or "").lower()]

    if min_score:
        results = [n for n in results if (n.get("score") or 0) >= min_score]

    # Sort
    reverse = sort_dir.lower() != "asc"
    if sort_by == "value":
        results = sorted(results, key=lambda n: n.get("value_amount") or 0, reverse=reverse)
    elif sort_by == "published":
        results = sorted(results, key=lambda n: n.get("published") or "", reverse=reverse)
    elif sort_by == "deadline":
        results = sorted(results, key=lambda n: n.get("deadline") or "", reverse=reverse)
    else:
        _cat_order = {"Opportunity": 0, "Future Opportunity": 1, "Awarded Contract": 2, "Unknown": 3}
        results = sorted(
            results,
            key=lambda n: (_cat_order.get(n.get("category", "Unknown"), 3), -(n.get("score") or 0)),
        )

    total = len(results)
    start = (page - 1) * page_size
    page_items = results[start: start + page_size]

    return {
        "total":          total,
        "returned":       len(page_items),
        "page":           page,
        "page_size":      page_size,
        "last_refreshed": store["fetched_at"],
        "notices":        page_items,
    }


@router.post("/refresh", summary="Fetch EU energy notices from TED (~1–2 min)")
async def refresh_ted():
    """
    Queries TED v3 Search API for energy CPV codes across:
      - Contract notices (CN): last 6 months
      - Award notices (CAN): last 12 months

    CPV codes: 71314000 (energy services), 71314200 (energy efficiency consultancy),
    09330000 (solar energy), 09310000 (electricity), and related codes.
    No API key required.
    """
    t0 = _time.time()
    try:
        store = await fetch_ted_notices()
        return {
            "success":          True,
            "notices_found":    store["total"],
            "duration_seconds": round(_time.time() - t0, 1),
            "last_refreshed":   store["fetched_at"],
        }
    except Exception as exc:
        logger.error("TED refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"TED refresh failed: {exc}")
