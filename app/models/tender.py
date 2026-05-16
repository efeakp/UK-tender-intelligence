from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from datetime import datetime
from enum import Enum


class TenderSource(str, Enum):
    FIND_A_TENDER             = "Find a Tender"
    CONTRACTS_FINDER          = "Contracts Finder"
    SELL2WALES                = "Sell2Wales"
    PUBLIC_CONTRACTS_SCOTLAND = "Public Contracts Scotland"


class ScopeTag(str, Enum):
    """
    Nordic Energy's four core service areas (Services Menu, 2026).
    Used for tender relevance scoring and dashboard filtering.
    """
    OPPORTUNITY_ID = "Service 01: Renewable Energy Opportunity Identification"
    FEASIBILITY    = "Service 02: Energy Feasibility Studies"
    OPTIMISATION   = "Service 03: Energy System Optimisation"
    BUSINESS_CASE  = "Service 04: Business Case Development"


class ScoreLabel(str, Enum):
    STRONG = "Strong match"
    LIKELY = "Likely relevant"
    WEAK   = "Weak match"


class NoticeCategory(str, Enum):
    """
    Unified procurement stage category across both Find a Tender and
    Contracts Finder, aligned with UK public procurement terminology.

    Find a Tender mapping:
      Planning          → Early Engagement   (prior info / market engagement)
      Pipeline          → Future Opportunity (contracts > £2M, not yet live)
      Tender (active)   → Opportunity        (bids can be submitted)
      Award             → Awarded Contract   (decision published)

    Contracts Finder mapping:
      market-engagement → Early Engagement
      planning / pin    → Future Opportunity
      tender (active)   → Opportunity
      award / contract  → Awarded Contract
    """
    EARLY_ENGAGEMENT    = "Early Engagement"    # Prior info / soft market testing
    FUTURE_OPPORTUNITY  = "Future Opportunity"  # Pipeline / planned, not yet live
    OPPORTUNITY         = "Opportunity"         # Active tender — bids open
    AWARDED_CONTRACT    = "Awarded Contract"    # Contract let, decision published
    UNKNOWN             = "Unknown"             # Fallback if tag not recognised


class Tender(BaseModel):
    id:             str
    source:         TenderSource
    title:          str
    authority:      str
    description:    str
    value:          Optional[str]   = None
    value_amount:   Optional[float] = None
    value_currency: str             = "GBP"
    published:      Optional[datetime] = None
    deadline:       Optional[datetime] = None
    url:            str
    cpv_codes:      List[str] = Field(default_factory=list)

    # Procurement stage — populated by source clients
    category: str = NoticeCategory.UNKNOWN

    # Framework & procurement route — populated by framework tagger
    procurement_route: str  = "Unknown"   # Open Market | Further Competition | DPS | Restricted
    framework_name:    str  = "Unknown"   # e.g. ENZPS, RM6313, Unknown
    nordic_eligible:   bool = False       # True if NE is registered on this framework

    # Scoring — populated by scorer service
    score:              int        = 0
    score_label:        ScoreLabel = ScoreLabel.WEAK
    matched_keywords:   List[str]  = Field(default_factory=list)
    matched_scopes:     List[str]  = Field(default_factory=list)
    all_matched_scopes: List[str]  = Field(default_factory=list)

    # Procurement Act 2023 notice intelligence
    notice_type:      str            = Field(default="",   description="UK notice type: UK1/UK2/UK3/UK4/UK5/UK6 etc.")
    ocid:             str            = Field(default="",   description="OCDS procurement identifier linking all notices in the same family")

    # Delivery regions — NUTS codes extracted from tender.items[].deliveryAddresses[].region
    nuts_codes: List[str] = Field(default_factory=list)

    # Expected tender notice date — populated for UK3 Planned Procurement Notices
    # Tells us when the linked UK4 tender notice is expected to drop
    future_notice_date: Optional[datetime] = None

    # Watchlist — set by scorer via watchlist.py after check against WATCHED_AUTHORITIES
    watchlist_match:     bool         = Field(default=False,  description="True if authority is on Nordic Energy watchlist")
    watchlist_authority: str          = Field(default="",     description="Display name of matched watched authority")

    # Lot information — populated when tender.lots[] is present in the OCDS release
    lot_count: int = Field(default=0, description="Number of lots in this tender (0 = single contract)")

    # Manually injected via POST /tenders/fetch/{id} — preserved across full refreshes
    manually_added: bool = Field(default=False, description="True if added via direct fetch; survives scheduled refreshes")

    model_config = ConfigDict(use_enum_values=True)


class NoticeEntry(BaseModel):
    """A single notice in a procurement lifecycle — used by ProcurementRecord."""
    notice_id:   str
    notice_type: str
    date:        Optional[datetime]
    tag:         List[str]
    url:         str
    title:       str


class ProcurementRecord(BaseModel):
    """Full procurement lifecycle returned by GET /tenders/{id}/record."""
    ocid:           str
    title:          str
    authority:      str
    current_status: str
    current_value:  Optional[str]
    lot_count:      int
    notices:        List[NoticeEntry]
    source:         str


class TenderListResponse(BaseModel):
    total:            int
    returned:         int
    page:             int
    page_size:        int
    last_refreshed:   Optional[datetime]
    tenders:          List[Tender]


class SourceStatus(BaseModel):
    name:         str
    healthy:      bool
    last_fetched: Optional[datetime]
    tender_count: int
    error:        Optional[str] = None


class SourcesResponse(BaseModel):
    sources:                  List[SourceStatus]
    total_cached:             int
    cache_ttl_minutes:        int
    next_scheduled_refresh:   Optional[str]


class RefreshResponse(BaseModel):
    success:          bool
    message:          str
    tenders_fetched:  int
    duration_seconds: float
    errors:           List[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status:          str
    version:         str
    environment:     str
    cache_populated: bool