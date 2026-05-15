"""
Aggregator service.
Orchestrates fetching from both sources, deduplicates cross-source results
by fuzzy title matching, runs the scorer, and updates the in-memory cache.

Rate-limit resilience: if a source returns 0 tenders due to a 429 / network
error, the aggregator falls back to the previously cached tenders for that
source rather than replacing them with an empty list.
"""
import logging
import time
from datetime import datetime, timezone
from typing import List, Tuple, Optional

import httpx

from app.services import find_a_tender, contracts_finder, sell2wales, public_contracts_scotland
from app.services.scorer import bulk_score
from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

# Threshold for considering two tenders "the same" by title similarity
_DEDUP_THRESHOLD = 0.85


async def refresh_all(
    days_back: int = 30,
    previous_tenders: Optional[List[Tender]] = None,
) -> Tuple[List[Tender], List[str]]:
    """
    Fetch from all sources, deduplicate, score, and return results.

    Args:
        days_back:         How far back to look for notices.
        previous_tenders:  The current cache contents, used as a fallback
                           if a source fetch fails or returns 0 results
                           (e.g. due to a 429 rate-limit response).

    Returns:
        (scored_tenders, list_of_error_messages)
    """
    errors: List[str] = []
    start = time.monotonic()

    # Split previous cache by source so we can fall back per-source
    prev_fat: List[Tender] = []
    prev_cf:  List[Tender] = []
    if previous_tenders:
        prev_fat = [t for t in previous_tenders if t.source == TenderSource.FIND_A_TENDER]
        prev_cf  = [t for t in previous_tenders if t.source == TenderSource.CONTRACTS_FINDER]

    async with httpx.AsyncClient(follow_redirects=True) as client:
        fat_tenders: List[Tender] = []
        cf_tenders:  List[Tender] = []

        # ── Find a Tender ─────────────────────────────────────────────────────
        try:
            fat_tenders = await find_a_tender.fetch_tenders(client, days_back=days_back)
        except Exception as e:
            msg = f"Find a Tender fetch failed: {e}"
            logger.error(msg)
            errors.append(msg)

        if not fat_tenders and prev_fat:
            logger.warning(
                "FaT returned 0 tenders (rate-limited or error) — "
                "retaining %d cached FaT tenders from previous refresh",
                len(prev_fat),
            )
            fat_tenders = prev_fat

        # ── Contracts Finder ──────────────────────────────────────────────────
        try:
            cf_tenders = await contracts_finder.fetch_tenders(client, days_back=days_back)
        except Exception as e:
            msg = f"Contracts Finder fetch failed: {e}"
            logger.error(msg)
            errors.append(msg)

        if not cf_tenders and prev_cf:
            logger.warning(
                "CF returned 0 tenders (rate-limited or error) — "
                "retaining %d cached CF tenders from previous refresh",
                len(prev_cf),
            )
            cf_tenders = prev_cf

        # ── Sell2Wales ────────────────────────────────────────────────────────
        s2w_tenders: List[Tender] = []
        prev_s2w = [t for t in (previous_tenders or []) if t.source == TenderSource.SELL2WALES]
        try:
            s2w_tenders = await sell2wales.fetch_tenders(client, days_back=days_back)
        except Exception as e:
            msg = f"Sell2Wales fetch failed: {e}"
            logger.error(msg)
            errors.append(msg)

        if not s2w_tenders and prev_s2w:
            logger.warning(
                "Sell2Wales returned 0 tenders (error) — "
                "retaining %d cached S2W tenders from previous refresh",
                len(prev_s2w),
            )
            s2w_tenders = prev_s2w

        # ── Public Contracts Scotland ─────────────────────────────────────────
        pcs_tenders: List[Tender] = []
        prev_pcs = [t for t in (previous_tenders or []) if t.source == TenderSource.PUBLIC_CONTRACTS_SCOTLAND]
        try:
            pcs_tenders = await public_contracts_scotland.fetch_tenders(client, days_back=days_back)
        except Exception as e:
            msg = f"Public Contracts Scotland fetch failed: {e}"
            logger.error(msg)
            errors.append(msg)

        if not pcs_tenders and prev_pcs:
            logger.warning(
                "PCS returned 0 tenders (error) — "
                "retaining %d cached PCS tenders from previous refresh",
                len(prev_pcs),
            )
            pcs_tenders = prev_pcs

    # ── Merge, deduplicate, score ─────────────────────────────────────────────
    combined = _deduplicate(fat_tenders + cf_tenders + s2w_tenders + pcs_tenders)
    scored   = bulk_score(combined)

    elapsed = time.monotonic() - start
    logger.info(
        "Refresh complete: %d tenders from %d raw in %.1fs (%d errors) — FaT:%d CF:%d S2W:%d PCS:%d",
        len(scored),
        len(fat_tenders) + len(cf_tenders) + len(s2w_tenders) + len(pcs_tenders),
        elapsed,
        len(errors),
        len(fat_tenders),
        len(cf_tenders),
        len(s2w_tenders),
        len(pcs_tenders),
    )
    return scored, errors


def _deduplicate(tenders: List[Tender]) -> List[Tender]:
    """
    Remove near-duplicate tenders across sources using title similarity.
    Prefers Find a Tender entries when duplicates are found.
    """
    seen:   List[str]    = []
    unique: List[Tender] = []

    for tender in tenders:
        normalised_title = _normalise(tender.title)
        is_dup = any(
            _similarity(normalised_title, s) >= _DEDUP_THRESHOLD
            for s in seen
        )
        if not is_dup:
            unique.append(tender)
            seen.append(normalised_title)

    logger.debug("Deduplication: %d → %d tenders", len(tenders), len(unique))
    return unique


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation/whitespace for comparison."""
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _similarity(a: str, b: str) -> float:
    """
    Simple Jaccard similarity on word sets — avoids importing difflib
    for a lightweight comparison suitable for title dedup.
    """
    set_a = set(a.split())
    set_b = set(b.split())
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union        = set_a | set_b
    return len(intersection) / len(union)