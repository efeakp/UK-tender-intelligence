"""
Find a Tender (FaT) API client.

API docs: https://www.find-tender.service.gov.uk/apidocumentation/1.0/GET-ocdsReleasePackages

Confirmed allowed parameters:
  - updatedFrom / updatedTo  : date range (ISO 8601)
  - stages                   : single stage value per request
  - limit                    : results per page
  - cursor                   : pagination token embedded in links.next URL

IMPORTANT: The FaT cursor URL only carries a single stage value, so passing
multiple stages causes cursor pages to drop some stages. We therefore make
three independent paginated fetches — planning, tender, award — and merge.

UK notice type → OCDS mapping (Procurement Act 2023):
  UK1  plannedProcurementNotice    → Future Opportunity
  UK2  marketEngagementNotice      → Early Engagement
  UK4  tenderNotice                → Opportunity
  UK5  contractAwardNotice         → Awarded Contract
  UK6  contractAwardNotice         → Awarded Contract (utilities)
  UK7  modificationNotice          → Awarded Contract (modification)

UK2 notices arrive with tag=["compiled"] — the actual notice type is stored
under planning.documents[].noticeType, NOT in the top-level tag field.
We inspect planning.documents to classify UK1/UK2 correctly.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import httpx

from app.config import settings
from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

PAGE_CAP = 20  # max pages per stage to avoid runaway pagination

# ── Notice category constants ─────────────────────────────────────────────────

CATEGORY_EARLY_ENGAGEMENT   = "Early Engagement"    # UK2 — PME / soft market testing
CATEGORY_FUTURE_OPPORTUNITY = "Future Opportunity"  # UK1 — pipeline / planned
CATEGORY_OPPORTUNITY        = "Opportunity"         # UK4 — active tender, bids open
CATEGORY_AWARDED            = "Awarded Contract"    # UK5/UK6/UK7 — contract let
CATEGORY_UNKNOWN            = "Unknown"

# OCDS top-level tags
AWARD_TAGS      = {"award", "contract"}
PIPELINE_TAGS   = {"pin", "prior-information", "planning", "pre-qualification"}
ENGAGEMENT_TAGS = {"market-engagement"}

# Procurement Act 2023 notice type → Nordic Energy category
# Source: FaT notice type documentation (official)
#
# FaT Stage    Notice  Description                          Actionable?
# ──────────── ─────── ──────────────────────────────────── ──────────
# Pipeline     UK1     Pipeline notice (>£2m future)        Monitor
# Planning     UK2     Preliminary market engagement        Monitor
# Planning     UK3     Planned procurement notice           Monitor
# Tender       UK4     Tender notice (bids open)            ✅ Bid
# Tender       UK5     Transparency notice (direct award)   Monitor
# Award        UK6     Contract award notice                Info only
# Contract     UK7     Contract details notice              Info only
# Contract     UK9     Contract performance notice          Info only
# Contract     UK10    Contract change notice               Info only
# Termination  UK11    Contract termination notice          Info only
# Termination  UK12    Procurement termination notice       Info only
# Pipeline     UK13    Dynamic market intention notice      Monitor
# Pipeline     UK14    Dynamic market establishment notice  Monitor
# Pipeline     UK15    Dynamic market modification notice   Monitor
# Termination  UK16    Dynamic market cessation notice      Info only
# Payments     UK17    Payments compliance notice           Info only

UK_NOTICE_TYPE_MAP = {
    # Pipeline stage — future opportunities to monitor
    "UK1":  CATEGORY_FUTURE_OPPORTUNITY,   # Pipeline notice (contracts >£2m)
    "UK13": CATEGORY_FUTURE_OPPORTUNITY,   # Dynamic market intention
    "UK14": CATEGORY_FUTURE_OPPORTUNITY,   # Dynamic market establishment
    "UK15": CATEGORY_FUTURE_OPPORTUNITY,   # Dynamic market modification

    # Planning stage — early engagement, bids not yet possible
    "UK2":  CATEGORY_EARLY_ENGAGEMENT,    # Preliminary market engagement
    # UK3 = Planned Procurement Notice — tender dropping within 40 days to 1 year
    # AND tendering period may be only 10 days (not the normal 25)
    # Flagged separately from UK2 so the dashboard can show urgency
    "UK3":  CATEGORY_EARLY_ENGAGEMENT,    # Planned procurement — imminent tender

    # Tender stage — active opportunities, bids can be submitted
    "UK4":  CATEGORY_OPPORTUNITY,         # Tender notice
    "UK5":  CATEGORY_OPPORTUNITY,         # Transparency notice (direct award)

    # Award / Contract / Termination — post-award information
    "UK6":  CATEGORY_AWARDED,             # Contract award notice
    "UK7":  CATEGORY_AWARDED,             # Contract details notice
    "UK9":  CATEGORY_AWARDED,             # Contract performance notice
    "UK10": CATEGORY_AWARDED,             # Contract change notice
    "UK11": CATEGORY_AWARDED,             # Contract termination notice
    "UK12": CATEGORY_AWARDED,             # Procurement termination notice
    "UK16": CATEGORY_AWARDED,             # Dynamic market cessation
    "UK17": CATEGORY_AWARDED,             # Payments compliance notice
}


def _extract_uk_notice_type(release: dict) -> Optional[str]:
    """
    Extract the most recent UK notice type from planning.documents[].noticeType.

    UK2 (market engagement) notices arrive with tag=["compiled"] — the actual
    notice type is buried in planning.documents, not in the top-level tag.
    We pick the LAST document entry as it represents the most recent notice
    in the procurement timeline.
    """
    planning  = release.get("planning", {})
    documents = planning.get("documents", [])
    uk_type   = None
    for doc in documents:
        nt = doc.get("noticeType", "")
        if nt.startswith("UK"):
            uk_type = nt  # keep iterating — last one wins
    return uk_type


def _classify_notice(
    tag: List[str],
    status: Optional[str],
    deadline: Optional[datetime],
    uk_notice_type: Optional[str],
) -> str:
    """
    Derive a human-readable notice category from OCDS fields.

    Classification order (most → least reliable):
    1. UK notice type from planning.documents  — definitive for PA2023 notices
    2. Top-level OCDS tag                      — award / engagement / pipeline
    3. tender.status field
    4. Fallback → Opportunity
    """
    tag_set = {t.lower() for t in (tag or [])}

    # 1. UK notice type — most reliable for Procurement Act 2023 notices
    if uk_notice_type and uk_notice_type in UK_NOTICE_TYPE_MAP:
        return UK_NOTICE_TYPE_MAP[uk_notice_type]

    # 2. Top-level OCDS tag
    if tag_set & AWARD_TAGS:
        return CATEGORY_AWARDED

    if tag_set & ENGAGEMENT_TAGS:
        return CATEGORY_EARLY_ENGAGEMENT

    if tag_set & PIPELINE_TAGS:
        return CATEGORY_FUTURE_OPPORTUNITY

    # 3. tender.status
    if "tender" in tag_set or status in ("active", "open"):
        return CATEGORY_OPPORTUNITY

    if status == "planned":
        return CATEGORY_FUTURE_OPPORTUNITY

    # 4. Fallback — "compiled" tag is used by FaT for aggregated records
    #    Check tender status for additional signal before falling through
    if status == "planned":
        return CATEGORY_FUTURE_OPPORTUNITY
    if status in ("active", "open"):
        return CATEGORY_OPPORTUNITY
    # Final fallback
    return CATEGORY_OPPORTUNITY


def is_open_opportunity(category: str, deadline: Optional[datetime]) -> bool:
    """
    Returns True if this notice represents something Nordic Energy can
    actively bid on or should be monitoring as an upcoming opportunity.
    """
    now = datetime.now(timezone.utc)
    if category == CATEGORY_OPPORTUNITY and deadline and deadline > now:
        return True
    if category in (CATEGORY_FUTURE_OPPORTUNITY, CATEGORY_EARLY_ENGAGEMENT):
        return True
    return False


async def fetch_tenders(
    client: httpx.AsyncClient,
    days_back: int = 30,
) -> List[Tender]:
    """
    Fetch recent notices from Find a Tender.

    Makes three independent paginated fetches (planning, tender, award) and
    merges the results, deduplicating by notice ID.

    Stages (only valid FaT API values — "pipeline" returns 400):
      planning — UK1 Pipeline + UK2 PME + UK3 Planned Procurement (Future Opportunity / Early Engagement)
      tender   — UK4 Tender notice + UK5 Transparency notice (Opportunity)
      award    — UK6 Contract award notice (Awarded Contract), 14-day window
    Returns raw (unscored) Tender objects.
    """
    date_from = (
        datetime.now(timezone.utc) - timedelta(days=days_back)
    ).strftime("%Y-%m-%dT00:00:00")

    headers = {"Accept": "application/json"}
    if settings.fat_api_key:
        headers["Authorization"] = f"Bearer {settings.fat_api_key}"

    seen_ids: set[str] = set()
    all_tenders: List[Tender] = []

    # Wider windows for pipeline/planning — UK1 notices are published once and
    # never updated, so we need to look back further to catch them. Tender stage
    # also extended to 60 days to capture notices published just before the
    # previous refresh boundary.
    # Valid FaT API stage values (official docs): planning, tender, award.
    # "pipeline" is NOT a valid value — returns 400 Bad Request.
    # UK1 pipeline notices are returned by stages=planning alongside UK2/UK3.
    # Award window kept shorter than planning/tender — a full 30-day window
    # fetches 17+ pages, triggers rate-limiting, and takes ~5 minutes with
    # minimal added intelligence value beyond the most recent 14 days.
    STAGE_DAYS_BACK = {
        "planning": max(days_back, 60),  # UK1 pipeline + UK2 PME + UK3 Planned Procurement
        "tender":   max(days_back, 60),  # UK4 tender + UK5 transparency
        "award":    min(days_back, 14),  # UK6 contract award — 14 days sufficient
    }

    for stage in ("planning", "tender", "award"):
        # FaT stages per official documentation:
        # planning  → UK1 Pipeline, UK2 PME, UK3 Planned Procurement, UK13-15 Dynamic Markets
        # tender    → UK4 Tender notice, UK5 Transparency notice
        # award     → UK6 Contract award notice
        # Excluded: contract (UK7/UK9-12), termination, payments — not actionable for NE
        stage_days = STAGE_DAYS_BACK.get(stage, days_back)
        stage_date_from = (
            datetime.now(timezone.utc) - timedelta(days=stage_days)
        ).strftime("%Y-%m-%dT00:00:00")
        stage_tenders = await _fetch_stage(
            client=client,
            stage=stage,
            date_from=stage_date_from,
            headers=headers,
            seen_ids=seen_ids,
            stage_hint=stage,
        )
        all_tenders.extend(stage_tenders)

    from collections import Counter
    cat_counts = Counter(t.category for t in all_tenders)
    logger.info(
        "Find a Tender: fetched %d tenders total | %s",
        len(all_tenders),
        " | ".join(f"{k}: {v}" for k, v in sorted(cat_counts.items())),
    )
    return all_tenders


# Notice type priority — higher number = higher priority
# When same OCID appears in multiple releases, keep the highest priority one
NOTICE_PRIORITY = {
    "UK1": 10,   # Pipeline — most important for early intelligence
    "UK2": 8,    # Preliminary market engagement
    "UK3": 7,    # Planned procurement
    "UK4": 9,    # Tender — active bid opportunity
    "UK5": 6,    # Transparency notice
    "UK6": 4,    # Contract award
    "UK7": 3,    # Contract details
    "UK9": 2,    # Performance
    "UK10": 2,   # Change
    "UK11": 1,   # Termination
    "UK12": 1,   # Procurement termination
}


def _notice_priority(tender) -> int:
    """Return priority score for a tender based on its notice type."""
    # Extract notice type from the tender id suffix or matched keywords
    # The notice type is stored in planning.documents[*].noticeType during parsing
    # We store it in tender.__dict__ for comparison
    nt = getattr(tender, '_notice_type', None) or tender.__dict__.get('_notice_type', '')
    return NOTICE_PRIORITY.get(nt, 5)


# ── Rate limiting constants ───────────────────────────────────────────────────
INTER_PAGE_DELAY_S = 1.5        # Proactive delay between pages (prevents triggering 429)
RETRY_BACKOFF_BASE = 60         # Initial backoff on 429 (seconds)
RETRY_BACKOFF_MULTIPLIER = 2    # Each retry doubles the wait: 60s → 120s → 240s
MAX_RETRIES = 3                 # Maximum retry attempts before giving up on a page


async def _fetch_stage(
    client: httpx.AsyncClient,
    stage: str,
    date_from: str,
    headers: dict,
    seen_ids: set,
    stage_hint: str = "",
) -> List[Tender]:
    """
    Paginate through all results for a single OCDS stage.

    Rate limiting strategy:
    - Inter-page delay of 1.5s between every page to avoid triggering 429
    - On 429: exponential backoff (60s → 120s → 240s), honouring Retry-After header
    - Maximum 3 retries per page before giving up and stopping the stage
    """
    import asyncio

    tenders: List[Tender] = []
    page = 0

    initial_params = {
        "updatedFrom": date_from,
        "limit": str(settings.fat_page_size),
        "stages": stage,
    }

    url: Optional[str] = settings.fat_base_url
    params = initial_params

    while url and page < PAGE_CAP:
        retry_count = 0
        while retry_count <= MAX_RETRIES:
            try:
                resp = await client.get(url, params=params, headers=headers, timeout=30.0)
                resp.raise_for_status()
                data = resp.json()

                releases = data.get("releases", [])
                if not releases:
                    url = None  # signal outer loop to stop
                    break

                for release in releases:
                    # Use release.id (notice number) as dedup key — NOT the OCID.
                    # This allows multiple releases sharing an OCID (e.g. UK1 + UK2
                    # from the same procurement family) to each appear separately.
                    release_id = release.get("id", "")
                    ocid_key   = release.get("ocid", "")
                    dedup_key  = release_id or ocid_key
                    if not dedup_key or dedup_key in seen_ids:
                        continue
                    tender = _parse_fat_release(release, stage_hint=stage_hint)
                    if not tender:
                        continue
                    tenders.append(tender)
                    seen_ids.add(dedup_key)

                page += 1
                logger.debug("FaT stage=%s page=%d — %d releases", stage, page, len(releases))

                next_url = data.get("links", {}).get("next")
                if next_url:
                    url    = next_url
                    params = None
                    # ── Proactive inter-page throttle ──────────────────────────
                    # Small delay between every page prevents the server pushing
                    # back with a 429 after a burst of back-to-back requests.
                    await asyncio.sleep(INTER_PAGE_DELAY_S)
                else:
                    url = None  # no more pages

                break  # success — exit retry loop

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    retry_count += 1
                    if retry_count > MAX_RETRIES:
                        logger.error(
                            "FaT stage=%s page=%d — exceeded %d retries after rate limiting, stopping stage",
                            stage, page, MAX_RETRIES,
                        )
                        url = None  # stop the stage
                        break

                    # ── Exponential backoff with Retry-After header check ──────
                    retry_after = e.response.headers.get("Retry-After")
                    if retry_after and retry_after.isdigit():
                        wait_s = int(retry_after)
                        logger.warning(
                            "FaT rate-limited (stage=%s, page=%d, attempt=%d/%d) "
                            "— Retry-After header says %ds",
                            stage, page, retry_count, MAX_RETRIES, wait_s,
                        )
                    else:
                        wait_s = RETRY_BACKOFF_BASE * (RETRY_BACKOFF_MULTIPLIER ** (retry_count - 1))
                        logger.warning(
                            "FaT rate-limited (stage=%s, page=%d, attempt=%d/%d) "
                            "— exponential backoff %ds",
                            stage, page, retry_count, MAX_RETRIES, wait_s,
                        )
                    await asyncio.sleep(wait_s)
                    # Loop back to retry same page

                else:
                    logger.warning("FaT HTTP error (stage=%s, page=%d): %s", stage, page, e)
                    url = None
                    break

            except httpx.RequestError as e:
                retry_count += 1
                if retry_count <= MAX_RETRIES:
                    wait_s = RETRY_BACKOFF_BASE * (RETRY_BACKOFF_MULTIPLIER ** (retry_count - 1))
                    logger.warning(
                        "FaT request error (stage=%s, page=%d, attempt=%d/%d) — retrying in %ds: %s",
                        stage, page, retry_count, MAX_RETRIES, wait_s, e,
                    )
                    await asyncio.sleep(wait_s)
                else:
                    logger.warning("FaT request error (stage=%s, page=%d): %s — giving up", stage, page, e)
                    url = None
                    break
            except Exception as e:
                logger.error("Unexpected FaT error (stage=%s, page=%d): %s", stage, page, e, exc_info=True)
                url = None
                break

        if url is None:
            break

    if page >= PAGE_CAP:
        logger.info("FaT stage=%s: reached page cap (%d), stopping", stage, PAGE_CAP)

    from collections import Counter
    cat_counts = Counter(t.category for t in tenders)
    logger.info(
        "FaT stage=%s: fetched %d tenders (%d pages) | %s",
        stage, len(tenders), page,
        " | ".join(f"{k}: {v}" for k, v in sorted(cat_counts.items())) or "none",
    )
    return tenders


def _parse_fat_release(release: dict, stage_hint: str = "") -> Optional[Tender]:
    """Parse an OCDS release from Find a Tender into a Tender model."""
    try:
        ocid      = release.get("ocid", "")
        notice_id = release.get("id", ocid)

        tender_block = release.get("tender", {})
        buyer        = release.get("buyer", {})

        title       = tender_block.get("title") or release.get("description", "Untitled")
        # UK1/UK2 Pipeline notices store description in planning.rationale
        # not in tender.description — fall back to read it from there
        description = (
            tender_block.get("description")
            or release.get("planning", {}).get("rationale", "")
            or release.get("description", "")
        )
        authority   = buyer.get("name", "Unknown Authority")

        # Value — prefer award value for awarded notices
        value_amount: Optional[float] = None
        currency = "GBP"

        tag = release.get("tag", [])
        if "award" in tag:
            awards = release.get("awards", [])
            if awards:
                av           = awards[0].get("value", {})
                value_amount = av.get("amount")
                currency     = av.get("currency", "GBP")

        if value_amount is None:
            tv           = tender_block.get("value", {})
            value_amount = tv.get("amount")
            currency     = tv.get("currency", "GBP")

        # UK1/UK2 notices may carry value in planning.budget instead
        if value_amount is None:
            budget = release.get("planning", {}).get("budget", {})
            if isinstance(budget, dict):
                amt = budget.get("amount", {})
                value_amount = amt.get("amount") if isinstance(amt, dict) else amt

        try:
            value_amount = float(value_amount) if value_amount is not None else None
        except (TypeError, ValueError):
            value_amount = None

        value_str = f"£{value_amount:,.0f}" if value_amount else "Value not stated"

        # Dates
        published = _parse_dt(release.get("date") or release.get("publishedDate"))
        # Deadline: try tenderPeriod first, then planning milestone (UK2 PME notices
        # store the engagement deadline in planning.milestones[].dueDate)
        deadline = _parse_dt(tender_block.get("tenderPeriod", {}).get("endDate"))
        if not deadline:
            milestones = release.get("planning", {}).get("milestones", [])
            for ms in milestones:
                d = _parse_dt(ms.get("dueDate"))
                if d:
                    deadline = d
                    break
        # Also store the expected tender notice date as a useful signal
        future_notice_date = _parse_dt(
            tender_block.get("communication", {}).get("futureNoticeDate")
        )

        # UK2 notices use enquiryPeriod for the engagement deadline
        if deadline is None:
            deadline = _parse_dt(tender_block.get("enquiryPeriod", {}).get("endDate"))

        # CPV codes
        cpv_codes: List[str] = []
        for item in tender_block.get("items", []):
            # FaT OCDS uses additionalClassifications (not classification) for CPVs
            for c in item.get("additionalClassifications", []):
                if c.get("scheme", "").upper() == "CPV" and c.get("id"):
                    cpv_codes.append(c["id"])
            # Also check classification (older releases)
            c = item.get("classification", {})
            if c.get("scheme", "").upper() == "CPV" and c.get("id"):
                cpv_codes.append(c["id"])
        # Top-level classification fallback
        direct = tender_block.get("classification", {})
        if isinstance(direct, dict) and direct.get("id"):
            cpv_codes.append(direct["id"])
        cpv_codes = list(dict.fromkeys(cpv_codes))

        # NUTS delivery region codes
        nuts_codes: List[str] = []
        for item in tender_block.get("items", []):
            for addr in item.get("deliveryAddresses", []):
                region = addr.get("region", "")
                if region and region.upper().startswith("UK"):
                    nuts_codes.append(region.upper())
        nuts_codes = list(dict.fromkeys(nuts_codes))

        # Notice category — inspect planning.documents for UK notice type first
        uk_notice_type = _extract_uk_notice_type(release)
        status         = tender_block.get("status")

        SKIP_STATUSES = {"cancelled", "withdrawn", "unsuccessful"}
        if status in SKIP_STATUSES:
            logger.debug("Skipping FaT release '%s' — status=%s", notice_id, status)
            return None

        category       = _classify_notice(tag, status, deadline, uk_notice_type)

        # For UK3 Planned Procurement Notices, use futureNoticeDate as a proxy deadline
        # if no tenderPeriod.endDate is present — it tells us when the UK4 is expected
        if not deadline and future_notice_date and uk_notice_type == "UK3":
            deadline = future_notice_date

        # Canonical URL — use the notice ID from the last planning.documents
        # entry when available (more reliable direct link than OCID suffix)
        planning_docs = release.get("planning", {}).get("documents", [])
        notice_url_id = planning_docs[-1].get("id", notice_id) if planning_docs else notice_id
        url = f"https://www.find-tender.service.gov.uk/Notice/{notice_url_id}"

        # Use notice_id (release.id e.g. "037689-2026") as the tender ID
        # so related notices sharing an OCID each get a unique identifier.
        # Falls back to OCID if notice_id is not available.
        tender_id = notice_id or ocid
        tender_obj = Tender(
            id=f"FAT-{tender_id}",
            source=TenderSource.FIND_A_TENDER,
            title=title,
            authority=authority,
            description=description,
            value=value_str,
            value_amount=value_amount,
            value_currency=currency,
            published=published,
            deadline=deadline,
            url=url,
            cpv_codes=cpv_codes,
            category=category,
            notice_type=uk_notice_type or "",
            ocid=ocid or "",
            nuts_codes=nuts_codes,
            future_notice_date=future_notice_date,
        )
        return tender_obj

    except Exception as e:
        logger.debug("Failed to parse FaT release '%s': %s", release.get("ocid"), e)
        return None


async def fetch_notice_by_id(notice_id: str) -> Optional[Tender]:
    """
    Fetch a specific notice directly by its FaT notice ID (e.g. '037689-2026').
    Uses the OCDS ReleasePackage endpoint on the FaT notice page.
    """
    # Try multiple URL formats — FaT has inconsistent OCDS endpoints
    urls_to_try = [
        f"https://www.find-tender.service.gov.uk/api/1.0/ocdsReleasePackages/{notice_id}",
        f"https://www.find-tender.service.gov.uk/Notice/{notice_id}/OCDS/ReleasePackage",
        f"{settings.fat_base_url}/{notice_id}",
    ]
    url = urls_to_try[0]  # Start with first, loop below tries others
    headers = {"Accept": "application/json"}
    if settings.fat_api_key:
        headers["Authorization"] = f"Bearer {settings.fat_api_key}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for try_url in urls_to_try:
            try:
                resp = await client.get(try_url, headers=headers, timeout=30.0)
                if resp.status_code == 404:
                    logger.debug("Direct fetch 404 at %s", try_url)
                    continue
                resp.raise_for_status()
                data = resp.json()
                releases = data.get("releases", [])
                if releases:
                    tender = _parse_fat_release(releases[0])
                    if tender:
                        logger.info("Direct fetch: found notice %s — '%s'", notice_id, tender.title)
                        return tender
            except httpx.HTTPStatusError:
                continue
            except Exception as e:
                logger.warning("Direct fetch error for %s at %s: %s", notice_id, try_url, e)
                continue
    logger.warning("Direct fetch: notice %s not found on any endpoint", notice_id)
    return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            v  = value[:26] if "." in value else value
            dt = datetime.strptime(v, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None