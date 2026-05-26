"""
In-Tend portal scraper (in-tendhost.co.uk).

In-Tend renders tender lists client-side via JavaScript and includes CAPTCHA
protection, so plain httpx cannot fetch results. This scraper uses Playwright.

Setup (one-time):
    pip install playwright
    playwright install chromium

If Playwright is not installed, fetch_tenders returns an empty list with a
warning logged — the other sources in the aggregator are unaffected.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.models.tender import Tender, TenderSource

logger = logging.getLogger(__name__)

INTEND_BASE = "https://www.in-tendhost.co.uk"

# Council slugs known to use In-Tend — add more as needed
_CLIENT_SLUGS = [
    "birminghamcc",
    "worcestershire",
    "hampshire",
    "eastsuffolk",
    "wnc",
    "os",
    "csw-jets",
]

_KEYWORDS = [
    "energy",
    "solar",
    "renewable",
    "decarbonisation",
    "feasibility",
    "heat pump",
    "carbon",
    "net zero",
]


def _parse_date(s: str) -> Optional[datetime]:
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


async def _scrape_slug(slug: str, keyword: str) -> list[dict]:
    """Scrape one In-Tend council's current tenders for a keyword using Playwright."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    url = f"{INTEND_BASE}/{slug}/aspx/Tenders/Current"
    rows = []

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_extra_http_headers({
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            })
            try:
                await page.goto(url, wait_until="networkidle", timeout=30_000)
                # Wait for tender rows to appear
                await page.wait_for_selector("table tr", timeout=15_000)
                html = await page.content()
            except Exception as exc:
                logger.debug("In-Tend playwright load failed (%s): %s", slug, exc)
                await browser.close()
                return []
            await browser.close()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "lxml")
        for tr in soup.select("table tbody tr"):
            cells = tr.find_all("td")
            if len(cells) < 2:
                continue
            title_cell = cells[0]
            link = title_cell.find("a")
            title = link.get_text(strip=True) if link else title_cell.get_text(strip=True)
            if not title:
                continue
            # Keyword filter (client-side, since portal doesn't filter well via URL)
            if keyword.lower() not in title.lower():
                continue
            href = link.get("href", "") if link else ""
            full_url = href if href.startswith("http") else f"{INTEND_BASE}{href}"
            deadline_str = cells[1].get_text(strip=True) if len(cells) > 1 else ""
            rows.append({
                "slug":         slug,
                "title":        title,
                "url":          full_url,
                "deadline_str": deadline_str,
            })

    except Exception as exc:
        logger.warning("In-Tend error (slug=%s, keyword=%r): %s", slug, keyword, exc)

    return rows


async def fetch_tenders(
    _client: httpx.AsyncClient,
    days_back: int = 30,
) -> list[Tender]:
    try:
        import playwright  # noqa: F401
    except ImportError:
        logger.warning(
            "In-Tend scraper disabled: Playwright not installed. "
            "Run: pip install playwright && playwright install chromium"
        )
        return []

    seen: set[str]   = set()
    raw:  list[dict] = []

    tasks = [
        _scrape_slug(slug, keyword)
        for slug in _CLIENT_SLUGS
        for keyword in _KEYWORDS
    ]
    # Run concurrently in small batches to avoid overwhelming the portal
    batch_size = 4
    for i in range(0, len(tasks), batch_size):
        batch_results = await asyncio.gather(*tasks[i:i + batch_size], return_exceptions=True)
        for result in batch_results:
            if isinstance(result, Exception):
                continue
            for row in result:
                uid = f"{row['slug']}-{row['title'][:60]}"
                if uid not in seen:
                    seen.add(uid)
                    raw.append(row)
        await asyncio.sleep(1.0)

    logger.info("In-Tend: %d unique tenders fetched", len(raw))

    tenders = []
    for i, row in enumerate(raw):
        try:
            tenders.append(Tender(
                id             = f"intend-{abs(hash(row['url']))}",
                source         = TenderSource.INTEND,
                title          = row["title"],
                authority      = row["slug"].replace("-", " ").title(),
                description    = "",
                published      = None,
                deadline       = _parse_date(row["deadline_str"]),
                value          = "Value not stated",
                value_amount   = None,
                value_currency = "GBP",
                url            = row["url"],
                category       = "Opportunity",
                cpv_codes      = [],
                nuts_codes     = [],
            ))
        except Exception as exc:
            logger.debug("In-Tend map error: %s", exc)

    return tenders
