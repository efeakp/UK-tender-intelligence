"""
API integration tests — uses FastAPI TestClient with a pre-populated cache.
Run with: pytest tests/test_api.py -v
"""

import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from app.main import app
from app.dependencies import cache, CACHE_KEY_TENDERS
from app.models.tender import Tender, TenderSource, ScopeTag, ScoreLabel


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _sample_tenders():
    return [
        Tender(
            id="FAT-001",
            source=TenderSource.FIND_A_TENDER,
            title="Heat Network Feasibility Study",
            authority="Manchester City Council",
            description="District heating assessment and heat pump options",
            value="£85,000",
            value_amount=85000,
            published=datetime(2025, 4, 18, tzinfo=timezone.utc),
            deadline=datetime(2025, 5, 16, tzinfo=timezone.utc),
            url="https://www.find-tender.service.gov.uk/Notice/FAT-001",
            score=8,
            score_label=ScoreLabel.STRONG,
            matched_keywords=["heat network", "district heating", "heat pump"],
            matched_scopes=[ScopeTag.OPTIMISATION.value],
        ),
        Tender(
            id="CF-001",
            source=TenderSource.CONTRACTS_FINDER,
            title="Solar PV Installation — NHS Estate",
            authority="NHS Property Services",
            description="Rooftop solar PV and battery storage across 40 NHS sites",
            value="£3,200,000",
            value_amount=3200000,
            published=datetime(2025, 4, 11, tzinfo=timezone.utc),
            deadline=datetime(2025, 5, 23, tzinfo=timezone.utc),
            url="https://www.contractsfinder.service.gov.uk/Notice/CF-001",
            score=6,
            score_label=ScoreLabel.LIKELY,
            matched_keywords=["solar pv", "battery storage"],
            matched_scopes=[ScopeTag.OPPORTUNITY_ID.value],
        ),
        Tender(
            id="CF-002",
            source=TenderSource.CONTRACTS_FINDER,
            title="Grounds Maintenance Services",
            authority="Surrey County Council",
            description="Grounds maintenance for parks and open spaces",
            value="£1,200,000",
            value_amount=1200000,
            published=datetime(2025, 4, 5, tzinfo=timezone.utc),
            deadline=datetime(2025, 5, 21, tzinfo=timezone.utc),
            url="https://www.contractsfinder.service.gov.uk/Notice/CF-002",
            score=0,
            score_label=ScoreLabel.WEAK,
            matched_keywords=[],
            matched_scopes=[],
        ),
    ]


@pytest.fixture(autouse=True)
def populate_cache():
    """Pre-populate cache before each test, clear after."""
    cache.set(CACHE_KEY_TENDERS, _sample_tenders(), ttl_minutes=60)
    yield
    cache.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ── Health ─────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["cache_populated"] is True


# ── Tenders list ───────────────────────────────────────────────────────────────

class TestListTenders:
    def test_returns_results(self, client):
        r = client.get("/tenders")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] >= 1
        assert isinstance(data["tenders"], list)

    def test_default_min_score_filters_weak(self, client):
        r = client.get("/tenders?min_score=3")
        ids = [t["id"] for t in r.json()["tenders"]]
        assert "CF-002" not in ids  # score=0

    def test_min_score_zero_includes_all(self, client):
        r = client.get("/tenders?min_score=0")
        assert r.json()["total"] == 3

    def test_filter_by_source_fat(self, client):
        r = client.get("/tenders?min_score=0&source=Find+a+Tender")
        tenders = r.json()["tenders"]
        assert all(t["source"] == "Find a Tender" for t in tenders)

    def test_filter_by_source_cf(self, client):
        r = client.get("/tenders?min_score=0&source=Contracts+Finder")
        tenders = r.json()["tenders"]
        assert all(t["source"] == "Contracts Finder" for t in tenders)

    def test_filter_by_scope(self, client):
        scope = ScopeTag.OPTIMISATION.value  # "Service 03: Energy System Optimisation"
        r = client.get(f"/tenders?min_score=0&scope={scope}")
        tenders = r.json()["tenders"]
        assert len(tenders) == 1
        assert tenders[0]["id"] == "FAT-001"

    def test_text_search(self, client):
        r = client.get("/tenders?min_score=0&q=solar")
        tenders = r.json()["tenders"]
        assert any(t["id"] == "CF-001" for t in tenders)

    def test_pagination(self, client):
        r = client.get("/tenders?min_score=0&page=1&page_size=2")
        data = r.json()
        assert data["returned"] == 2
        assert data["total"] == 3

    def test_sort_by_value_desc(self, client):
        r = client.get("/tenders?min_score=0&sort_by=value&sort_dir=desc")
        tenders = r.json()["tenders"]
        values = [t["value_amount"] or 0 for t in tenders]
        assert values == sorted(values, reverse=True)


# ── Tender detail ──────────────────────────────────────────────────────────────

class TestGetTender:
    def test_get_existing_tender(self, client):
        r = client.get("/tenders/FAT-001")
        assert r.status_code == 200
        assert r.json()["id"] == "FAT-001"

    def test_get_missing_tender(self, client):
        r = client.get("/tenders/DOES-NOT-EXIST")
        assert r.status_code == 404

    def test_tender_has_score_fields(self, client):
        r = client.get("/tenders/FAT-001")
        data = r.json()
        assert "score" in data
        assert "matched_keywords" in data
        assert "matched_scopes" in data


# ── Empty cache ────────────────────────────────────────────────────────────────

class TestEmptyCache:
    def test_503_when_cache_empty(self, client):
        cache.clear()
        r = client.get("/tenders")
        assert r.status_code == 503
