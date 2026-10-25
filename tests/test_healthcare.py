from decimal import Decimal

import pytest

from healthcare.metrics import HealthMetricInputError, analyze_health_kpis


@pytest.fixture()
def payload():
    return {
        "source_url": "https://example.org/operations/monthly-report.pdf",
        "period": "April 2025",
        "values": {
            "inpatient_days": "540",
            "discharges": "60",
            "occupied_bed_days": "900",
            "available_staffed_bed_days": "1000",
            "total_wait_minutes": "1200",
            "completed_consultations": "100",
            "total_discharge_turnaround_minutes": "1800",
            "completed_discharges": "60",
            "reports_received": "18",
            "reports_expected": "20",
        },
    }


def test_healthcare_metrics_use_aggregate_denominators_and_source_provenance(payload):
    result = analyze_health_kpis(payload)
    metrics = result["metrics"]
    assert Decimal(metrics["average_length_of_stay_days"]["value_decimal"]) == Decimal("9")
    assert Decimal(metrics["bed_occupancy_rate_percent"]["value_decimal"]) == Decimal("90.0")
    assert Decimal(metrics["consultation_wait_minutes"]["value_decimal"]) == Decimal("12")
    assert Decimal(metrics["discharge_turnaround_minutes"]["value_decimal"]) == Decimal("30")
    assert Decimal(metrics["reporting_completeness_percent"]["value_decimal"]) == Decimal("90.0")
    assert result["metadata"]["source_url"] == payload["source_url"]
    assert result["metadata"]["review_status"] == "unreviewed"
    assert "not stored" in result["privacy"]


def test_missing_pairs_are_not_fabricated(payload):
    payload["values"] = {"inpatient_days": "10"}
    result = analyze_health_kpis(payload)
    assert result["metrics"] == {}
    assert any("discharges" in note for note in result["limitations"])


def test_zero_denominators_are_not_calculated(payload):
    payload["values"]["discharges"] = "0"
    result = analyze_health_kpis(payload)
    assert "average_length_of_stay_days" not in result["metrics"]
    assert any("zero" in note for note in result["limitations"])


@pytest.mark.parametrize(
    ("field", "value"),
    [("occupied_bed_days", "1001"), ("reports_received", "21")],
)
def test_numerator_greater_than_denominator_is_flagged(payload, field, value):
    payload["values"][field] = value
    result = analyze_health_kpis(payload)
    key = "bed_occupancy_rate_percent" if field == "occupied_bed_days" else "reporting_completeness_percent"
    assert key not in result["metrics"]
    assert any("exceeds" in note for note in result["limitations"])


def test_invalid_or_unknown_health_data_is_rejected(payload):
    payload["values"]["completed_consultations"] = "NaN"
    with pytest.raises(HealthMetricInputError, match="finite.*non-negative"):
        analyze_health_kpis(payload)
    payload["values"] = {"patient_name": "not allowed"}
    with pytest.raises(HealthMetricInputError, match="Unknown healthcare KPI"):
        analyze_health_kpis(payload)


def test_source_must_be_https(payload):
    payload["source_url"] = "http://example.org/report.pdf"
    with pytest.raises(HealthMetricInputError, match="HTTPS"):
        analyze_health_kpis(payload)


def test_case_counts_must_be_whole_numbers(payload):
    payload["values"]["completed_consultations"] = "10.5"
    with pytest.raises(HealthMetricInputError, match="whole-number count"):
        analyze_health_kpis(payload)
