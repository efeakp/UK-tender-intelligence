"""
Contracting Authority Watchlist.

Flags any tender from a watched authority regardless of relevance score.
Watched authorities are defined in WATCHED_AUTHORITIES below.

When a tender matches a watched authority:
  - watchlist_match is set to True on the tender
  - watchlist_authority is set to the matched authority name
  - Score is boosted by WATCHLIST_SCORE_BOOST (default +3, capped at 10)

Watched authority matching is fuzzy — partial name matches are supported.
"""

from typing import Optional
import re

# ── Watched authorities ────────────────────────────────────────────────────────
# Format: { "match_pattern": "display_name" }
# Patterns are case-insensitive partial matches against tender.authority
# Add new authorities by appending to this dict

WATCHED_AUTHORITIES: dict[str, str] = {
    # Current clients & active relationships
    "northumberland county council": "Northumberland County Council",
    "york and north yorkshire":     "York & North Yorkshire CA",
    "north yorkshire":              "North Yorkshire Council",
    "city of york":                 "City of York Council",
    "enfield":                      "Enfield Council",
    "energetik":                    "Energetik",

    # Target combined authorities
    "greater manchester":           "Greater Manchester CA",
    "west yorkshire":               "West Yorkshire CA",
    "south yorkshire":              "South Yorkshire CA",
    "sheffield city region":        "Sheffield City Region",
    "liverpool city region":        "Liverpool City Region",
    "west midlands":                "West Midlands CA",
    "bristol city":                 "Bristol City Council",
    "city leap":                    "City Leap / Bristol",

    # Heat network & energy funders
    "desnz":                        "DESNZ",
    "department for energy":        "DESNZ",
    "great british energy":         "GB Energy",
    "gb energy":                    "GB Energy",
    "hndu":                         "HNDU",

    # Active bid pipeline authorities
    "salford":                      "Salford City Council",
    "surrey":                       "Surrey County Council",
    "harrogate":                    "Harrogate Town Council",

    # Housing associations with heat network programmes
    "orbit":                        "Orbit Group",
    "wheatley":                     "Wheatley Group",
    "notting hill genesis":         "Notting Hill Genesis",
    "peabody":                      "Peabody Trust",

    # STAR Procurement (HNCF framework owner)
    "star procurement":             "STAR Procurement",
    "trafford":                     "Trafford Council",
    "stockport":                    "Stockport Council",
}

WATCHLIST_SCORE_BOOST = 3


def check_watchlist(authority: str) -> tuple[bool, Optional[str]]:
    """
    Check if a tender authority matches any watched authority.
    Returns (is_match, display_name).
    """
    authority_lower = authority.lower()
    for pattern, display_name in WATCHED_AUTHORITIES.items():
        if pattern.lower() in authority_lower:
            return True, display_name
    return False, None


def apply_watchlist(tender) -> None:
    """
    Apply watchlist check to a tender in-place.
    Sets watchlist_match and watchlist_authority as proper Pydantic fields
    so they are serialised in the API response and visible on the dashboard.
    Boosts score if matched.
    """
    is_match, display_name = check_watchlist(tender.authority)
    tender.watchlist_match     = is_match
    tender.watchlist_authority = display_name or ""

    if is_match:
        # Boost score so watchlist tenders always appear near the top
        boosted = min(tender.score + WATCHLIST_SCORE_BOOST, 10)
        if boosted > tender.score:
            tender.score = boosted