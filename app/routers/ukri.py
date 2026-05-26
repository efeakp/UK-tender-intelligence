"""
UKRI / Innovate UK endpoints.

GET  /ukri/status   — check whether project data is loaded
POST /ukri/refresh  — fetch Innovate UK energy projects (~1 min)
GET  /ukri/projects — list/filter cached projects
"""
import logging
import time as _time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.ukri import get_store, fetch_ukri_projects

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ukri", tags=["UKRI / Innovate UK"])

SCOPES = [
    "Service 01: Renewable Energy Opportunity Identification",
    "Service 02: Energy Feasibility Studies",
    "Service 03: Energy System Optimisation",
    "Service 04: Business Case Development",
]


class UKRIStatus(BaseModel):
    populated:      bool
    project_count:  int
    last_refreshed: Optional[str] = None


@router.get("/status", response_model=UKRIStatus, summary="UKRI data status")
async def ukri_status():
    store = get_store()
    return UKRIStatus(
        populated=len(store["projects"]) > 0,
        project_count=store["total"],
        last_refreshed=store["fetched_at"],
    )


@router.get("/projects", summary="List Innovate UK projects")
async def list_ukri_projects(
    q:          Optional[str] = Query(None,       description="Search title, authority, description"),
    scope:      Optional[str] = Query(None,       description="Filter by service scope tag"),
    status:     Optional[str] = Query(None,       description="Active | Closed"),
    min_score:  int           = Query(default=0,  ge=0, le=10),
    sort_by:    str           = Query(default="score", description="score | published | deadline | value"),
    sort_dir:   str           = Query(default="desc"),
    page:       int           = Query(default=1,  ge=1),
    page_size:  int           = Query(default=50, ge=1, le=500),
):
    store = get_store()
    projects = store["projects"]

    if not projects:
        raise HTTPException(
            status_code=503,
            detail=(
                "Innovate UK data not yet loaded. "
                "POST /ukri/refresh to fetch projects (~30–60 seconds)."
            ),
        )

    # ── Filters ──────────────────────────────────────────────────────────────
    results = projects

    if q and q.strip():
        q_lower = q.strip().lower()
        results = [
            p for p in results
            if q_lower in (p.get("title") or "").lower()
            or q_lower in (p.get("authority") or "").lower()
            or q_lower in (p.get("description") or "").lower()
        ]

    if scope and scope != "All":
        results = [p for p in results if scope in (p.get("matched_scopes") or [])]

    if status and status != "All":
        results = [p for p in results if (p.get("ukri_status") or "").lower() == status.lower()]

    if min_score:
        results = [p for p in results if (p.get("score") or 0) >= min_score]

    # ── Sort ─────────────────────────────────────────────────────────────────
    reverse = sort_dir.lower() != "asc"
    if sort_by == "value":
        results = sorted(results, key=lambda p: p.get("value_amount") or 0, reverse=reverse)
    elif sort_by == "published":
        results = sorted(results, key=lambda p: p.get("published") or "", reverse=reverse)
    elif sort_by == "deadline":
        results = sorted(results, key=lambda p: p.get("deadline") or "", reverse=reverse)
    else:  # score (default)
        results = sorted(
            results,
            key=lambda p: (p.get("ukri_status") != "Active", -(p.get("score") or 0)),
        )

    total = len(results)
    start = (page - 1) * page_size
    page_items = results[start: start + page_size]

    return {
        "total":        total,
        "returned":     len(page_items),
        "page":         page,
        "page_size":    page_size,
        "last_refreshed": store["fetched_at"],
        "projects":     page_items,
    }


@router.post("/refresh", summary="Fetch Innovate UK energy projects (~30–60 s)")
async def refresh_ukri():
    """
    Queries the GtR v2 API across 10 energy search terms, deduplicates,
    filters to Innovate UK-funded projects, scores them, and caches results.
    Typically completes in 30–60 seconds.
    """
    t0 = _time.time()
    try:
        store = await fetch_ukri_projects()
        return {
            "success":          True,
            "projects_found":   store["total"],
            "duration_seconds": round(_time.time() - t0, 1),
            "last_refreshed":   store["fetched_at"],
        }
    except Exception as exc:
        logger.error("UKRI refresh failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"UKRI refresh failed: {exc}")
