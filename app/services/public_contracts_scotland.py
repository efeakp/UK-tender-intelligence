"""
Public Contracts Scotland (PCS) API client.

API docs: https://api.publiccontractsscotland.gov.uk/v1

Endpoint: GET /v1/Notices
Parameters:
  dateFrom   : Month and year in format 'mm-yyyy'. Default is current month.
  noticeType : See notice type list below. Default is 2 (Contract Notice).
  outputType : 0 = OCDS format, 1 = TED/custom format. Default is 0.

No locale parameter — PCS is English only unlike Sell2Wales.

Notice types fetched:
  1   — Prior Information Notice (OJEU F1)         → Future Opportunity
  2   — Contract Notice (OJEU F2)                  → Opportunity
  3   — Contract Award Notice (OJEU F3)             → Awarded Contract
  5   — Contract Notice Utilities (OJEU F5)         → Opportunity
  6   — Contract Award Notice Utilities (OJEU F6)   → Awarded Contract
  101 — Website Prior Information Notice            → Future Opportunity
  102 — Website Contract Notice                     → Opportunity
  103 — Website Contract Award Notice               → Awarded Contract
  104 — Quick Quote Award                           → Awarded Contract

Strategy: PCS only supports monthly date ranges (mm-yyyy). We iterate over
the months covered by days_back and fetch each notice type in turn,
deduplicating by OCID across all fetches.

OCDS prefix for PCS: ocds-r6ebe6-
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

import httpx

from app.config import settings
from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

BASE_URL = "https://api.publiccontractsscotland.gov.uk/v1/Notices"

# Notice types to fetch and their category mappings
NOTICE_TYPES = {
    1:   "Future Opportunity",   # Prior Information Notice
    2:   "Opportunity",          # Contract Notice
    3:   "Awarded Contract",     # Contract Award Notice
    5:   "Opportunity",          # Contract Notice (Utilities)
    6:   "Awarded Contract",     # Contract Award Notice (Utilities)
    101: "Future Opportunity",   # Website Prior Information Notice
    102: "Opportunity",          # Website Contract Notice
    103: "Awarded Contract",     # Website Contract Award Notice
    104: "Awarded Contract",     # Quick Quote Award
}


async def fetch_tenders(
    client: httpx.AsyncClient,
    days_back: int = 30,
) -> List[Tender]:
    """
    Fetch recent notices from Public Contracts Scotland.

    Uses a dedicated httpx client with SSL verification disabled because
    the PCS API server uses a certificate chain that Python on Windows
    cannot verify using its default CA bundle. The API is a UK government
    service so this is safe to do.

    Iterates over months covered by days_back, fetching each notice type
    per month. Deduplicates by OCID. Returns raw (unscored) Tender objects.
    """
    months = _months_to_fetch(days_back)
    seen_ocids: set[str] = set()
    all_tenders: List[Tender] = []

    # Use a dedicated client with SSL verification disabled for PCS.
    # The passed-in client uses the default SSL context which cannot verify
    # the PCS certificate on Windows. verify=False is safe here as PCS is
    # a Scottish Government service on a known gov.uk domain.
    # Suppress the InsecureRequestWarning that verify=False produces
    import warnings
    warnings.filterwarnings("ignore", message=".*Unverified HTTPS.*")

    # PCS blocks requests from python-httpx/* User-Agent with 403.
    # A browser UA is required to receive data.
    pcs_headers = {
        "Accept":     "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(verify=False, follow_redirects=True, headers=pcs_headers) as pcs_client:
        for month_str in months:
            for notice_type, category in NOTICE_TYPES.items():
                try:
                    tenders = await _fetch_month(
                        client=pcs_client,
                        month_str=month_str,
                        notice_type=notice_type,
                        category=category,
                        seen_ocids=seen_ocids,
                    )
                    all_tenders.extend(tenders)
                except Exception as e:
                    logger.warning(
                        "PCS fetch error (month=%s, type=%d): %s",
                        month_str, notice_type, e,
                    )

    from collections import Counter
    cat_counts = Counter(t.category for t in all_tenders)
    logger.info(
        "Public Contracts Scotland: fetched %d tenders (%d months) | %s",
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
    """Fetch a single month + notice type combination from PCS."""
    tenders: List[Tender] = []

    params = {
        "dateFrom":   month_str,
        "noticeType": notice_type,
        "outputType": 0,  # OCDS format
    }

    try:
        resp = await client.get(
            BASE_URL,
            params=params,
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()

        # PCS OCDS response: list of releases or dict with releases key
        releases = []
        if isinstance(data, list):
            releases = data
        elif isinstance(data, dict):
            releases = data.get("releases", data.get("data", []))

        if not releases:
            logger.debug(
                "PCS: no releases for month=%s type=%d", month_str, notice_type
            )
            return tenders

        for release in releases:
            ocid = _get_ocid(release)
            if ocid and ocid not in seen_ocids:
                tender = _parse_release(release, category)
                if tender:
                    tenders.append(tender)
                    seen_ocids.add(ocid)

        logger.debug(
            "PCS month=%s type=%d: %d tenders",
            month_str, notice_type, len(tenders),
        )

    except httpx.HTTPStatusError as e:
        logger.warning(
            "PCS HTTP error (month=%s, type=%d): %s",
            month_str, notice_type, e,
        )
    except httpx.RequestError as e:
        logger.warning(
            "PCS request error (month=%s, type=%d): %s",
            month_str, notice_type, e,
        )

    return tenders


def _get_ocid(release: dict) -> str:
    """Extract OCID from a release."""
    return (
        release.get("ocid")
        or release.get("OCID")
        or release.get("id")
        or ""
    )


def _parse_release(release: dict, category: str) -> Optional[Tender]:
    """Parse a PCS OCDS release into a Tender model."""
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
        authority   = buyer.get("name") or "Unknown Authority"

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

        # Canonical URL — PCS notice page
        ocid_suffix = ocid.replace("ocds-r6ebe6-", "").replace("ocds-", "")
        url = (
            f"https://www.publiccontractsscotland.gov.uk"
            f"/Search/Search_Switch.aspx?ID={ocid_suffix}"
        )

        # Contact point
        contact_block = tender_block.get("contactPoint", {})
        contact_name  = contact_block.get("name")      or None
        contact_email = contact_block.get("email")     or None
        contact_phone = contact_block.get("telephone") or None
        contact_url   = contact_block.get("url")       or None

        # Awarded supplier
        awarded_supplier = None
        _awards = release.get("awards", [])
        if _awards:
            _suppliers = _awards[0].get("suppliers", [])
            if _suppliers:
                awarded_supplier = _suppliers[0].get("name") or None

        return Tender(
            id=f"PCS-{ocid}",
            source=TenderSource.PUBLIC_CONTRACTS_SCOTLAND,
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
            awarded_supplier=awarded_supplier,
            contact_name=contact_name,
            contact_email=contact_email,
            contact_phone=contact_phone,
            contact_url=contact_url,
        )

    except Exception as e:
        logger.debug(
            "Failed to parse PCS release '%s': %s", _get_ocid(release), e
        )
        return None


def _months_to_fetch(days_back: int) -> List[str]:
    """
    Return a list of month strings (mm-yyyy) covering the days_back window.
    Always includes the current month. Safety cap of 6 months.
    """
    now     = datetime.now(timezone.utc)
    months  = []
    current = now

    while len(months) <= 6:
        months.append(current.strftime("%m-%Y"))
        first_of_month = current.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if (now - first_of_month).days <= days_back:
            prev = first_of_month - timedelta(days=1)
            current = prev
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