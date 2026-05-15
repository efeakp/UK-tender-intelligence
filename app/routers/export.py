"""
GET /export/csv — Download all cached tenders as a CSV file.

Supports optional filtering via query params (same as /tenders) so the
caller can export everything, only strong matches, a specific source, etc.
Includes all tenders regardless of score by default (min_score=0).
"""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.dependencies import cache, CACHE_KEY_TENDERS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["Export"])

# Columns written to the CSV, in order
CSV_COLUMNS = [
    "id",
    "source",
    "category",
    "title",
    "authority",
    "value",
    "value_amount",
    "published",
    "deadline",
    "score",
    "score_label",
    "matched_scopes",
    "matched_keywords",
    "cpv_codes",
    "url",
    "description",
]


def _tender_source_str(tender) -> str:
    """Extract source as plain string regardless of whether it's an enum or string."""
    return tender.source.value if hasattr(tender.source, "value") else str(tender.source)


def _tender_matches_source(tender, source: str) -> bool:
    return _tender_source_str(tender) == source


def _tender_matches_scope(tender, scope: str) -> bool:
    """
    Check scope across both matched_scopes (enum values) and
    all_matched_scopes (all 5 plain-string scopes from scorer).
    """
    enum_scopes = [
        s.value if hasattr(s, "value") else str(s)
        for s in (tender.matched_scopes or [])
    ]
    all_scopes = tender.__dict__.get("all_matched_scopes", enum_scopes)
    return scope in all_scopes or scope in enum_scopes


@router.get("/csv", summary="Export tenders as CSV")
async def export_csv(
    q:         Optional[str] = Query(None, description="Free-text search across title, authority, description"),
    source:    Optional[str] = Query(None, description="Filter by source: 'Find a Tender' or 'Contracts Finder'"),
    scope:     Optional[str] = Query(None, description="Filter by matched scope label"),
    category:  Optional[str] = Query(None, description="Filter by notice category e.g. Opportunity, Awarded Contract"),
    min_score: int           = Query(default=0, ge=0, le=10, description="Minimum relevance score (default 0 = all tenders)"),
):
    """
    Export the full cached tender list as a CSV file.

    By default exports ALL tenders (min_score=0) including those outside
    Nordic Energy's scope, so you can review the complete dataset.

    Optional filters mirror the /tenders endpoint:
      ?min_score=7                      → strong matches only
      ?source=Contracts+Finder          → CF tenders only
      ?category=Opportunity             → active tenders only
      ?scope=Heat+networks+%2F+district+energy  → heat network tenders only
      ?q=solar                          → tenders matching "solar"
    """
    tenders = cache.get(CACHE_KEY_TENDERS) or []

    # ── Apply filters ─────────────────────────────────────────────────────────
    if q:
        q_lower = q.lower()
        tenders = [
            t for t in tenders
            if q_lower in t.title.lower()
            or q_lower in t.authority.lower()
            or q_lower in (t.description or "").lower()
        ]

    if source:
        tenders = [t for t in tenders if _tender_matches_source(t, source)]

    if scope:
        tenders = [t for t in tenders if _tender_matches_scope(t, scope)]

    if category:
        CATEGORY_ALIASES = {
            "Future Opportunity": {"Future Opportunity", "Pipeline"},
            "Early Engagement":   {"Early Engagement", "Planning"},
            "Opportunity":        {"Opportunity", "Tender", "Open Opportunity"},
            "Awarded Contract":   {"Awarded Contract", "Award", "Contract"},
        }
        allowed = CATEGORY_ALIASES.get(category, {category})
        tenders = [t for t in tenders if t.category in allowed]

    tenders = [t for t in tenders if t.score >= min_score]

    # ── Build CSV in memory ───────────────────────────────────────────────────
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for t in tenders:
        # Get all matched scopes including new plain-string ones
        enum_scopes = [
            s.value if hasattr(s, "value") else str(s)
            for s in (t.matched_scopes or [])
        ]
        all_scopes = t.__dict__.get("all_matched_scopes", enum_scopes)

        writer.writerow({
            "id":               t.id,
            "source":           _tender_source_str(t),
            "category":         t.category or "",
            "title":            t.title,
            "authority":        t.authority,
            "value":            t.value or "",
            "value_amount":     t.value_amount if t.value_amount is not None else "",
            "published":        _fmt_dt(t.published),
            "deadline":         _fmt_dt(t.deadline),
            "score":            t.score,
            "score_label":      t.score_label.value if hasattr(t.score_label, "value") else str(t.score_label or ""),
            "matched_scopes":   " | ".join(all_scopes),
            "matched_keywords": " | ".join(t.matched_keywords or []),
            "cpv_codes":        " | ".join(t.cpv_codes or []),
            "url":              t.url,
            "description":      (t.description or "").replace("\n", " ").replace("\r", " "),
        })

    csv_content = output.getvalue()
    output.close()

    # ── Filename includes date and filter summary ─────────────────────────────
    date_str     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_part  = f"_{source.lower().replace(' ', '-')}"   if source   else ""
    scope_part   = f"_{scope.split('/')[0].strip().lower().replace(' ', '-').replace('&', 'and')}" if scope else ""
    cat_part     = f"_{category.lower().replace(' ', '-')}" if category else ""
    score_part   = f"_score{min_score}+"                    if min_score > 0 else ""
    filename     = f"nordic-energy-tenders_{date_str}{source_part}{scope_part}{cat_part}{score_part}.csv"

    logger.info("CSV export: %d tenders → %s", len(tenders), filename)

    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Total-Tenders":     str(len(tenders)),
        },
    )


def _fmt_dt(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return str(dt)