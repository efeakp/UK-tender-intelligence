"""
Unit tests for the relevance scoring engine.
Run with: pytest tests/test_scorer.py -v
"""

import pytest
from app.models.tender import Tender, TenderSource, ScopeTag, ScoreLabel
from app.services.scorer import score_tender, bulk_score


def make_tender(title: str, description: str = "") -> Tender:
    return Tender(
        id="test-001",
        source=TenderSource.CONTRACTS_FINDER,
        title=title,
        authority="Test Authority",
        description=description,
        url="https://example.com",
    )


class TestScoreTender:
    def test_strong_heat_network_match(self):
        t = make_tender(
            "District heating network feasibility study",
            "Assessment of heat network potential, district energy options, heat pump integration",
        )
        result = score_tender(t)
        assert result.score >= 7
        assert result.score_label == ScoreLabel.STRONG
        assert ScopeTag.HEAT_NETWORKS in result.matched_scopes

    def test_renewables_match(self):
        t = make_tender(
            "Offshore wind operations advisory",
            "Technical advisory services for offshore wind asset performance",
        )
        result = score_tender(t)
        assert result.score >= 4
        assert ScopeTag.RENEWABLES in result.matched_scopes

    def test_consulting_match(self):
        t = make_tender(
            "Net zero decarbonisation strategy",
            "Energy consultancy to develop a net zero roadmap and carbon reduction plan",
        )
        result = score_tender(t)
        assert result.score >= 4
        assert ScopeTag.CONSULTING in result.matched_scopes

    def test_irrelevant_tender_low_score(self):
        t = make_tender(
            "Grounds maintenance services",
            "Provision of grounds maintenance for parks and open spaces",
        )
        result = score_tender(t)
        assert result.score <= 2

    def test_score_capped_at_ten(self):
        # Flood with keywords
        desc = " ".join([
            "heat network district heating district energy heat pump",
            "solar pv wind energy biomass geothermal offshore wind",
            "net zero decarbonisation energy consultancy ESCO",
            "renewable energy CHP combined heat and power tidal energy",
        ])
        t = make_tender("Mega energy project", desc)
        result = score_tender(t)
        assert result.score <= 10

    def test_multi_word_keywords_score_higher(self):
        # "heat network" (2 words) should score 2; "solar" (1 word) scores 1
        t1 = make_tender("Heat network project")
        t2 = make_tender("Solar project")
        r1 = score_tender(t1)
        r2 = score_tender(t2)
        assert r1.score >= r2.score

    def test_matched_keywords_deduped(self):
        t = make_tender("Heat network heat network heat network")
        result = score_tender(t)
        assert result.matched_keywords.count("heat network") == 1

    def test_multi_scope_match(self):
        t = make_tender(
            "Renewable energy and heat network consultancy",
            "Advisory for solar, district heating and net zero strategy",
        )
        result = score_tender(t)
        assert len(result.matched_scopes) >= 2


class TestBulkScore:
    def test_sorted_by_score_descending(self):
        tenders = [
            make_tender("Grounds maintenance services"),
            make_tender("District heating network feasibility", "heat pump district energy"),
            make_tender("Solar PV installation", "renewable energy solar panels"),
        ]
        results = bulk_score(tenders)
        scores = [t.score for t in results]
        assert scores == sorted(scores, reverse=True)

    def test_all_tenders_returned(self):
        tenders = [make_tender(f"Tender {i}") for i in range(10)]
        results = bulk_score(tenders)
        assert len(results) == 10
