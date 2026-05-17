"""
Aggregator service.
Orchestrates fetching from all sources concurrently, deduplicates
cross-source results by fuzzy title matching, runs the scorer, and
updates the in-memory cache.

Rate-limit resilience: if a source returns 0 tenders due to a 429 or
network error, the aggregator falls back to the previously cached tenders
for that source rather than replacing them with an empty list.
"""
import asyncio
import logging
import time
from typing import List, Tuple, Optional

import httpx

from app.services import find_a_tender, contracts_finder, sell2wales, public_contracts_scotland
from app.services.scorer import bulk_score
from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

_DEDUP_THRESHOLD = 0.85

# Higher = more actionable/recent — used to prefer UK4 over UK1 in Jaccard dedup
_NOTICE_PRIORITY = {
    "UK4": 9, "UK5": 8, "UK3": 7, "UK2": 6,
    "UK1": 5, "UK6": 4, "UK7": 3,
}

_SOURCE_INFO = [
    (TenderSource.FIND_A_TENDER,             "Find a Tender",           find_a_tender.fetch_tenders),
    (TenderSource.CONTRACTS_FINDER,          "Contracts Finder",        contracts_finder.fetch_tenders),
    (TenderSource.SELL2WALES,               "Sell2Wales",              sell2wales.fetch_tenders),
    (TenderSource.PUBLIC_CONTRACTS_SCOTLAND, "Public Contracts Scotland", public_contracts_scotland.fetch_tenders),
]


async def refresh_all(
    days_back: int = 30,
    previous_tenders: Optional[List[Tender]] = None,
) -> Tuple[List[Tender], List[str]]:
    """
    Fetch from all sources concurrently, deduplicate, score, and return results.

    Args:
        days_back:         How far back to look for notices.
        previous_tenders:  Current cache contents used as a per-source fallback
                           if a fetch fails or returns 0 results.

    Returns:
        (scored_tenders, list_of_error_messages)
    """
    errors: List[str] = []
    start = time.monotonic()

    # Build per-source fallback map keyed by enum value string
    prev_by_source: dict[str, List[Tender]] = {}
    for t in (previous_tenders or []):
        src = t.source if isinstance(t.source, str) else t.source.value
        prev_by_source.setdefault(src, []).append(t)

    # ── Concurrent fetch ──────────────────────────────────────────────────────
    # Each source is wrapped in a 10-minute timeout so a hung source (e.g. FaT
    # during a sustained DNS outage) cannot block the cache write for other sources.
    SOURCE_TIMEOUT_S = 600

    async def _fetch_with_timeout(fn, client, label):
        try:
            return await asyncio.wait_for(fn(client, days_back=days_back), timeout=SOURCE_TIMEOUT_S)
        except asyncio.TimeoutError:
            raise Exception(f"{label} timed out after {SOURCE_TIMEOUT_S}s")

    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(
            *[_fetch_with_timeout(fn, client, label) for _, label, fn in _SOURCE_INFO],
            return_exceptions=True,
        )

    # ── Merge results with per-source fallback ────────────────────────────────
    all_tenders: List[Tender] = []
    for (source_enum, label, _), result in zip(_SOURCE_INFO, results):
        if isinstance(result, Exception):
            msg = f"{label} fetch failed: {result}"
            logger.error(msg)
            errors.append(msg)
            tenders: List[Tender] = []
        else:
            tenders = result

        if not tenders:
            fallback = prev_by_source.get(source_enum.value, [])
            if fallback:
                logger.warning(
                    "%s returned 0 tenders — retaining %d cached tenders from previous refresh",
                    label, len(fallback),
                )
                tenders = fallback

        all_tenders.extend(tenders)

    # ── Deduplicate, score ────────────────────────────────────────────────────
    # Sort so that more actionable notice types (UK4 > UK1) win the Jaccard dedup.
    # The deduplicator keeps the first occurrence, so highest-priority must come first.
    all_tenders.sort(
        key=lambda t: _NOTICE_PRIORITY.get(t.notice_type, 5),
        reverse=True,
    )
    combined = _deduplicate(all_tenders)
    scored   = bulk_score(combined)

    # ── Re-retain manually-added notices ─────────────────────────────────────
    # Notices injected via POST /tenders/fetch/{id} won't appear in a normal
    # refresh (e.g. expired deadlines, pipeline stage DNS blip). Re-add any
    # that weren't already fetched this cycle so they survive future refreshes.
    scored_ids = {t.id for t in scored}
    for t in (previous_tenders or []):
        if getattr(t, "manually_added", False) and t.id not in scored_ids:
            scored.append(t)
            logger.info("Retained manually-added notice: '%s'", t.title)

    elapsed = time.monotonic() - start
    counts  = {
        label: sum(1 for t in scored if t.source == source_enum.value)
        for source_enum, label, _ in _SOURCE_INFO
    }
    logger.info(
        "Refresh complete: %d tenders from %d raw in %.1fs (%d errors) — %s",
        len(scored),
        len(all_tenders),
        elapsed,
        len(errors),
        " | ".join(f"{label}:{count}" for label, count in counts.items()),
    )
    return scored, errors


def _deduplicate(tenders: List[Tender]) -> List[Tender]:
    """
    Remove near-duplicate tenders across sources using Jaccard title similarity.
    Prefers Find a Tender entries when duplicates are found.

    Uses an inverted token index so each new tender is only compared against
    candidates that share at least one title word, avoiding O(n²) comparisons.
    """
    unique: List[Tender]       = []
    seen_token_sets: List[set] = []
    inverted: dict[str, List[int]] = {}  # token → indices into unique

    for tender in tenders:
        tokens = set(_normalise(tender.title).split())
        if not tokens:
            unique.append(tender)
            continue

        candidate_indices: set[int] = set()
        for token in tokens:
            candidate_indices.update(inverted.get(token, []))

        is_dup = any(
            _jaccard(tokens, seen_token_sets[i]) >= _DEDUP_THRESHOLD
            for i in candidate_indices
        )

        if not is_dup:
            idx = len(unique)
            unique.append(tender)
            seen_token_sets.append(tokens)
            for token in tokens:
                inverted.setdefault(token, []).append(idx)

    logger.debug("Deduplication: %d → %d tenders", len(tenders), len(unique))
    return unique


def _normalise(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similarity(a: str, b: str) -> float:
    return _jaccard(set(a.split()), set(b.split()))
