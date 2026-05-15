"""
Relevance scoring engine for Nordic Energy tender matching.

Scores each tender 0–10 against Nordic Energy's four core services.

Scoring improvements (v2):
  - Title keywords score 3× weight (vs 2× for multi-word in description)
  - Negative keywords suppress obvious out-of-scope matches
  - EGAP-derived keywords added from won contract analysis
  - Combined authority / regional programme keywords added
  - Framework & procurement route signals added
"""

from typing import List, Tuple
from app.models.tender import Tender, ScopeTag, ScoreLabel
from app.services.framework_tagger import tag_tender
from app.services.watchlist import apply_watchlist


# ── Service scope constants ───────────────────────────────────────────────────
SCOPE_S01_OPPORTUNITY_ID  = ScopeTag.OPPORTUNITY_ID
SCOPE_S02_FEASIBILITY     = ScopeTag.FEASIBILITY
SCOPE_S03_OPTIMISATION    = ScopeTag.OPTIMISATION
SCOPE_S04_BUSINESS_CASE   = ScopeTag.BUSINESS_CASE


# ── Negative keywords — suppress out-of-scope matches ────────────────────────
# If ANY of these appear in the title/description, the score is halved.
# These catch energy-adjacent tenders that are not Nordic Energy's work.
NEGATIVE_KEYWORDS: List[str] = [
    # Utilities supply & billing — not consultancy
    "catering energy",
    "office energy",
    "street lighting energy",
    "energy drinks",
    "fuel cards",
    "utilities billing",
    "energy billing",
    "gas supply",
    "electricity supply contract",
    "energy procurement contract",
    "meter reading",
    "smart meter installation",
    "energy procurement framework",
    "travel energy",
    "vehicle energy",
    # Defence & military
    "military energy",
    "weapons",
    "defence energy",
    # Leisure & facilities management — not NE's work
    "gym energy",
    "leisure centre energy",
    "internal fit-out",
    "facilities management",
    "FM services",
    "cleaning services",
    "planned maintenance",
    # Crown Estate offshore leasing rounds — developer role, not consultancy
    "seabed lease",
    "leasing round",
    "licence option agreement",
    "offshore leasing",
    "development lease",
    "offshore wind farm development",
    "wind farm developer",
]


# ── Keyword taxonomy ──────────────────────────────────────────────────────────

SCOPE_KEYWORDS: dict = {

    # ── Service 01: Renewable Energy Opportunity Identification ───────────────
    SCOPE_S01_OPPORTUNITY_ID: [
        # Spatial & digital opportunity identification
        "opportunity identification",
        "opportunity mapping",
        "energy opportunity",
        "renewable opportunity",
        "site identification",
        "site assessment",
        "spatial analysis",
        "spatial planning",
        "energy spatial planning",
        "3D modelling",
        "3D energy modelling",
        "interactive modelling",
        "energy mapping",
        "energy masterplan",
        "energy master plan",
        "energy landscape",
        "local area energy plan",
        "LAEP",
        "local energy plan",
        "area energy plan",
        "heat network zoning",
        "heat network zone",
        "HNZU",
        "HNZD",
        "heat network zoning tool",
        "HNZT",
        "GIS",
        "ArcGIS",
        "geographic information system",
        "geospatial platform",
        "geospatial analysis",
        "spatial data",
        "multi-layer map",
        "digital twin",
        "energy digital twin",
        "city scale modelling",
        "city scale energy",
        "regional energy",
        "energy planning tool",
        "energy planning software",
        "decarbonisation modelling",
        "scenario modelling",
        "scenario planning",
        "net zero modelling",
        "net zero mapping",
        "net zero hub",
        "local power plan",
        "combined authority energy",
        "combined authority",
        "community energy",
        "ordnance survey",
        "place-based",
        "place based approach",
        "behind-the-meter",
        "behind the meter",
        "anchor site",
        "opportunity sites",
        "closed landfill",
        "landfill energy",
        "City Leap",
        "City Leap Replicator",
        "LNZA",
        "Local Net Zero Accelerator",
        "net zero accelerator",
        "carbon negative",
        "energy generation accelerator",
        "EGAP",
        "RESP",
        "Regional Energy Strategic Plan",
        "Energy Systems Catapult",
        "Northern Powergrid",
        "NESO",
        "National Energy System Operator",
        "foundational dataset",
        "data validation",
        # Technology identification
        "solar pv",
        "solar farm",
        "solar energy",
        "photovoltaic",
        "wind energy",
        "wind farm",
        "wind turbine",
        "offshore wind",
        "onshore wind",
        "floating wind",
        "hybrid energy",
        "hybrid solution",
        "renewable energy",
        "low carbon generation",
        "clean energy",
        "green energy",
        "heat network",
        "district heating",
        "district heat",
        "district energy",
        "district cooling",
    ],

    # ── Service 02: Energy Feasibility Studies ────────────────────────────────
    SCOPE_S02_FEASIBILITY: [
        "feasibility study",
        "feasibility assessment",
        "feasibility report",
        "feasibility and options",
        "options appraisal",
        "option appraisal",
        "options assessment",
        "options analysis",
        "technical feasibility",
        "energy feasibility",
        "pre-feasibility",
        "pre feasibility",
        "RIBA stage 2",
        "RIBA2",
        "concept design",
        "outline design",
        "preliminary design",
        "pre-FEED",
        "FEED study",
        "front end engineering",
        "technical study",
        "technical assessment",
        "technical analysis",
        "engineering study",
        "engineering assessment",
        "scoping study",
        "outline business case",
        "OBC",
        "strategic outline case",
        "SOC",
        "heat network feasibility",
        "district heating feasibility",
        "energy system study",
        "energy assessment",
        "energy audit",
        "energy survey",
        "energy review",
        "energy performance",
        "techno-economic",
        "techno economic",
        "techno-economic feasibility",
        "desktop feasibility",
        "desk-top feasibility",
        "desktop assessment",
        "investment grade audit",
        "investment grade",
        "technical due diligence",
        "due diligence",
        "technology assessment",
        "decarbonisation study",
        "decarbonisation assessment",
        "net zero study",
        "net zero assessment",
        "carbon assessment",
        "carbon reduction plan",
        "energy modelling",
        "energy model",
        "building energy model",
        "dynamic simulation",
        "counterfactual",
        "renewable energy feasibility",
        "site feasibility",
        "generation feasibility",
    ],

    # ── Service 03: Energy System Optimisation ────────────────────────────────
    SCOPE_S03_OPTIMISATION: [
        "heat network",
        "district heating",
        "district heat",
        "district energy",
        "district cooling",
        "communal heating",
        "thermal network",
        "heat distribution",
        "heat supply",
        "heat network performance",
        "heat network optimisation",
        "heat network operation",
        "heat network management",
        "network optimisation",
        "system optimisation",
        "energy optimisation",
        "energy efficiency",
        "energy performance contract",
        "ESCO",
        "energy service company",
        "guaranteed energy savings",
        "heat recovery",
        "waste heat recovery",
        "waste heat",
        "energy recovery",
        "energy from waste",
        "heat reuse",
        "private wire",
        "private wire network",
        "grid connection",
        "grid constraint",
        "grid optimisation",
        "DNO",
        "distribution network",
        "network reinforcement",
        "demand side response",
        "demand flexibility",
        "smart grid",
        "flexibility market",
        "battery storage",
        "battery energy storage",
        "BESS",
        "energy storage",
        "thermal storage",
        "grid storage",
        "storage optimisation",
        "heat pump",
        "air source heat pump",
        "ground source heat pump",
        "water source heat pump",
        "ASHP",
        "GSHP",
        "heat interface unit",
        "HIU",
        "metering and billing",
        "combined heat and power",
        "CHP",
        "co-generation",
        "cogeneration",
        "energy centre",
        "plant room",
        "primary pipework",
        "secondary pipework",
        "fifth generation",
        "5GDHC",
        "retrofit",
        "deep retrofit",
        "building fabric",
        "fabric first",
        "whole house retrofit",
        "social housing decarbonisation",
        "SHDF",
        "ECO4",
        "home upgrade grant",
        "HUG",
        "public sector decarbonisation",
        "PSDS",
        "SALIX",
    ],

    # ── Service 04: Business Case Development ────────────────────────────────
    SCOPE_S04_BUSINESS_CASE: [
        "business case",
        "business case development",
        "outline business case",
        "OBC",
        "full business case",
        "FBC",
        "strategic outline case",
        "SOC",
        "investment appraisal",
        "investment case",
        "investment readiness",
        "investment decision",
        "final investment decision",
        "FID",
        "green book",
        "HM treasury",
        "five case model",
        "benefits realisation",
        "financial modelling",
        "financial model",
        "commercial model",
        "commercial modelling",
        "economic appraisal",
        "cost benefit analysis",
        "cost-benefit analysis",
        "value for money",
        "VfM",
        "whole life cost",
        "whole life costing",
        "lifecycle cost",
        "lifecycle costing",
        "revenue model",
        "tariff modelling",
        "tariff model",
        "PPA structuring",
        "power purchase agreement",
        "offtake agreement",
        "transaction support",
        "grant funding",
        "grant application",
        "funding application",
        "funding strategy",
        "green heat network fund",
        "GHFF",
        "HNDU",
        "heat network development",
        "local investment fund",
        "LIFF",
        "UKSPF",
        "shared prosperity fund",
        "Innovate UK",
        "Horizon Europe",
        "net zero living",
        "public funding",
        "subsidy",
        "net zero fund",
        "DESNZ",
        "Department for Energy Security",
        "further competition",
        "call-off",
        "framework call",
        "ENZPS",
        "RM6313",
        "CPCA DPS",
        "dynamic purchasing system",
        "energy consulting",
        "energy consultancy",
        "energy advisory",
        "energy adviser",
        "energy advisor",
        "energy strategy",
        "decarbonisation strategy",
        "net zero strategy",
        "net zero",
        "net-zero",
        "decarbonisation",
        "decarbonization",
        "carbon reduction",
        "sustainability strategy",
        "ESG",
        "green finance",
        "impact investment",
        "inward investment",
        "stakeholder engagement",
        "public consultation",
        "community engagement",
        "project pipeline",
        "regional project pipeline",
        "City Leap",
        "LNZA",
        "net zero accelerator",
        "combined authority",
        "place-based",
    ],

}

# Flat list of all keywords
ALL_KEYWORDS: List[str] = [
    kw for kws in SCOPE_KEYWORDS.values() for kw in kws
]

# ── Nordic Energy CPV taxonomy ────────────────────────────────────────────────
# Mapped from full CPV 2007 taxonomy — 56 codes aligned to NE's four services
# Score contribution: 2pts per matching CPV code (capped in total score)
NE_CPV_CODES: dict[str, str] = {
    # Service 01 — Renewable Energy Opportunity Identification
    "09000000": "Energy sources",
    "09300000": "Electricity, heating, solar and nuclear energy",
    "09310000": "Electricity",
    "09323000": "District heating",
    "09324000": "Long-distance heating",
    "09330000": "Solar energy",
    "09331000": "Solar panels",
    "09332000": "Solar installations",
    "31121300": "Wind-energy generators",
    "31121320": "Wind turbines",
    "31121340": "Wind farm",
    # Service 01 + 03 — Heat networks & infrastructure
    "39715000": "Water heaters and heating for buildings",
    "44161000": "Pipelines",
    "45232140": "District-heating mains construction",
    "45232141": "Heating-mains construction",
    "45331000": "Heating, ventilation and air-conditioning",
    "45331100": "Central-heating installation",
    # Service 02 — Feasibility Studies
    "71240000": "Architectural, engineering and planning services",
    "71241000": "Feasibility study, advisory service, analysis",
    "71300000": "Engineering services",
    "71313000": "Environmental engineering consultancy",
    "71314000": "Energy and related services",
    "71314200": "Energy-management services",
    "71314300": "Energy-efficiency consultancy",
    "71315000": "Building-services engineering",
    "71318000": "Consultancy and advisory engineering",
    "71320000": "Engineering design services",
    "71321200": "Heating system engineering services",
    "79314000": "Feasibility study",
    "73420000": "Pre-feasibility study",
    # Service 03 — Energy System Optimisation
    "42120000": "Pumps and compressors",
    "42122000": "Pumps",
    "45231000": "Construction work for pipelines",
    "45251000": "Power plant construction work",
    "45259300": "Heating plant repair and maintenance",
    "50700000": "Repair and maintenance of building installations",
    # Service 04 — Business Case Development
    "79400000": "Business and management consultancy",
    "79410000": "Business and management consultancy services",
    "79411000": "General management consultancy services",
    "73200000": "Research and development consultancy",
    # Environmental & cross-cutting
    "71313410": "Environmental impact assessment",
    "90700000": "Environmental services",
    "90710000": "Environmental management",
    "90711000": "Environmental impact assessment",
    "90712000": "Environmental planning",
    "90712100": "Urban environmental development planning",
    "90713000": "Environmental issues consultancy services",
    "90714000": "Environmental audit services",
    "71500000": "Construction-related services",
    "71600000": "Technical testing, analysis and consultancy",
    "71621000": "Technical analysis or consultancy services",
    "72224000": "Project management consultancy services",
    "79420000": "Project-management services",
}

NE_CPV_SET = set(NE_CPV_CODES.keys())

def _score_cpv_codes(cpv_codes: List[str]) -> Tuple[int, List[str]]:
    """
    Score CPV codes against Nordic Energy taxonomy.
    Returns (bonus_points, matched_cpv_descriptions)
    Each matching CPV adds 2 points. Total CPV bonus capped at 6.
    """
    matched = []
    for code in cpv_codes:
        # Match on first 8 digits (ignore check digit suffix)
        clean = code.replace("-", "").strip()[:8]
        if clean in NE_CPV_SET:
            matched.append(f"{clean} — {NE_CPV_CODES[clean]}")
        else:
            # Try parent code match (first 4 or 6 digits)
            for prefix_len in (6, 4):
                prefix = clean[:prefix_len]
                parent = next((k for k in NE_CPV_SET if k.startswith(prefix)), None)
                if parent:
                    matched.append(f"{clean} ≈ {NE_CPV_CODES[parent]}")
                    break
    bonus = min(len(matched) * 2, 6)
    return bonus, matched

# All four service ScopeTag values
_ALL_SCOPES = {
    ScopeTag.OPPORTUNITY_ID,
    ScopeTag.FEASIBILITY,
    ScopeTag.OPTIMISATION,
    ScopeTag.BUSINESS_CASE,
}


def _normalise(text: str) -> str:
    return text.lower().strip()


def _check_negative(title: str, description: str) -> bool:
    """Return True if this tender matches a negative keyword (out of scope)."""
    corpus = _normalise(f"{title} {description}")
    return any(_normalise(nk) in corpus for nk in NEGATIVE_KEYWORDS)


def score_tender(tender: Tender) -> Tender:
    """
    Score a tender and return it with updated scoring fields.

    Scoring weights (v2):
      - Keyword in TITLE, multi-word  → 3 points
      - Keyword in TITLE, single-word → 2 points
      - Keyword in description/CPV, multi-word  → 2 points
      - Keyword in description/CPV, single-word → 1 point
      - Negative keyword match → score halved (min 0)
    Score is capped at 10.
    """
    title_corpus = _normalise(tender.title)
    full_corpus  = _normalise(
        f"{tender.title} {tender.description} {' '.join(tender.cpv_codes)}"
    )
    desc_corpus  = _normalise(
        f"{tender.description} {' '.join(tender.cpv_codes)}"
    )

    raw_score = 0
    matched: List[str] = []
    matched_scopes = []

    for scope, keywords in SCOPE_KEYWORDS.items():
        scope_hit = False
        for kw in keywords:
            kw_lower = _normalise(kw)
            is_multi  = " " in kw

            if kw_lower in title_corpus:
                # Title match — higher weight
                weight = 3 if is_multi else 2
                raw_score += weight
                matched.append(kw)
                scope_hit = True
            elif kw_lower in desc_corpus:
                # Description/CPV match — standard weight
                weight = 2 if is_multi else 1
                raw_score += weight
                matched.append(kw)
                scope_hit = True

        if scope_hit:
            matched_scopes.append(scope)

    # CPV code bonus — each matching CPV adds 2 points (capped at 6 total)
    cpv_bonus, matched_cpv = _score_cpv_codes(tender.cpv_codes)
    raw_score += cpv_bonus

    # Apply negative keyword penalty — halve the score
    if _check_negative(tender.title, tender.description):
        raw_score = raw_score // 2

    tender.score            = min(raw_score, 10)
    tender.matched_keywords = list(dict.fromkeys(matched))
    tender.matched_scopes   = [
        s.value if hasattr(s, "value") else s
        for s in matched_scopes if s in _ALL_SCOPES
    ]
    tender.score_label      = _label(tender.score)
    tender.__dict__["all_matched_scopes"] = tender.matched_scopes[:]

    # Framework tagging
    fw = tag_tender(tender.title, tender.description)
    tender.procurement_route = fw.procurement_route
    tender.framework_name    = fw.framework_name
    tender.nordic_eligible   = fw.nordic_eligible

    # Notice type boost — UK3 needs urgency, UK2 needs attention for scope influence
    if tender.notice_type == "UK3":
        # UK3 = tender dropping imminently with potentially only 10-day window
        # Boost score so it appears at the top of Early Engagement list
        tender.score = min(tender.score + 2, 10)
    elif tender.notice_type == "UK1":
        # UK1 = pipeline notice — useful intelligence, slight boost for watched frameworks
        pass  # watchlist boost already handles this

    # Watchlist — boost score and flag if authority is on the watchlist
    apply_watchlist(tender)

    return tender


def _label(score: int) -> ScoreLabel:
    if score >= 7:
        return ScoreLabel.STRONG
    if score >= 4:
        return ScoreLabel.LIKELY
    return ScoreLabel.WEAK


def bulk_score(tenders: List[Tender]) -> List[Tender]:
    """Score a list of tenders and return sorted by score descending."""
    scored = [score_tender(t) for t in tenders]
    return sorted(scored, key=lambda t: t.score, reverse=True)