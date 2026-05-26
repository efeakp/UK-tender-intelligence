"""
TED (Tenders Electronic Daily) — EU procurement API client.

Queries the TED v3 Search API for energy-related contract notices,
maps them to the internal Tender schema, scores them, and caches results.

No API key required for public notice search.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.models.tender import Tender, TenderSource
from app.services.scorer import score_tender

logger = logging.getLogger(__name__)

TED_BASE = "https://api.ted.europa.eu/v3"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "NordicEnergyTenderIntelligence/2.0",
}

# CPV codes for energy consultancy and renewable energy
ENERGY_CPVS = [
    "71314000",  # Energy and related services
    "71314100",  # Electrical services
    "71314200",  # Energy efficiency consultancy services
    "71314300",  # Energy efficiency services
    "09330000",  # Solar energy
    "09331000",  # Solar panels
    "09332000",  # Solar installation
    "09310000",  # Electricity
]

# TED fields to request from the API
FIELDS = [
    "publication-number",
    "notice-type",
    "notice-title",
    "publication-date",
    "deadline-date-lot",
    "deadline-date-part",
    "buyer-name",
    "buyer-country",
    "estimated-value-proc",
    "estimated-value-cur-proc",
    "estimated-value-lot",
    "estimated-value-cur-lot",
    "classification-cpv",
    "description-lot",
    "description-part",
    "links",
]

# Notice type → category mapping
_NOTICE_CATEGORY = {
    "pin-buyer":           "Future Opportunity",
    "pin-cfc-standard":    "Future Opportunity",
    "pin-cfc-social":      "Future Opportunity",
    "cn-standard":         "Opportunity",
    "cn-social":           "Opportunity",
    "cn-desg":             "Opportunity",
    "can-standard":        "Awarded Contract",
    "can-social":          "Awarded Contract",
    "can-desg":            "Awarded Contract",
    "can-modifications":   "Awarded Contract",
}

# ISO 3-letter → display name (common EU procurement countries)
_COUNTRY_NAMES = {
    "AUT": "Austria",  "BEL": "Belgium",  "BGR": "Bulgaria",  "CYP": "Cyprus",
    "CZE": "Czechia",  "DEU": "Germany",  "DNK": "Denmark",   "ESP": "Spain",
    "EST": "Estonia",  "FIN": "Finland",  "FRA": "France",    "GBR": "United Kingdom",
    "GRC": "Greece",   "HRV": "Croatia",  "HUN": "Hungary",   "IRL": "Ireland",
    "ITA": "Italy",    "LTU": "Lithuania","LUX": "Luxembourg","LVA": "Latvia",
    "MLT": "Malta",    "NLD": "Netherlands","NOR": "Norway",   "POL": "Poland",
    "PRT": "Portugal", "ROU": "Romania",  "SVK": "Slovakia",  "SVN": "Slovenia",
    "SWE": "Sweden",   "NOR": "Norway",   "ISL": "Iceland",
}

_store: dict = {"notices": [], "fetched_at": None, "total": 0}


def get_store() -> dict:
    return _store


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_text(val) -> str:
    """Extract English text from a TED multilingual field (dict or str or list)."""
    if not val:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, list):
        return _get_text(val[0]) if val else ""
    if isinstance(val, dict):
        for key in ("eng", "ENG"):
            if key in val:
                v = val[key]
                return (v[0] if isinstance(v, list) and v else str(v))
        # Fall back to first non-empty value
        for v in val.values():
            text = _get_text(v)
            if text:
                return text
    return ""


def _get_buyer(notice: dict) -> str:
    buyer = notice.get("buyer-name") or {}
    return _get_text(buyer) or "—"


def _get_country(notice: dict) -> str:
    codes = notice.get("buyer-country") or []
    if codes:
        code = codes[0] if isinstance(codes, list) else str(codes)
        return _COUNTRY_NAMES.get(code, code)
    return ""


def _get_url(notice: dict) -> str:
    pub_num = notice.get("publication-number", "")
    links   = notice.get("links") or {}
    html    = links.get("htmlDirect") or {}
    return (
        html.get("ENG")
        or html.get("FRA")
        or (next(iter(html.values()), "") if html else "")
        or (f"https://ted.europa.eu/en/notice/{pub_num}/html" if pub_num else "")
    )


def _get_deadline(notice: dict) -> Optional[datetime]:
    for key in ("deadline-date-lot", "deadline-date-part"):
        val = notice.get(key)
        if val:
            text = _get_text(val) if isinstance(val, (dict, list)) else str(val)
            try:
                return datetime.fromisoformat(text.split("+")[0]).replace(tzinfo=timezone.utc)
            except Exception:
                pass
    return None


def _get_value(notice: dict) -> tuple[Optional[float], str]:
    for val_key, cur_key in [
        ("estimated-value-proc", "estimated-value-cur-proc"),
        ("estimated-value-lot",  "estimated-value-cur-lot"),
    ]:
        raw = notice.get(val_key)
        if raw is not None:
            try:
                amount = float(raw)
                if amount > 0:
                    cur = str(notice.get(cur_key) or "EUR")
                    return amount, cur
            except (TypeError, ValueError):
                pass
    return None, "EUR"


def _format_value(amount: Optional[float], currency: str) -> str:
    if not amount:
        return "Value not stated"
    if amount >= 1_000_000:
        return f"{currency} {amount / 1_000_000:.2f}m"
    return f"{currency} {int(amount):,}"


def _get_description(notice: dict) -> str:
    for key in ("description-lot", "description-part"):
        val = notice.get(key)
        if val:
            text = _get_text(val)
            if text:
                return text
    return ""


def _map_to_tender(notice: dict) -> Tender:
    pub_num      = notice.get("publication-number", "")
    notice_type  = notice.get("notice-type", "")
    amount, cur  = _get_value(notice)
    country      = _get_country(notice)
    authority    = _get_buyer(notice)
    if country:
        authority = f"{authority} ({country})"

    pub_raw = notice.get("publication-date", "")
    published = None
    if pub_raw:
        try:
            published = datetime.fromisoformat(pub_raw.split("+")[0]).replace(tzinfo=timezone.utc)
        except Exception:
            pass

    return Tender(
        id           = f"ted-{pub_num}",
        source       = TenderSource.TED,
        title        = _get_text(notice.get("notice-title")) or "Untitled",
        authority    = authority or "—",
        description  = _get_description(notice),
        published    = published,
        deadline     = _get_deadline(notice),
        value        = _format_value(amount, cur),
        value_amount = amount,
        value_currency = cur,
        url          = _get_url(notice),
        category     = _NOTICE_CATEGORY.get(notice_type, "Unknown"),
        cpv_codes    = notice.get("classification-cpv") or [],
        nuts_codes   = [],
    )


# Notice types to keep (all others discarded client-side)
_KEEP_TYPES = frozenset({
    "cn-standard", "cn-social", "cn-desg",       # contract notices (active tenders)
    "pin-buyer", "pin-cfc-standard",              # prior information notices
    "can-standard", "can-social", "can-desg",     # contract award notices
})

# ── Fetcher ───────────────────────────────────────────────────────────────────

async def _search_page(
    client:     httpx.AsyncClient,
    cpv:        str,
    page:       int,
    page_size:  int = 100,
) -> tuple[list[dict], int]:
    """
    Fetch one page for a single CPV code.
    Uses a simple query with no notice-type or date filters to avoid 400s;
    filtering is done client-side after retrieval.
    """
    body = {
        "query":          f"classification-cpv IN ({cpv})",
        "fields":         FIELDS,
        "limit":          page_size,
        "scope":          "ALL",
        "paginationMode": "PAGE_NUMBER",
        "page":           page,
    }
    try:
        resp = await client.post(f"{TED_BASE}/notices/search", json=body)
        resp.raise_for_status()
        data = resp.json()
        notices = data.get("notices", [])
        total   = data.get("totalNoticeCount", 0)
        logger.debug("TED cpv=%s page=%d: %d notices (total=%d)", cpv, page, len(notices), total)
        return notices, total
    except Exception as exc:
        logger.warning("TED API error (cpv=%s page=%d): %s", cpv, page, exc)
        return [], 0


async def fetch_ted_notices() -> dict:
    """
    Query the TED API for energy-related EU notices, score them, and cache.
    Queries each CPV code separately (one query per code) to avoid complex
    query syntax that can trigger 400 errors. Client-side filtering retains
    only relevant notice types and notices from the last 12 months.
    Returns the updated store dict.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=730)  # 2 years

    seen: set[str] = set()
    raw:  list[dict] = []

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        for cpv in ENERGY_CPVS:
            # Fetch up to 4 pages (400 notices) per CPV code
            for page in range(1, 5):
                notices, total = await _search_page(client, cpv, page)
                if not notices:
                    break
                for n in notices:
                    pub_num = n.get("publication-number")
                    if not pub_num or pub_num in seen:
                        continue
                    # Client-side: filter to relevant notice types only
                    notice_type = n.get("notice-type", "")
                    if notice_type not in _KEEP_TYPES:
                        continue
                    # Client-side: filter to last 12 months
                    pub_raw = n.get("publication-date", "")
                    if pub_raw:
                        try:
                            pub_dt = datetime.fromisoformat(
                                pub_raw.split("+")[0]
                            ).replace(tzinfo=timezone.utc)
                            if pub_dt < cutoff:
                                continue
                        except Exception:
                            pass
                    seen.add(pub_num)
                    raw.append(n)
                if not notices or len(notices) < 100:
                    break
                await asyncio.sleep(0.4)
            await asyncio.sleep(0.4)

    logger.info(
        "TED: %d unique notices after type+date filter (seen=%d deduped keys)",
        len(raw), len(seen),
    )

    scored: list[dict] = []
    for n in raw:
        try:
            tender = _map_to_tender(n)
            tender = score_tender(tender)
            d = tender.model_dump(mode="json")
            d["ted_notice_type"] = n.get("notice-type", "")
            d["ted_country"]     = _get_country(n)
            scored.append(d)
        except Exception as exc:
            logger.debug("Failed to map TED notice %s: %s", n.get("publication-number"), exc)

    # Opportunity first, then by score
    _cat_order = {"Opportunity": 0, "Future Opportunity": 1, "Awarded Contract": 2, "Unknown": 3}
    scored.sort(key=lambda t: (_cat_order.get(t.get("category", "Unknown"), 3), -(t.get("score") or 0)))

    _store["notices"]    = scored
    _store["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _store["total"]      = len(scored)
    return _store
