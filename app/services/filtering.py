"""
Shared tender filtering and sorting logic.
Used by both routers/tenders.py and routers/export.py to keep filter
behaviour consistent and avoid duplication.
"""

from datetime import datetime, timezone
from typing import List, Optional

from app.models.tender import Tender

CATEGORY_ALIASES: dict[str, set[str]] = {
    "Future Opportunity": {"Future Opportunity", "Pipeline"},
    "Early Engagement":   {"Early Engagement", "Planning"},
    "Opportunity":        {"Opportunity", "Tender", "Open Opportunity"},
    "Awarded Contract":   {"Awarded Contract", "Award", "Contract"},
}

_EPOCH = datetime.fromtimestamp(0, tz=timezone.utc)


def apply_filters(
    tenders: List[Tender],
    q: Optional[str] = None,
    source: Optional[str] = None,
    scope: Optional[str] = None,
    category: Optional[str] = None,
    min_score: int = 0,
    region: Optional[str] = None,
    competitor_win: Optional[bool] = None,
) -> List[Tender]:
    if q:
        q_lower = q.lower()
        tenders = [
            t for t in tenders
            if q_lower in t.title.lower()
            or q_lower in t.authority.lower()
            or q_lower in (t.description or "").lower()
        ]
    if source:
        tenders = [t for t in tenders if _source_str(t) == source]
    if scope:
        tenders = [t for t in tenders if scope in (t.matched_scopes or [])]
    if category:
        allowed = CATEGORY_ALIASES.get(category, {category})
        tenders = [t for t in tenders if t.category in allowed]
    if min_score > 0:
        tenders = [t for t in tenders if t.score >= min_score]
    if region:
        region_upper = region.upper()
        tenders = [
            t for t in tenders
            if any(n.startswith(region_upper) for n in (t.nuts_codes or []))
        ]
    if competitor_win is not None:
        tenders = [t for t in tenders if t.competitor_win == competitor_win]
    return tenders


def apply_sort(
    tenders: List[Tender],
    sort_by: str = "score",
    sort_dir: str = "desc",
) -> List[Tender]:
    reverse = sort_dir.lower() != "asc"
    key_map = {
        "score":     lambda t: t.score,
        "deadline":  lambda t: t.deadline     or _EPOCH,
        "published": lambda t: t.published    or _EPOCH,
        "value":     lambda t: t.value_amount or 0.0,
    }
    return sorted(tenders, key=key_map.get(sort_by, key_map["score"]), reverse=reverse)


def _source_str(tender: Tender) -> str:
    return tender.source.value if hasattr(tender.source, "value") else str(tender.source)
