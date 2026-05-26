"""
UKRI / Innovate UK — Gateway to Research (GtR v2) API client.

Fetches Innovate UK-funded energy projects, maps them to the internal
Tender schema, scores them with the existing relevance scorer, and
caches the results in memory.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.models.tender import Tender, TenderSource
from app.services.scorer import score_tender

logger = logging.getLogger(__name__)

GtR_BASE = "https://gtr.ukri.org/gtr/api"
HEADERS = {
    "Accept": "application/vnd.rcuk.gtr.json-v7",
    "User-Agent": "NordicEnergyTenderIntelligence/2.0",
}

# Energy search terms sent to GtR.  Each query fetches up to 100 results;
# client-side deduplication and funder filtering are applied afterwards.
SEARCH_TERMS = [
    "renewable energy",
    "heat network",
    "energy efficiency",
    "net zero",
    "decarbonisation",
    "energy storage",
    "low carbon buildings",
    "hydrogen energy",
    "smart grid",
    "energy system optimisation",
]

_store: dict = {"projects": [], "fetched_at": None, "total": 0}


def get_store() -> dict:
    return _store


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_date(val) -> Optional[datetime]:
    """Handle GtR epoch-milliseconds ints and ISO-string dates."""
    if val is None:
        return None
    try:
        if isinstance(val, (int, float)):
            return datetime.fromtimestamp(val / 1000, tz=timezone.utc)
        return datetime.fromisoformat(str(val).rstrip("Z")).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _format_value(pounds) -> str:
    if not pounds:
        return "Value not stated"
    pounds = float(pounds)
    if pounds >= 1_000_000:
        return f"£{pounds / 1_000_000:.2f}m"
    return f"£{int(pounds):,}"


def _lead_org(project: dict) -> str:
    for key in ("leadOrganisations", "leadOrganisation"):
        val = project.get(key)
        if isinstance(val, list) and val:
            o = val[0]
            return o.get("name", "—") if isinstance(o, dict) else str(o)
        if isinstance(val, dict):
            return val.get("name", "—")
    return "—"


def _funder_name(project: dict) -> str:
    fund   = project.get("fund") or {}
    funder = fund.get("funder") or {}
    return funder.get("name", "") if isinstance(funder, dict) else ""


def _is_innovate_uk(project: dict) -> bool:
    name = _funder_name(project)
    return "Innovate UK" in name or "UKRI" in name


def _map_to_tender(project: dict) -> Tender:
    fund = project.get("fund") or {}
    pid  = project.get("id", "")
    return Tender(
        id            = f"ukri-{pid}",
        source        = TenderSource.INNOVATE_UK,
        title         = project.get("title") or "Untitled",
        authority     = _lead_org(project),
        description   = project.get("abstractText") or project.get("potentialImpact") or "",
        published     = _parse_date(fund.get("start")),
        deadline      = _parse_date(fund.get("end")),
        value         = _format_value(fund.get("valuePounds")),
        value_amount  = fund.get("valuePounds"),
        url           = f"https://gtr.ukri.org/projects?ref={pid}",
        category      = "Opportunity" if project.get("status") == "Active" else "Awarded Contract",
        cpv_codes     = [],
        nuts_codes    = [],
    )


# ── Fetcher ───────────────────────────────────────────────────────────────────

async def _fetch_page(client: httpx.AsyncClient, term: str, page: int) -> list[dict]:
    try:
        resp = await client.get(
            f"{GtR_BASE}/projects",
            params={"p": page, "s": 100, "q": term, "f": "pro.am"},
        )
        resp.raise_for_status()
        return resp.json().get("project", [])
    except Exception as exc:
        logger.warning("GtR API error (term=%r page=%d): %s", term, page, exc)
        return []


async def fetch_ukri_projects() -> dict:
    """
    Query the GtR API for Innovate UK energy projects, score them, and cache.
    Returns the updated store dict.
    """
    seen: set[str] = set()
    raw:  list[dict] = []

    async with httpx.AsyncClient(
        headers=HEADERS,
        timeout=httpx.Timeout(30.0),
        follow_redirects=True,
    ) as client:
        for term in SEARCH_TERMS:
            projects = await _fetch_page(client, term, 1)
            for p in projects:
                pid = p.get("id")
                if not pid or pid in seen:
                    continue
                if not _is_innovate_uk(p):
                    continue
                seen.add(pid)
                raw.append(p)
            await asyncio.sleep(0.3)   # polite pacing

    logger.info("UKRI: %d unique Innovate UK projects retrieved", len(raw))

    scored: list[dict] = []
    for p in raw:
        try:
            tender = _map_to_tender(p)
            tender = score_tender(tender)
            d = tender.model_dump(mode="json")
            # Attach extra display fields not in the core Tender model
            d["grant_category"] = p.get("grantCategory", "")
            d["funder"]         = _funder_name(p) or "Innovate UK"
            d["ukri_status"]    = p.get("status", "Unknown")
            scored.append(d)
        except Exception as exc:
            logger.debug("Failed to map UKRI project %s: %s", p.get("id"), exc)

    # Active projects first, then by relevance score descending
    scored.sort(key=lambda t: (t.get("ukri_status") != "Active", -(t.get("score") or 0)))

    _store["projects"]   = scored
    _store["fetched_at"] = datetime.now(timezone.utc).isoformat()
    _store["total"]      = len(scored)
    return _store
