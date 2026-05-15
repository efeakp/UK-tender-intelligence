"""
GET /export/csv — Download all cached tenders as a CSV file.

Supports optional filtering via query params (same as /tenders).
Defaults to min_score=0 so the full dataset is exported unless filtered.
"""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.dependencies import cache, CACHE_KEY_TENDERS
from app.services.filtering import apply_filters, _source_str

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/export", tags=["Export"])

CSV_COLUMNS = [
    "id", "source", "category", "title", "authority", "value",
    "value_amount", "published", "deadline", "score", "score_label",
    "matched_scopes", "matched_keywords", "cpv_codes", "url", "description",
]


@router.get("/csv", summary="Export tenders as CSV")
async def export_csv(
    q:         Optional[str] = Query(None, description="Free-text search"),
    source:    Optional[str] = Query(None, description="Filter by source"),
    scope:     Optional[str] = Query(None, description="Filter by matched scope label"),
    category:  Optional[str] = Query(None, description="Filter by notice category"),
    min_score: int           = Query(default=0, ge=0, le=10, description="Minimum relevance score (default 0 = all tenders)"),
):
    tenders = cache.get(CACHE_KEY_TENDERS) or []
    tenders = apply_filters(tenders, q=q, source=source, scope=scope, category=category, min_score=min_score)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    for t in tenders:
        scopes = t.all_matched_scopes or t.matched_scopes or []
        writer.writerow({
            "id":               t.id,
            "source":           _source_str(t),
            "category":         t.category or "",
            "title":            t.title,
            "authority":        t.authority,
            "value":            t.value or "",
            "value_amount":     t.value_amount if t.value_amount is not None else "",
            "published":        _fmt_dt(t.published),
            "deadline":         _fmt_dt(t.deadline),
            "score":            t.score,
            "score_label":      t.score_label.value if hasattr(t.score_label, "value") else str(t.score_label or ""),
            "matched_scopes":   " | ".join(scopes),
            "matched_keywords": " | ".join(t.matched_keywords or []),
            "cpv_codes":        " | ".join(t.cpv_codes or []),
            "url":              t.url,
            "description":      (t.description or "").replace("\n", " ").replace("\r", " "),
        })

    csv_content = output.getvalue()
    output.close()

    date_str    = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    source_part = f"_{source.lower().replace(' ', '-')}"   if source   else ""
    scope_part  = f"_{scope.split('/')[0].strip().lower().replace(' ', '-').replace('&', 'and')}" if scope else ""
    cat_part    = f"_{category.lower().replace(' ', '-')}" if category else ""
    score_part  = f"_score{min_score}+"                    if min_score > 0 else ""
    filename    = f"nordic-energy-tenders_{date_str}{source_part}{scope_part}{cat_part}{score_part}.csv"

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
