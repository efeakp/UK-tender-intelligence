"""
Unit tests for the Contracts Finder OCDS client parser.
Validates correct parsing of OCDS release objects per the CF OCDS guide v2.1.

Run with: pytest tests/test_cf_client.py -v
"""

import pytest
from datetime import datetime, timezone
from app.services.contracts_finder import _parse_ocds_release, _parse_dt
from app.models.tender import TenderSource


# ── Sample OCDS fixtures ───────────────────────────────────────────────────────

def tender_release(overrides: dict = {}) -> dict:
    """Minimal valid OCDS tender release."""
    base = {
        "ocid": "ocds-b5fd17-abc12345",
        "date": "2025-04-18T10:00:00Z",
        "tag": ["tender"],
        "buyer": {"name": "Manchester City Council"},
        "tender": {
            "title": "Heat Network Feasibility Study",
            "description": "District heating assessment and heat pump options appraisal.",
            "value": {"amount": 85000, "currency": "GBP"},
            "tenderPeriod": {"endDate": "2025-05-16T12:00:00Z"},
            "items": [
                {
                    "classification": {
                        "id": "45232140",
                        "scheme": "CPV",
                        "description": "District-heating mains construction works",
                    }
                }
            ],
        },
    }
    base.update(overrides)
    return base


def award_release(overrides: dict = {}) -> dict:
    """Minimal valid OCDS award release."""
    base = {
        "ocid": "ocds-b5fd17-def67890",
        "date": "2025-03-10T09:00:00Z",
        "tag": ["award"],
        "buyer": {"name": "NHS Property Services"},
        "tender": {
            "title": "Solar PV Installation — NHS Estate",
            "description": "Rooftop solar PV and battery storage across 40 NHS sites.",
            "value": {"amount": 3000000, "currency": "GBP"},
            "tenderPeriod": {},
            "items": [],
        },
        "awards": [
            {"value": {"amount": 3200000, "currency": "GBP"}}
        ],
    }
    base.update(overrides)
    return base


# ── Parser tests ───────────────────────────────────────────────────────────────

class TestParseOcdsRelease:
    def test_parses_tender_release(self):
        result = _parse_ocds_release(tender_release())
        assert result is not None
        assert result.source == TenderSource.CONTRACTS_FINDER
        assert result.title == "Heat Network Feasibility Study"
        assert result.authority == "Manchester City Council"

    def test_id_prefixed_with_cf(self):
        result = _parse_ocds_release(tender_release())
        assert result.id.startswith("CF-ocds-b5fd17-")

    def test_tender_value_parsed(self):
        result = _parse_ocds_release(tender_release())
        assert result.value_amount == 85000.0
        assert result.value == "£85,000"
        assert result.value_currency == "GBP"

    def test_award_uses_award_value_over_tender_value(self):
        """Award releases should prefer awards[0].value over tender.value."""
        result = _parse_ocds_release(award_release())
        assert result.value_amount == 3200000.0  # award value, not tender estimate
        assert result.value == "£3,200,000"

    def test_published_date_parsed(self):
        result = _parse_ocds_release(tender_release())
        assert result.published == datetime(2025, 4, 18, 10, 0, 0, tzinfo=timezone.utc)

    def test_deadline_from_tender_period(self):
        result = _parse_ocds_release(tender_release())
        assert result.deadline == datetime(2025, 5, 16, 12, 0, 0, tzinfo=timezone.utc)

    def test_deadline_none_when_absent(self):
        release = tender_release()
        release["tender"]["tenderPeriod"] = {}
        result = _parse_ocds_release(release)
        assert result.deadline is None

    def test_cpv_codes_extracted_from_items(self):
        result = _parse_ocds_release(tender_release())
        assert "45232140" in result.cpv_codes

    def test_non_cpv_scheme_ignored(self):
        release = tender_release()
        release["tender"]["items"] = [
            {"classification": {"id": "Z99", "scheme": "UNSPSC"}}
        ]
        result = _parse_ocds_release(release)
        assert "Z99" not in result.cpv_codes

    def test_url_constructed_from_ocid(self):
        # Real CF releases always have a release.id — use one to exercise the
        # version-suffix-stripping logic (format: "{guid}-{version_number}")
        release = tender_release({"id": "abc12345-896514"})
        result = _parse_ocds_release(release)
        assert "contractsfinder.service.gov.uk/Notice/" in result.url
        assert "ocds-b5fd17-" not in result.url  # ocid prefix absent from URL

    def test_missing_tender_block_returns_none(self):
        release = {"ocid": "ocds-b5fd17-xyz", "date": "2025-04-01", "tag": ["tender"]}
        # No "buyer" or "tender" key — should not raise, may return a partial result
        # Parser should handle gracefully
        result = _parse_ocds_release(release)
        # Either returns None or a tender with defaults — must not raise
        if result:
            assert result.title in ("Untitled", "")

    def test_value_none_when_absent(self):
        release = tender_release()
        release["tender"]["value"] = {}
        result = _parse_ocds_release(release)
        assert result.value_amount is None
        assert result.value == "Value not stated"

    def test_direct_classification_also_captured(self):
        """Some CF releases put CPV on tender.classification directly."""
        release = tender_release()
        release["tender"]["classification"] = {"id": "09300000", "scheme": "CPV"}
        release["tender"]["items"] = []
        result = _parse_ocds_release(release)
        assert "09300000" in result.cpv_codes

    def test_cpv_deduplication(self):
        """Same CPV in both items and classification should appear only once."""
        release = tender_release()
        release["tender"]["classification"] = {"id": "45232140", "scheme": "CPV"}
        result = _parse_ocds_release(release)
        assert result.cpv_codes.count("45232140") == 1


class TestParseDt:
    def test_z_suffix_format(self):
        dt = _parse_dt("2025-04-18T10:00:00Z")
        assert dt == datetime(2025, 4, 18, 10, 0, 0, tzinfo=timezone.utc)

    def test_microsecond_format(self):
        dt = _parse_dt("2025-04-18T10:00:00.000Z")
        assert dt is not None

    def test_date_only_format(self):
        dt = _parse_dt("2025-04-18")
        assert dt == datetime(2025, 4, 18, 0, 0, 0, tzinfo=timezone.utc)

    def test_none_returns_none(self):
        assert _parse_dt(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_dt("") is None

    def test_unparseable_returns_none(self):
        assert _parse_dt("not-a-date") is None
