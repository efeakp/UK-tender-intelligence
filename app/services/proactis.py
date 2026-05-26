"""
Proactis portal scraper (procontract.due-north.com).

Scrapes the public opportunities list using energy-related keywords.
No login required. Uses browser-like headers to avoid 403s.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin

import httpx

from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

PROACTIS_BASE = "https://procontract.due-north.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.5",
}

# Energy-specific keywords — broad enough to catch edge cases, narrow enough to
# avoid drowning in irrelevant results (scorer handles final relevance filtering)
_KEYWORDS = [
    "energy consultancy",
    "energy feasibility",
    "energy optimisation",
    "energy audit",
    "solar energy",
    "solar pv",
    "renewable energy",
    "decarbonisation",
    "net zero",
    "heat pump",
    "carbon reduction",
    "biomass",
    "wind energy",
    "energy management",
    "energy strategy",
]


def _parse_date(s: str) -> Optional[datetime]:
    """Parse DD/MM/YYYY portal dates."""
    try:
        return datetime.strptime(s.strip(), "%d/%m/%Y").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_value(s: str) -> tuple[Optional[float], str]:
    """Parse £X,XXX.XX value strings."""
    s = s.strip().replace(",", "")
    if s.startswith("£"):
        try:
            return float(s[1:]), "GBP"
        except Exception:
            pass
    return None, "GBP"


def _format_value(amount: Optional[float], currency: str) -> str:
    if not amount:
        return "Value not stated"
    if amount >= 1_000_000:
        return f"{currency} {amount / 1_000_000:.2f}m"
    return f"{currency} {int(amount):,}"


def _parse_rows(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", {"id": "opportunitiesGrid"})
    if not table:
        return []
    tbody = table.find("tbody")
    if not tbody:
        return []

    rows = []
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        link = cells[0].find("a")
        if not link:
            continue

        href = link.get("href", "")
        advert_id = href.split("advertId=")[-1] if "advertId=" in href else ""
        if not advert_id:
            continue

        rows.append({
            "advert_id":  advert_id,
            "reference":  link.get("title", ""),
            "title":      link.get_text(strip=True),
            "buyer":      cells[1].get_text(strip=True),
            "start":      cells[2].get_text(strip=True),
            "end":        cells[3].get_text(strip=True),
            "value_str":  cells[4].get_text(strip=True),
        })
    return rows


async def _fetch_page(client: httpx.AsyncClient, keyword: str, page: int) -> str:
    try:
        r = await client.get(
            f"{PROACTIS_BASE}/Opportunities/Index",
            params={
                "Page":     page,
                "PageSize": 50,
                "TabName":  "opportunities",
                "Keywords": keyword,
            },
        )
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.warning("Proactis error (keyword=%r page=%d): %s", keyword, page, exc)
        return ""


async def fetch_tenders(
    _client: httpx.AsyncClient,  # not used — Proactis needs its own browser headers
    days_back: int = 30,
) -> list[Tender]:
    seen:    set[str]  = set()
    raw:     list[dict] = []

    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
    ) as client:
        for keyword in _KEYWORDS:
            for page in range(1, 4):
                html = await _fetch_page(client, keyword, page)
                if not html:
                    break
                rows = _parse_rows(html)
                if not rows:
                    break
                added = 0
                for row in rows:
                    if row["advert_id"] not in seen:
                        seen.add(row["advert_id"])
                        raw.append(row)
                        added += 1
                logger.debug(
                    "Proactis keyword=%r page=%d: %d rows, %d added",
                    keyword, page, len(rows), added,
                )
                if len(rows) < 50:
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(0.3)

    logger.info("Proactis: %d unique opportunities fetched", len(raw))

    tenders = []
    for row in raw:
        try:
            amount, currency = _parse_value(row["value_str"])
            published = _parse_date(row["start"])
            deadline  = _parse_date(row["end"])
            tenders.append(Tender(
                id             = f"proactis-{row['advert_id']}",
                source         = TenderSource.PROACTIS,
                title          = row["title"] or "Untitled",
                authority      = row["buyer"] or "—",
                description    = "",
                published      = published,
                deadline       = deadline,
                value          = _format_value(amount, currency),
                value_amount   = amount,
                value_currency = currency,
                url            = f"{PROACTIS_BASE}/Advert?advertId={row['advert_id']}",
                category       = "Opportunity",
                cpv_codes      = [],
                nuts_codes     = [],
            ))
        except Exception as exc:
            logger.debug("Proactis map error (%s): %s", row.get("advert_id"), exc)

    return tenders
