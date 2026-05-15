from pydantic import BaseModel, Field
from typing import Optional
from app.models.tender import TenderSource, ScopeTag


class TenderFilters(BaseModel):
    """Query parameters for the /tenders endpoint."""

    q: Optional[str] = Field(
        default=None,
        description="Free-text search across title, authority, and description",
    )
    source: Optional[TenderSource] = Field(
        default=None,
        description=(
            "Filter by data source. "
            "Options: 'Find a Tender' | 'Contracts Finder' | "
            "'Sell2Wales' | 'Public Contracts Scotland'"
        ),
    )
    scope: Optional[str] = Field(
        default=None,
        description=(
            "Filter by matched Nordic Energy service scope. "
            "Options: "
            "'Service 01: Renewable Energy Opportunity Identification' | "
            "'Service 02: Energy Feasibility Studies' | "
            "'Service 03: Energy System Optimisation' | "
            "'Service 04: Business Case Development'"
        ),
    )
    category: Optional[str] = Field(
        default=None,
        description=(
            "Filter by procurement stage category. "
            "Options: 'Opportunity' | 'Future Opportunity' | "
            "'Early Engagement' | 'Awarded Contract'"
        ),
    )
    min_score: int = Field(
        default=3,
        ge=0,
        le=10,
        description="Minimum relevance score (0–10). Default is 3.",
    )
    page: int = Field(
        default=1,
        ge=1,
        description="Page number (1-indexed)",
    )
    page_size: int = Field(
        default=25,
        ge=1,
        le=2000,
        description="Results per page. Max 2000.",
    )
    sort_by: str = Field(
        default="score",
        description="Sort field: score | deadline | published | value",
    )
    sort_dir: str = Field(
        default="desc",
        description="Sort direction: asc | desc",
    )