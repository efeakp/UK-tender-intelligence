"""
Sell2Wales API client.

API docs: https://api.sell2wales.gov.wales/v1/Notices

Endpoint: GET /v1/Notices
Parameters:
  dateFrom   : Month and year in format 'mm-yyyy'. Default is current month.
  noticeType : See notice type list below. Default is 2 (Contract Notice).
  outputType : 0 = OCDS format, 1 = TED/custom format. Default is 0.
  locale     : Documented values: 2057 = English, 1106 = Welsh.
               WARNING: locale=2057 (and omitting locale entirely) triggers a SQL
               cast error on the S2W server ("Error converting data type nvarchar
               to float"). The only working value found via live testing is locale=0.

Notice type status (verified via live API testing, May 2026):

  WORKING — return HTTP 200 with locale=0:
  2  — Contract Notice (OJEU F2)                             → Opportunity
  5  — Contract Notice (Utilities, OJEU F5)                  → Opportunity
  6  — Contract Award Notice (Utilities, OJEU F6)            → Awarded Contract
  15 — Voluntary Ex Ante Transparency Notice (OJEU F15)      → Awarded Contract
  20 — Modification Notice (OJEU F20)                        → Awarded Contract
  21 — Social and other Specific Services (OJEU F21)         → Opportunity
  22 — Social and other Specific Services (Utilities, F22)   → Opportunity
  23 — Social and other Specific Services (Concessions, F23) → Opportunity
  25 — Concession Award Notice (OJEU F25)                    → Awarded Contract
  54 — Sub Contract Pre Award                                → Opportunity
  55 — Sub Contract Post Award                               → Awarded Contract
  56 — Sub Contract Award                                    → Awarded Contract

  BROKEN — return HTTP 500 regardless of locale:
  1  — Prior Information Notice (OJEU F1)
  3  — Contract Award Notice (OJEU F3)
  4  — Prior Information Notice (Utilities, OJEU F4)
  7  — Qualification Systems (Utilities, OJEU F7)
  24 — Concession Notice (OJEU F24)
  51 — Website Invitation to Tender Notice
  52 — Website Prior Information Notice
  53 — Website Contract Award Notice

Note: As of May 2026, the S2W OCDS API returns 200 with an empty releases array
for all working notice types — the response contains only the OCDS package header.
This is a server-side data population issue. The client is structured to handle
data correctly once S2W restores their OCDS release export.

Strategy: Sell2Wales only supports monthly date ranges (mm-yyyy). We iterate over
the required months (derived from days_back) and fetch each working notice type per
month, deduplicating by OCID.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from calendar import monthrange

import httpx

from app.config import settings
from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.sell2wales.gov.wales/v1/Notices"

# locale=0 is the only value that does not trigger the S2W SQL cast error.
# locale=2057 (documented English value) and omitting locale both return HTTP 500.
LOCALE = 0

# Notice types confirmed to return HTTP 200 with locale=0 (May 2026 live test).
# Types 1, 3, 4, 7, 24, 51, 52, 53 return HTTP 500 and are excluded.
NOTICE_TYPES = {
    # ── OJEU / Above-threshold notices ───────────────────────────────────────
    2:  ("Opportunity",        "Contract Notice"),
    5:  ("Opportunity",        "Contract Notice (Utilities)"),
    6:  ("Awarded Contract",   "Contract Award Notice (Utilities)"),
    15: ("Awarded Contract",   "Voluntary Ex Ante Transparency"),
    20: ("Awarded Contract",   "Modification Notice"),
    21: ("Opportunity",        "Social and other Specific Services"),
    22: ("Opportunity",        "Social and other Specific Services (Utilities)"),
    23: ("Opportunity",        "Social and other Specific Services (Concessions)"),
    25: ("Awarded Contract",   "Concession Award Notice"),
    # ── Sub-contract notices ─────────────────────────────────────────────────
    54: ("Opportunity",        "Sub Contract Pre Award"),
    55: ("Awarded Contract",   "Sub Contract Post Award"),
    56: ("Awarded Contract",   "Sub Contract Award"),
}

# Category constants (aligned with FaT and CF clients)
CATEGORY_EARLY_ENGAGEMENT   = "Early Engagement"
CATEGORY_FUTURE_OPPORTUNITY = "Future Opportunity"
CATEGORY_OPPORTUNITY        = "Opportunity"
CATEGORY_AWARDED            = "Awarded Contract"


async def fetch_tenders(
    client: httpx.AsyncClient,
    days_back: int = 30,
) -> List[Tender]:
    """
    Fetch recent notices from Sell2Wales.

    Iterates over months covered by days_back, fetching each notice type
    per month. Deduplicates by OCID. Returns raw (unscored) Tender objects.
    """
    # Determine which months to fetch
    months = _months_to_fetch(days_back)
    seen_ocids: set[str] = set()
    all_tenders: List[Tender] = []

    for month_str in months:
        for notice_type, (category, _) in NOTICE_TYPES.items():
            try:
                tenders = await _fetch_month(
                    client=client,
                    month_str=month_str,
                    notice_type=notice_type,
                    category=category,
                    seen_ocids=seen_ocids,
                )
                all_tenders.extend(tenders)
            except Exception as e:
                logger.warning(
                    "Sell2Wales fetch error (month=%s, type=%d): %s",
                    month_str, notice_type, e,
                )

    from collections import Counter
    cat_counts = Counter(t.category for t in all_tenders)
    logger.info(
        "Sell2Wales: fetched %d tenders (%d months) | %s",
        len(all_tenders),
        len(months),
        " | ".join(f"{k}: {v}" for k, v in sorted(cat_counts.items())),
    )
    return all_tenders


async def _fetch_month(
    client: httpx.AsyncClient,
    month_str: str,
    notice_type: int,
    category: str,
    seen_ocids: set,
) -> List[Tender]:
    """Fetch a single month + notice type combination from Sell2Wales."""
    tenders: List[Tender] = []

    params = {
        "dateFrom":   month_str,
        "noticeType": notice_type,
        "outputType": 0,      # OCDS format
        "locale":     LOCALE, # locale=0 is the only value that avoids the S2W SQL cast error
    }

    try:
        resp = await client.get(
            BASE_URL,
            params=params,
            headers={
                "Accept":     "application/json",
                # Sell2Wales blocks python-httpx/* UA with 403; browser UA required
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # Sell2Wales OCDS response: either a list of releases or
        # a dict with a "releases" key — handle both
        releases = []
        if isinstance(data, list):
            releases = data
        elif isinstance(data, dict):
            releases = data.get("releases", data.get("data", []))

        if not releases:
            logger.debug("Sell2Wales: no releases for month=%s type=%d", month_str, notice_type)
            return tenders

        for release in releases:
            ocid = _get_ocid(release)
            if ocid and ocid not in seen_ocids:
                tender = _parse_release(release, category)
                if tender:
                    tenders.append(tender)
                    seen_ocids.add(ocid)

        logger.debug(
            "Sell2Wales month=%s type=%d: %d tenders",
            month_str, notice_type, len(tenders),
        )

    except httpx.HTTPStatusError as e:
        logger.warning(
            "Sell2Wales HTTP error (month=%s, type=%d): %s",
            month_str, notice_type, e,
        )
    except httpx.RequestError as e:
        logger.warning(
            "Sell2Wales request error (month=%s, type=%d): %s",
            month_str, notice_type, e,
        )

    return tenders


def _get_ocid(release: dict) -> str:
    """Extract OCID from a release, trying common field names."""
    return (
        release.get("ocid")
        or release.get("OCID")
        or release.get("id")
        or ""
    )


def _parse_release(release: dict, category: str) -> Optional[Tender]:
    """Parse a Sell2Wales OCDS release into a Tender model."""
    try:
        ocid         = _get_ocid(release)
        tender_block = release.get("tender", {})
        buyer        = release.get("buyer", {})

        status = tender_block.get("status", "")
        if status in {"cancelled", "withdrawn", "unsuccessful"}:
            logger.debug("Skipping S2W release '%s' — status=%s", _get_ocid(release), status)
            return None

        title       = tender_block.get("title") or release.get("title", "Untitled")
        description = tender_block.get("description") or release.get("description", "")
        authority   = (
            buyer.get("name")
            or release.get("buyer", {}).get("name")
            or "Unknown Authority"
        )

        # Value — prefer award value for awarded notices
        value_amount: Optional[float] = None
        currency = "GBP"
        tag = release.get("tag", [])

        if "award" in [t.lower() for t in tag]:
            awards = release.get("awards", [])
            if awards:
                av           = awards[0].get("value", {})
                value_amount = av.get("amount")
                currency     = av.get("currency", "GBP")

        if value_amount is None:
            tv           = tender_block.get("value", {})
            value_amount = tv.get("amount")
            currency     = tv.get("currency", "GBP")

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
        published = _parse_dt(release.get("date") or release.get("publishedDate"))
        deadline  = _parse_dt(
            tender_block.get("tenderPeriod", {}).get("endDate")
            or tender_block.get("enquiryPeriod", {}).get("endDate")
        )

        # CPV codes
        cpv_codes: List[str] = []
        for item in tender_block.get("items", []):
            c = item.get("classification", {})
            if c.get("scheme", "").upper() == "CPV" and c.get("id"):
                cpv_codes.append(c["id"])
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

        # Canonical URL — Sell2Wales notice page
        ocid_suffix = ocid.replace("ocds-kuma6s-", "").replace("ocds-", "")
        url = f"https://www.sell2wales.gov.wales/Search/Search_Switch.aspx?ID={ocid_suffix}"

        return Tender(
            id=f"S2W-{ocid}",
            source=TenderSource.SELL2WALES,
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
            nuts_codes=nuts_codes,
            lot_count=lot_count,
        )

    except Exception as e:
        logger.debug("Failed to parse Sell2Wales release '%s': %s", _get_ocid(release), e)
        return None


async def fetch_notice_by_ocid(ocid: str) -> Optional[Tender]:
    """
    Fetch a single S2W notice family by OCID via the /v1/Notice endpoint.
    Returns a parsed (unscored) Tender, or None if not found.
    """
    params = {"id": ocid, "outputType": 0, "locale": LOCALE}
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(
                "https://api.sell2wales.gov.wales/v1/Notice",
                params=params,
                headers=headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, list):
                release = data[0] if data else None
            elif isinstance(data, dict):
                releases = data.get("releases", [])
                release = releases[0] if releases else (data if data.get("ocid") else None)
            else:
                return None

            if not release:
                return None

            tag = release.get("tag", [])
            tag_lower = [t.lower() for t in tag]
            if "award" in tag_lower:
                category = CATEGORY_AWARDED
            elif any(t in tag_lower for t in ("planning", "prior-information")):
                category = CATEGORY_FUTURE_OPPORTUNITY
            else:
                category = CATEGORY_OPPORTUNITY

            return _parse_release(release, category)

        except httpx.HTTPStatusError as e:
            logger.warning("S2W direct fetch HTTP error for OCID %s: %s", ocid, e)
            return None
        except Exception as e:
            logger.warning("S2W direct fetch error for OCID %s: %s", ocid, e)
            return None


def _months_to_fetch(days_back: int) -> List[str]:
    """
    Return a list of month strings (mm-yyyy) covering the days_back window.
    Always includes the current month. Adds prior months as needed.
    """
    now    = datetime.now(timezone.utc)
    months = []

    # Walk back month by month
    current = now
    while True:
        months.append(current.strftime("%m-%Y"))
        # Check if going back one more month is still within window
        first_of_month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        if (now - first_of_month).days <= days_back:
            # Go back one month
            prev_month = first_of_month - timedelta(days=1)
            current = prev_month
            if len(months) > 6:  # safety cap — never fetch more than 6 months
                break
        else:
            break

    return months


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y",
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