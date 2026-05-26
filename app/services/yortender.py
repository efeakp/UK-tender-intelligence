"""
Yortender portal scraper (yortender.eu-supply.com).

Scrapes the public tender list. The old yortender.co.uk domain is defunct;
the live portal is hosted on EU-Supply at yortender.eu-supply.com.
No login required.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

YORTENDER_BASE = "https://yortender.eu-supply.com"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.5",
}

_KEYWORDS = [
    "energy",
    "solar",
    "renewable",
    "decarbonisation",
    "net zero",
    "heat pump",
    "carbon",
    "biomass",
    "wind",
    "feasibility",
]


def _parse_date(s: str) -> Optional[datetime]:
    """Parse DD/MM/YYYY or DD/MM/YYYY HH:MM portal dates."""
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def _cell_text(td) -> str:
    """Extract text from a Yortender cell (text lives in a div.js-tooltip-content)."""
    div = td.find("div", class_="js-tooltip-content")
    if div:
        return div.get_text(strip=True)
    return td.get_text(strip=True)


def _parse_rows(html: str) -> list[dict]:
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    # Table class: "table table-hover ctm-table-fixed-layout sorter custom-tooltip"
    table = soup.find("table", class_="ctm-table-fixed-layout")
    if not table:
        return []
    tbody = table.find("tbody")
    if not tbody:
        return []

    rows = []
    for tr in tbody.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 7:
            continue

        # Col 0: Tender ID  Col 2: Name (with link)  Col 3: Published  Col 4: Deadline  Col 6: Buyer
        tender_id = _cell_text(cells[0])
        link = cells[2].find("a")
        if not link:
            continue
        title = link.get_text(strip=True)
        href  = link.get("href", "")
        # Extract PID for the unique key
        pid = ""
        for part in href.split("&"):
            if part.startswith("PID="):
                pid = part[4:]
                break

        pub_str      = _cell_text(cells[3])
        deadline_str = _cell_text(cells[4])
        buyer        = _cell_text(cells[6])

        rows.append({
            "tender_id":    tender_id or pid,
            "pid":          pid,
            "title":        title,
            "buyer":        buyer,
            "published_str": pub_str,
            "deadline_str":  deadline_str,
            "href":          href,
        })
    return rows


async def _fetch_page(client: httpx.AsyncClient, keyword: str, page: int) -> str:
    try:
        r = await client.get(
            f"{YORTENDER_BASE}/ctm/supplier/publictenders",
            params={"B": "YORTENDER", "Name": keyword, "page": page},
        )
        r.raise_for_status()
        return r.text
    except Exception as exc:
        logger.warning("Yortender error (keyword=%r page=%d): %s", keyword, page, exc)
        return ""


async def fetch_tenders(
    _client: httpx.AsyncClient,
    days_back: int = 30,
) -> list[Tender]:
    seen: set[str]   = set()
    raw:  list[dict] = []

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
                    uid = row["pid"] or row["tender_id"]
                    if uid and uid not in seen:
                        seen.add(uid)
                        raw.append(row)
                        added += 1
                logger.debug(
                    "Yortender keyword=%r page=%d: %d rows, %d added",
                    keyword, page, len(rows), added,
                )
                if len(rows) < 20:
                    break
                await asyncio.sleep(0.5)
            await asyncio.sleep(0.3)

    logger.info("Yortender: %d unique tenders fetched", len(raw))

    tenders = []
    for row in raw:
        try:
            uid      = row["pid"] or row["tender_id"]
            href     = row["href"]
            full_url = href if href.startswith("http") else f"{YORTENDER_BASE}{href}"
            tenders.append(Tender(
                id             = f"yortender-{uid}",
                source         = TenderSource.YORTENDER,
                title          = row["title"] or "Untitled",
                authority      = row["buyer"] or "—",
                description    = "",
                published      = _parse_date(row["published_str"]),
                deadline       = _parse_date(row["deadline_str"]),
                value          = "Value not stated",
                value_amount   = None,
                value_currency = "GBP",
                url            = full_url,
                category       = "Opportunity",
                cpv_codes      = [],
                nuts_codes     = [],
            ))
        except Exception as exc:
            logger.debug("Yortender map error: %s", exc)

    return tenders
