"""
Unit tests for the aggregator deduplication logic.
Run with: pytest tests/test_aggregator.py -v
"""

import pytest
from app.models.tender import Tender, TenderSource
from app.services.aggregator import _deduplicate, _similarity, _normalise


def make_tender(id: str, title: str, source=TenderSource.CONTRACTS_FINDER) -> Tender:
    return Tender(
        id=id,
        source=source,
        title=title,
        authority="Test Authority",
        description="",
        url="https://example.com",
    )


class TestSimilarity:
    def test_identical_strings(self):
        assert _similarity("heat network feasibility", "heat network feasibility") == 1.0

    def test_completely_different(self):
        assert _similarity("solar energy project", "grounds maintenance services") == 0.0

    def test_partial_overlap(self):
        score = _similarity("heat network feasibility study", "heat network assessment")
        assert 0.0 < score < 1.0

    def test_empty_strings(self):
        assert _similarity("", "") == 0.0


class TestNormalise:
    def test_lowercases(self):
        assert _normalise("HEAT NETWORK") == "heat network"

    def test_strips_punctuation(self):
        assert _normalise("net-zero strategy!") == "netzero strategy"

    def test_strips_whitespace(self):
        assert _normalise("  heat network  ") == "heat network"


class TestDeduplicate:
    def test_removes_near_duplicates(self):
        tenders = [
            make_tender("FAT-001", "Heat Network Feasibility Study Manchester", TenderSource.FIND_A_TENDER),
            make_tender("CF-001", "Heat Network Feasibility Study Manchester", TenderSource.CONTRACTS_FINDER),
        ]
        result = _deduplicate(tenders)
        assert len(result) == 1

    def test_keeps_distinct_tenders(self):
        tenders = [
            make_tender("FAT-001", "District Heating Network Design"),
            make_tender("CF-001", "Solar PV Installation NHS Sites"),
            make_tender("CF-002", "Grounds Maintenance Services"),
        ]
        result = _deduplicate(tenders)
        assert len(result) == 3

    def test_preserves_order_first_seen(self):
        tenders = [
            make_tender("FAT-001", "Heat Network Feasibility Study", TenderSource.FIND_A_TENDER),
            make_tender("CF-001", "Heat Network Feasibility Study", TenderSource.CONTRACTS_FINDER),
        ]
        result = _deduplicate(tenders)
        assert result[0].source == TenderSource.FIND_A_TENDER

    def test_empty_list(self):
        assert _deduplicate([]) == []

    def test_single_tender(self):
        tenders = [make_tender("CF-001", "Solar energy project")]
        assert len(_deduplicate(tenders)) == 1
