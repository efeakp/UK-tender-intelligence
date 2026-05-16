"""
Contracts Finder (CF) API client.

API docs: https://www.contractsfinder.service.gov.uk/apidocumentation
Endpoint: POST /Published/Notices/OCDS/Search

Per the official CF OCDS Implementation Guide v2.1 (Crown Commercial Service):
  - Method: POST with JSON body
  - Accepts: publishedFrom, publishedTo, stages, orderBy, order, size, page
  - Returns: { hitsCount, maxPage, releases: [ <OCDS release>, ... ] }

OCDS release structure:
  release.ocid               → unique notice ID
  release.date               → publication date
  release.tag                → ["tender"] | ["tenderUpdate"] | ["award"] | ["planning"]
  release.buyer.name         → contracting authority
  release.tender.status      → "active" | "planned" | "cancelled" | "complete"
  release.tender.*           → title, description, value, tenderPeriod, items (CPV)
  release.awards[].value     → awarded contract value

Authoritative CF → OCDS mapping (Appendix B of the official guide):
┌─────────────────────┬───────────────┬──────────────────┬────────────────────────┐
│ CF Notice Type      │ CF Status     │ OCDS tag         │ OCDS tender.status     │
├─────────────────────┼───────────────┼──────────────────┼────────────────────────┤
│ Opportunity         │ Open          │ tender           │ active                 │
│ Opportunity         │ Closed        │ tenderUpdate     │ active                 │
│ Opportunity         │ Awarded       │ award            │ complete / active      │
│ Future Opportunity  │ Open          │ planning         │ planned                │
│ Future Opportunity  │ Withdrawn     │ planning         │ cancelled              │
│ Early Engagement    │ Open          │ planning         │ planned                │
│ Early Engagement    │ Withdrawn     │ planning         │ cancelled              │
└─────────────────────┴───────────────┴──────────────────┴────────────────────────┘

KEY INSIGHT from Appendix B:
  Future Opportunity and Early Engagement are IDENTICAL in OCDS terms — both use
  tag=["planning"] and tender.status="planned". The CF OCDS API does not expose
  enough data to distinguish them reliably. We therefore use description text as
  a secondary signal: keywords like "market engagement", "soft market testing",
  "preliminary" and "early engagement" indicate an Early Engagement notice.
  All other planning notices are classified as Future Opportunity.
"""

import asyncio
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import httpx

from app.config import settings
from app.models.tender import Tender, TenderSource

CF_MAX_RETRIES    = 3
CF_RETRY_BACKOFF  = 60  # seconds; multiplied by retry_count on each attempt

logger = logging.getLogger(__name__)

# OCDS stages to query (per Appendix B of CF OCDS Guide v2.1)
# planning — Future Opportunity and Early Engagement (tag=planning, status=planned)
# tender   — Opportunity open (tag=tender) and closed (tag=tenderUpdate)
# award    — Awarded Contract (tag=award, status=complete)
OCDS_STAGES = ["planning", "tender", "award"]

# ── Notice category constants ─────────────────────────────────────────────────

CATEGORY_EARLY_ENGAGEMENT   = "Early Engagement"   # planning/planned + engagement keywords
CATEGORY_FUTURE_OPPORTUNITY = "Future Opportunity" # planning/planned — pipeline
CATEGORY_OPPORTUNITY        = "Opportunity"        # tender/tenderUpdate active
CATEGORY_AWARDED            = "Awarded Contract"   # award/complete

# OCDS tags per Appendix B of CF OCDS Guide v2.1
AWARD_TAGS    = {"award"}
TENDER_TAGS   = {"tender", "tenderupdate"}   # tenderUpdate = closed opportunity
PLANNING_TAGS = {"planning"}

# Text patterns that distinguish Early Engagement from Future Opportunity
# when both arrive as tag=planning, status=planned (per Appendix B — identical OCDS)
EARLY_ENGAGEMENT_PATTERNS = re.compile(
    r"\b(early\s+engagement|market\s+engagement|soft\s+market|preliminary\s+market"
    r"|pre[- ]market|prior\s+information\s+notice|PIN\b|market\s+sounding"
    r"|request\s+for\s+information|RFI\b|expression\s+of\s+interest|EOI\b"
    r"|supplier\s+event|industry\s+day|pre[- ]procurement)\b",
    re.IGNORECASE,
)


def _is_early_engagement(title: str, description: str) -> bool:
    """
    Use description text as a secondary signal to distinguish Early Engagement
    from Future Opportunity when both arrive with tag=planning, status=planned.
    Per Appendix B of the CF OCDS guide, these are otherwise identical in OCDS.
    """
    return bool(EARLY_ENGAGEMENT_PATTERNS.search(f"{title} {description}"))


def _classify_cf_notice(
    tag: List[str],
    status: Optional[str],
    title: str,
    description: str,
) -> str:
    """
    Classify a CF OCDS release into a procurement stage category.

    Based on the authoritative mapping in Appendix B of CF OCDS Guide v2.1:

    1. tag = award                          → Awarded Contract
    2. tag = tender or tenderUpdate         → Opportunity (open or closed)
    3. tag = planning + status = cancelled  → Future Opportunity (withdrawn)
    4. tag = planning + engagement text     → Early Engagement
    5. tag = planning (all other)           → Future Opportunity
    6. status = active / complete           → Opportunity (fallback)
    7. Fallback                             → Opportunity
    """
    tag_set = {t.lower() for t in (tag or [])}

    # 1. Awarded — tag=award, status=complete/active
    if tag_set & AWARD_TAGS:
        return CATEGORY_AWARDED

    # 2. Active or closed tender
    #    Open opportunity:   tag=tender,       status=active
    #    Closed opportunity: tag=tenderUpdate, status=active
    if tag_set & TENDER_TAGS:
        return CATEGORY_OPPORTUNITY

    # 3 / 4 / 5. Planning notices — both Future Opportunity and Early Engagement
    #    use tag=planning + status=planned per Appendix B.
    #    Use description text to distinguish them.
    if tag_set & PLANNING_TAGS or status in ("planned", "cancelled"):
        if status == "cancelled":
            return CATEGORY_FUTURE_OPPORTUNITY
        if _is_early_engagement(title, description):
            return CATEGORY_EARLY_ENGAGEMENT
        return CATEGORY_FUTURE_OPPORTUNITY

    # 6. Status-based fallback
    if status in ("active", "complete"):
        return CATEGORY_OPPORTUNITY

    # 7. Final fallback
    return CATEGORY_OPPORTUNITY


def is_actionable(category: str, deadline: Optional[datetime]) -> bool:
    """
    Returns True if this notice is something Nordic Energy can actively
    bid on or should be tracking as an upcoming opportunity.
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
    Fetch recent notices from Contracts Finder via the OCDS Search endpoint.

    Paginates through all results for the look-back window, deduplicates by
    OCID, and returns raw (unscored) Tender objects.

    Now fetches planning stage notices (Future Opportunity + Early Engagement)
    in addition to tender and award stages.
    """
    tenders: List[Tender] = []
    seen_ocids: set[str] = set()

    date_from = (
        datetime.now(timezone.utc) - timedelta(days=days_back)
    ).strftime("%Y-%m-%dT00:00:00Z")

    page     = 1
    max_page = 1

    while page <= max_page:
        payload = {
            "searchCriteria": {
                "publishedFrom": date_from,
                "stages": OCDS_STAGES,
            },
            "orderBy": "publishedDate",
            "order":   "DESC",
            "size":    settings.cf_page_size,
            "page":    page,
        }

        fetched     = False
        retry_count = 0

        while not fetched and retry_count <= CF_MAX_RETRIES:
            try:
                resp = await client.post(
                    settings.cf_base_url,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Accept":       "application/json",
                    },
                    timeout=30.0,
                )
                resp.raise_for_status()
                data = resp.json()

                max_page = int(data.get("maxPage", 1))
                releases = data.get("releases", [])

                if not releases:
                    max_page = 0  # signal outer loop to stop
                    fetched  = True
                    break

                for release in releases:
                    ocid = release.get("ocid", "")
                    if ocid and ocid not in seen_ocids:
                        tender = _parse_ocds_release(release)
                        if tender:
                            tenders.append(tender)
                            seen_ocids.add(ocid)

                logger.debug("CF page %d/%d — %d releases", page, max_page, len(releases))
                page    += 1
                fetched  = True

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    retry_count += 1
                    if retry_count > CF_MAX_RETRIES:
                        logger.error("CF rate-limited — exceeded %d retries on page %d, stopping", CF_MAX_RETRIES, page)
                        max_page = 0
                        break
                    wait_s = int(e.response.headers.get("Retry-After", CF_RETRY_BACKOFF * retry_count))
                    logger.warning(
                        "CF rate-limited page %d — backoff %ds (attempt %d/%d)",
                        page, wait_s, retry_count, CF_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait_s)
                else:
                    logger.warning("CF HTTP error on page %d: %s", page, e)
                    max_page = 0
                    break

            except httpx.RequestError as e:
                logger.warning("CF request error on page %d: %s", page, e)
                max_page = 0
                break

            except Exception as e:
                logger.error("Unexpected CF error on page %d: %s", page, e, exc_info=True)
                max_page = 0
                break

        if not fetched:
            break

    from collections import Counter
    cat_counts = Counter(t.category for t in tenders)
    logger.info(
        "Contracts Finder: fetched %d tenders (%d pages) | %s",
        len(tenders),
        page - 1,
        " | ".join(f"{k}: {v}" for k, v in sorted(cat_counts.items())),
    )
    return tenders


def _parse_ocds_release(release: dict) -> Optional[Tender]:
    """
    Parse a single OCDS release from Contracts Finder into a Tender model.

    OCDS structure per CF OCDS Guide v2.1:
    {
      "ocid": "ocds-b5fd17-...",
      "date": "2025-04-18T10:00:00Z",
      "tag": ["tender"],                       ← tender | tenderUpdate | award | planning
      "buyer": { "name": "Manchester City Council" },
      "tender": {
        "title": "...",
        "description": "...",
        "status": "active",                    ← active | planned | cancelled | complete
        "value": { "amount": 85000, "currency": "GBP" },
        "tenderPeriod": { "endDate": "2025-05-16T12:00:00Z" },
        "items": [{ "classification": { "id": "45232140", "scheme": "CPV" } }]
      },
      "awards": [{ "value": { "amount": 120000, "currency": "GBP" } }]
    }
    """
    try:
        ocid = release.get("ocid", "unknown")
        tag  = release.get("tag", [])

        tender_block = release.get("tender", {})
        buyer        = release.get("buyer", {})

        title       = tender_block.get("title") or release.get("title", "Untitled")
        description = tender_block.get("description") or ""
        authority   = buyer.get("name", "Unknown Authority")
        status      = tender_block.get("status")

        if status == "cancelled":
            logger.debug("Skipping CF release '%s' — status cancelled", ocid)
            return None

        # Value — prefer award value for awarded contracts
        value_amount: Optional[float] = None
        currency = "GBP"

        if "award" in [t.lower() for t in tag]:
            awards = release.get("awards", [])
            if awards:
                award_value  = awards[0].get("value", {})
                value_amount = award_value.get("amount")
                currency     = award_value.get("currency", "GBP")

        if value_amount is None:
            tender_value = tender_block.get("value", {})
            value_amount = tender_value.get("amount")
            currency     = tender_value.get("currency", "GBP")

        try:
            value_amount = float(value_amount) if value_amount is not None else None
        except (TypeError, ValueError):
            value_amount = None

        lots = tender_block.get("lots", [])
        lot_count = len(lots)
        if value_amount is None and lots:
            lot_amounts = []
            for lot in lots:
                la = lot.get("value", {}).get("amount")
                try:
                    if la is not None:
                        lot_amounts.append(float(la))
                except (TypeError, ValueError):
                    pass
            if lot_amounts:
                value_amount = sum(lot_amounts)
                currency = lots[0].get("value", {}).get("currency", "GBP")

        value_str = f"£{value_amount:,.0f}" if value_amount else "Value not stated"

        # Dates
        published = _parse_dt(release.get("date"))
        deadline  = _parse_dt(tender_block.get("tenderPeriod", {}).get("endDate"))

        # CPV codes
        cpv_codes: List[str] = []
        for item in tender_block.get("items", []):
            # Primary: singular classification (standard CF OCDS format)
            classification = item.get("classification", {})
            if isinstance(classification, dict) and classification.get("scheme", "").upper() == "CPV":
                cpv_id = classification.get("id", "")
                if cpv_id:
                    cpv_codes.append(cpv_id)
            # Fallback: additionalClassifications list (used by some CF and FaT notices)
            for ac in item.get("additionalClassifications", []):
                if isinstance(ac, dict) and ac.get("scheme", "").upper() == "CPV" and ac.get("id"):
                    cpv_codes.append(ac["id"])

        direct_class = tender_block.get("classification", {})
        if isinstance(direct_class, dict) and direct_class.get("id"):
            cpv_codes.append(direct_class["id"])
        elif isinstance(direct_class, list):
            for c in direct_class:
                if isinstance(c, dict) and c.get("id"):
                    cpv_codes.append(c["id"])

        cpv_codes = list(dict.fromkeys(cpv_codes))

        # NUTS delivery region codes
        nuts_codes: List[str] = []
        for item in tender_block.get("items", []):
            for addr in item.get("deliveryAddresses", []):
                region = addr.get("region", "")
                if region and region.upper().startswith("UK"):
                    nuts_codes.append(region.upper())
        nuts_codes = list(dict.fromkeys(nuts_codes))

        # Notice category — passes title + description for planning notice disambiguation
        category = _classify_cf_notice(tag, status, title, description)

        # Canonical URL — use release.id GUID (strip numeric version suffix)
        # CF release.id format: "{guid}-{version_number}" e.g. "abc123-896514"
        # Public notice URL: /Notice/{guid}
        release_id = release.get("id", "")
        if release_id:
            parts = release_id.rsplit("-", 1)
            notice_guid = parts[0] if len(parts) == 2 and parts[1].isdigit() else release_id
            url = f"https://www.contractsfinder.service.gov.uk/Notice/{notice_guid}"
        else:
            # Final fallback if no release.id
            url = f"https://www.contractsfinder.service.gov.uk/Notice/{ocid}"

        return Tender(
            id=f"CF-{ocid}",
            source=TenderSource.CONTRACTS_FINDER,
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
            ocid=ocid or "",
            nuts_codes=nuts_codes,
            lot_count=lot_count,
        )

    except Exception as e:
        logger.debug(
            "Failed to parse CF OCDS release '%s': %s",
            release.get("ocid"),
            e,
        )
        return None


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None