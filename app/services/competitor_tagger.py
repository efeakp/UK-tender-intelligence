"""
Competitor watch service.

Flags Awarded Contract notices where a known competitor was the winning supplier.
Matching uses whole-word regex so "regen" does not match "regeneration".
"""

import re
from typing import Optional

COMPETITORS: dict[str, str] = {
    "advanced infrastructure": "Advanced Infrastructure",
    "city science":            "City Science",
    "grid edge":               "Grid Edge",
    "tibo energy":             "Tibo Energy",
    "tibo":                    "Tibo Energy",
    "centre for sustainable energy": "Centre for Sustainable Energy",
    "element energy":          "Element Energy",
    "regen sw":                "Regen",
    "regen":                   "Regen",
    "living places":           "Living Places",
    "vital energi limited":    "Vital Energi",
    "vital energi":            "Vital Energi",
}

# Pre-compiled whole-word patterns (avoids "regen" matching "regeneration")
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b" + re.escape(p) + r"\b", re.IGNORECASE), name)
    for p, name in COMPETITORS.items()
]


def apply_competitor_flag(tender) -> None:
    """
    Check if an awarded contract was won by a known competitor.
    Sets competitor_win and competitor_name on the tender in-place.
    Only applies to Awarded Contract notices with a populated awarded_supplier.
    """
    tender.competitor_win  = False
    tender.competitor_name = None

    if tender.category != "Awarded Contract":
        return
    if not tender.awarded_supplier:
        return

    for pattern, display_name in _PATTERNS:
        if pattern.search(tender.awarded_supplier):
            tender.competitor_win  = True
            tender.competitor_name = display_name
            return
