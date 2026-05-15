"""
Framework & procurement route tagger for Nordic Energy tenders.

Detects procurement route from tender text and tags each tender with:
  - procurement_route: Open Market | Further Competition | DPS | Restricted | Unknown
  - framework_name: the specific framework name if detected
  - nordic_eligible: True if Nordic Energy is registered on the detected framework

Nordic Energy's registered frameworks (from Procurement Frameworks and Portals Overview):
  HNCF       — Heat Network Consultancy Services Framework (STAR Procurement, DN800427)
               PME participated Dec 2025 — launches 1 April 2026, 8 years, 2 lots
               Lot 1: Non-Technical (feasibility, zoning, tariff, compliance)
               Lot 2: Technical (design, construction, O&M, decarbonisation)
  ENZPS      — Energy and Net Zero Professional Services (DESNZ, Prj_2623)
  HNDU       — Heat Network Development Unit (DESNZ)
  RM6313     — Demand Management and Renewables DPS (CCS)
  CPCA DPS   — Combined Procurement for Construction and Assets DPS
  Yortender  — Yorkshire & Humber procurement portal
  Bloom      — Bloom Procurement Services
  ProContract / Bravo — Wales & West procurement portal
  BlueLight  — Emergency services procurement
  ESPO       — Eastern Shires Purchasing Organisation
  NEUPC      — North Eastern Universities Purchasing Consortium
  TPPL       — Total Procurement Partnership Ltd
"""

import re
from typing import Optional, Tuple
from dataclasses import dataclass


@dataclass
class FrameworkTag:
    procurement_route: str   # Open Market | Further Competition | DPS | Restricted | Unknown
    framework_name:    str   # e.g. "ENZPS", "RM6313", "Unknown"
    nordic_eligible:   bool  # True if NE is on this framework
    confidence:        str   # High | Medium | Low


# ── Procurement route detection ───────────────────────────────────────────────

# Patterns that indicate a Further Competition off a framework
FURTHER_COMPETITION_PATTERNS = [
    r"further competition",
    r"call.?off",
    r"mini.?competition",
    r"framework agreement",
    r"framework contract",
    r"awarded under.*framework",
    r"lot \d+",
    r"framework ref",
    r"framework no",
    r"appointed.*framework",
]

# Patterns that indicate a Dynamic Purchasing System
DPS_PATTERNS = [
    r"dynamic purchasing system",
    r"\bDPS\b",
    r"dynamic marketplace",
    r"invitation to participate",
    r"\bITP\b",
]

# Patterns that indicate restricted/pre-qualified
RESTRICTED_PATTERNS = [
    r"restricted procedure",
    r"pre.?qualif",
    r"\bPQQ\b",
    r"selection questionnaire",
    r"\bSQ\b",
    r"invitation to tender.*selected",
    r"shortlist",
]

# Patterns that indicate open market
OPEN_MARKET_PATTERNS = [
    r"open procedure",
    r"open tender",
    r"open to all",
    r"anyone can",
    r"no framework",
]

# ── Framework name detection ──────────────────────────────────────────────────
# Maps regex patterns to (framework_name, nordic_eligible)

FRAMEWORK_PATTERNS: list[Tuple[str, str, bool]] = [
    # Nordic Energy registered frameworks — nordic_eligible = True
    # HNCF — Heat Network Consultancy Services Framework
    # STAR Procurement / Greater Manchester & Liverpool LAs
    # PME participated Dec 2025 — framework launches 1 April 2026, 8 years
    # Lot 1: Non-Technical (feasibility, zoning, tariff, compliance)
    # Lot 2: Technical (design, construction, O&M, decarbonisation)
    (r"\bHNCF\b|Heat Network Consultancy.*Framework|HNCS.*Framework|HNCS.*FW",
     "HNCF — Heat Network Consultancy Services Framework (STAR)",  True),
    (r"\bSTAR\s+Procurement\b|STAR.*heat network|heat network.*STAR",
     "HNCF — Heat Network Consultancy Services Framework (STAR)",  True),
    (r"DN800427",
     "HNCF — Heat Network Consultancy Services Framework (STAR)",  True),
    (r"\bENZPS\b|Energy and Net Zero Professional Services",         "ENZPS (DESNZ)",        True),
    (r"\bHNDU\b|Heat Network Development Unit",                      "HNDU (DESNZ)",         True),
    (r"\bRM6313\b|Demand Management.*Renewables.*DPS|Demand Management and Renewables Dynamic Purchasing", "RM6313 — Demand Management & Renewables DPS (CCS)", True),
    (r"Demand Management.*Renewables|Renewables.*Demand Management",     "RM6313 — Demand Management & Renewables DPS (CCS)", True),
    (r"\bCPCA\b.*DPS|CPCA DPS",                                      "CPCA DPS",             True),
    (r"\bYortender\b|Yortender",                                     "Yortender (portal)",   False),  # Portal — registered but not a framework
    (r"\bBloom\b.*procurement|Bloom Procurement",                    "Bloom",                True),
    (r"\bProContract\b|ProContract|Bravo.*portal",                   "ProContract (portal)", False),  # Portal — registered but not a framework
    (r"\bBlueLight\b|Blue Light.*procurement",                       "BlueLight",            True),
    (r"\bESPO\b|Eastern Shires Purchasing",                          "ESPO",                 True),
    (r"\bNEUPC\b|North Eastern Universities Purchasing",             "NEUPC",                True),
    (r"\bTPPL\b|Total Procurement Partnership",                      "TPPL",                 True),
    (r"\bLNZA\b|Local Net Zero Accelerator",                         "LNZA",                 True),

    # Other common frameworks — nordic_eligible = False (unless they apply)
    (r"\bCCS\b.*RM\d{4}|Crown Commercial Service.*RM\d{4}",         "CCS Framework",        False),
    (r"\bRM\d{4}\b",                                                  "CCS Framework",        False),
    (r"\bGCloud\b|G.Cloud",                                          "G-Cloud",              False),
    (r"\bDOS\b|Digital Outcomes",                                    "DOS/DDAT",             False),
    (r"\bNPS\b.*framework|National Procurement Service",             "NPS Wales",            False),
    (r"\bLHC\b.*framework|London Housing Consortium",                "LHC",                  False),
    (r"\bNHBC\b|NHBC.*framework",                                    "NHBC",                 False),
    (r"\bPSCA\b|Professional Services.*CCS",                         "PSCA (CCS)",           False),
    (r"\bSBS\b.*framework|Shared Business Services",                 "SBS",                  False),
    (r"\bEast.*Midlands.*framework|EMPA\b",                          "EMPA",                 False),
    (r"\bNorth.*framework|NEPO\b",                                   "NEPO",                 False),
    (r"\bSouth.*West.*framework\b",                                 "SW Framework",         False),
    (r"\bYPO\b",                                                      "YPO",                  False),
    (r"\bNET Zero.*framework|net zero.*framework",                   "Net Zero Framework",   False),
    (r"\bDESNZ\b.*framework|Department.*Energy.*framework",          "DESNZ Framework",      True),
]


def tag_tender(title: str, description: str) -> FrameworkTag:
    """
    Detect procurement route and framework for a tender.

    Returns a FrameworkTag with route, framework name,
    Nordic Energy eligibility and confidence level.
    """
    corpus = f"{title} {description}".lower()
    title_lower = title.lower()

    # ── Step 1: Detect framework name ────────────────────────────────────────
    framework_name  = "Unknown"
    nordic_eligible = False
    fw_confidence   = "Low"

    for pattern, name, eligible in FRAMEWORK_PATTERNS:
        if re.search(pattern, corpus, re.IGNORECASE):
            framework_name  = name
            nordic_eligible = eligible
            fw_confidence   = "High" if re.search(pattern, title_lower, re.IGNORECASE) else "Medium"
            break

    # ── Step 2: Detect procurement route ─────────────────────────────────────
    route      = "Unknown"
    rt_confidence = "Low"

    # Check in priority order — most specific first
    if any(re.search(p, corpus, re.IGNORECASE) for p in DPS_PATTERNS):
        route = "DPS"
        rt_confidence = "High"

    elif any(re.search(p, corpus, re.IGNORECASE) for p in FURTHER_COMPETITION_PATTERNS):
        route = "Further Competition"
        rt_confidence = "High" if any(
            re.search(p, title_lower, re.IGNORECASE) for p in FURTHER_COMPETITION_PATTERNS
        ) else "Medium"

    elif any(re.search(p, corpus, re.IGNORECASE) for p in RESTRICTED_PATTERNS):
        route = "Restricted"
        rt_confidence = "Medium"

    elif any(re.search(p, corpus, re.IGNORECASE) for p in OPEN_MARKET_PATTERNS):
        route = "Open Market"
        rt_confidence = "High"

    elif framework_name != "Unknown":
        # Framework detected but no explicit route — likely a further competition
        route = "Further Competition"
        rt_confidence = "Medium"

    else:
        # Default — most tenders on FaT/CF without framework signals are open market
        route = "Open Market"
        rt_confidence = "Low"

    # Confidence = lower of the two signals
    confidence_rank = {"High": 3, "Medium": 2, "Low": 1}
    overall = min(fw_confidence, rt_confidence,
                  key=lambda x: confidence_rank.get(x, 0))

    return FrameworkTag(
        procurement_route = route,
        framework_name    = framework_name,
        nordic_eligible   = nordic_eligible,
        confidence        = overall,
    )