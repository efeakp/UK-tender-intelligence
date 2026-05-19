"""
Market intelligence service: CPV-relevant awarded contracts over extended lookback.

Lookback windows (balance between data richness and API rate limits):
  S2W, PCS — 365 days  (monthly-based API makes 12-month fetch efficient)
  CF        — 180 days  (award stage only, page cap of 40 prevents runaway)
  FaT       —  30 days  (award stage is heavily rate-limited on FaT)
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import List

import httpx

from app.models.tender import Tender
from app.services.scorer import NE_CPV_SET, score_tender

logger = logging.getLogger(__name__)

S2W_PCS_DAYS = 365
CF_DAYS      = 180
FAT_DAYS     = 30
CF_PAGE_CAP  = 40   # max pages to paginate on CF awards — prevents multi-hour runs


def _is_cpv_relevant(tender: Tender) -> bool:
    """Return True if any CPV code matches Nordic Energy's taxonomy (exact or prefix)."""
    for code in (tender.cpv_codes or []):
        clean = code.replace("-", "").strip()[:8]
        if clean in NE_CPV_SET:
            return True
        for prefix_len in (6, 4):
            prefix = clean[:prefix_len]
            if any(k.startswith(prefix) for k in NE_CPV_SET):
                return True
    return False


async def _fetch_cf_award_releases(days_back: int) -> List[Tender]:
    """
    Fetch award-stage notices from Contracts Finder (award stage only).
    Capped at CF_PAGE_CAP pages to prevent very long runs on large date windows.
    """
    from app.config import settings
    from app.services.contracts_finder import (
        _parse_ocds_release,
        CATEGORY_AWARDED,
        CF_MAX_RETRIES,
        CF_RETRY_BACKOFF,
        CF_NETWORK_RETRY_DELAY_S,
        CF_MAX_NETWORK_RETRIES,
    )

    date_from = (
        datetime.now(timezone.utc) - timedelta(days=days_back)
    ).strftime("%Y-%m-%dT00:00:00Z")

    tenders: List[Tender] = []
    seen_ocids: set = set()
    page = 1
    max_page = 1

    async with httpx.AsyncClient(follow_redirects=True) as client:
        while page <= max_page and page <= CF_PAGE_CAP:
            payload = {
                "searchCriteria": {
                    "publishedFrom": date_from,
                    "stages": ["award"],
                },
                "orderBy": "publishedDate",
                "order":   "DESC",
                "size":    settings.cf_page_size,
                "page":    page,
            }
            retry_count = 0
            network_retries = 0
            fetched = False

            while not fetched and retry_count <= CF_MAX_RETRIES:
                try:
                    resp = await client.post(
                        settings.cf_base_url,
                        json=payload,
                        headers={"Content-Type": "application/json", "Accept": "application/json"},
                        timeout=30.0,
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    max_page = min(int(data.get("maxPage", 1)), CF_PAGE_CAP)
                    releases = data.get("releases", [])
                    if not releases:
                        max_page = 0
                        fetched = True
                        break
                    for release in releases:
                        ocid = release.get("ocid", "")
                        if ocid and ocid not in seen_ocids:
                            t = _parse_ocds_release(release)
                            if t and t.category == CATEGORY_AWARDED:
                                tenders.append(t)
                                seen_ocids.add(ocid)
                    logger.debug("Market CF: page %d/%d — %d total", page, max_page, len(tenders))
                    page += 1
                    fetched = True
                    await asyncio.sleep(0.5)

                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429:
                        retry_count += 1
                        if retry_count > CF_MAX_RETRIES:
                            logger.warning("Market CF: rate-limited, stopping at page %d", page)
                            max_page = 0
                            break
                        wait_s = int(e.response.headers.get("Retry-After", CF_RETRY_BACKOFF * retry_count))
                        logger.warning("Market CF: 429 backoff %ds (attempt %d)", wait_s, retry_count)
                        await asyncio.sleep(wait_s)
                    else:
                        logger.warning("Market CF HTTP error page %d: %s", page, e)
                        max_page = 0
                        break

                except httpx.RequestError as e:
                    network_retries += 1
                    if network_retries <= CF_MAX_NETWORK_RETRIES:
                        logger.warning("Market CF network error page %d, retrying: %s", page, e)
                        await asyncio.sleep(CF_NETWORK_RETRY_DELAY_S)
                    else:
                        logger.warning("Market CF network error page %d, giving up: %s", page, e)
                        max_page = 0
                        break

                except Exception as e:
                    logger.error("Market CF unexpected error page %d: %s", page, e, exc_info=True)
                    max_page = 0
                    break

            if not fetched:
                break

    logger.info(
        "Market CF awards: %d raw releases (%d days, %d pages fetched)",
        len(tenders), days_back, page - 1,
    )
    return tenders


async def fetch_market_awards() -> List[Tender]:
    """
    Fetch CPV-relevant awarded contracts from all four procurement sources.

    Sources and windows:
      FaT  — 30 days, award stage only (rate-limited)
      CF   — 180 days, award stage only, max 40 pages
      S2W  — 365 days, all award notice types via monthly API
      PCS  — 365 days, all award notice types via monthly API

    All results filtered to Nordic Energy's CPV taxonomy, scored, deduplicated,
    and returned sorted by published date descending.
    """
    from app.services.find_a_tender import _fetch_stage
    from app.services.sell2wales import fetch_tenders as s2w_fetch
    from app.services.public_contracts_scotland import fetch_tenders as pcs_fetch
    from app.config import settings

    fat_date_from = (
        datetime.now(timezone.utc) - timedelta(days=FAT_DAYS)
    ).strftime("%Y-%m-%dT00:00:00")
    fat_headers = {"Accept": "application/json"}
    if settings.fat_api_key:
        fat_headers["Authorization"] = f"Bearer {settings.fat_api_key}"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        fat_r, s2w_r, pcs_r, cf_r = await asyncio.gather(
            _fetch_stage(
                client=client,
                stage="award",
                date_from=fat_date_from,
                headers=fat_headers,
                seen_ids=set(),
                stage_hint="award",
            ),
            s2w_fetch(client, days_back=S2W_PCS_DAYS),
            pcs_fetch(client, days_back=S2W_PCS_DAYS),
            _fetch_cf_award_releases(CF_DAYS),
            return_exceptions=True,
        )

    all_raw: List[Tender] = []
    for label, result in [("FaT", fat_r), ("S2W", s2w_r), ("PCS", pcs_r), ("CF", cf_r)]:
        if isinstance(result, Exception):
            logger.warning("Market awards %s failed: %s", label, result)
        else:
            all_raw.extend(result)

    # Keep only awarded contracts with NE-relevant CPV codes
    relevant: List[Tender] = []
    for t in all_raw:
        if t.category != "Awarded Contract":
            continue
        if _is_cpv_relevant(t):
            relevant.append(score_tender(t))

    # Deduplicate by tender id
    seen_ids: set = set()
    unique: List[Tender] = []
    for t in relevant:
        if t.id not in seen_ids:
            seen_ids.add(t.id)
            unique.append(t)

    min_dt = datetime.min.replace(tzinfo=timezone.utc)
    unique.sort(key=lambda t: t.published or min_dt, reverse=True)

    logger.info(
        "Market awards: %d CPV-relevant awarded contracts "
        "(FaT %dd / CF %dd / S2W+PCS %dd)",
        len(unique), FAT_DAYS, CF_DAYS, S2W_PCS_DAYS,
    )
    return unique
