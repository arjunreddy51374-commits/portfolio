"""Source-attributed healthcare operational KPI calculations.

Accepts only aggregated counts/time totals. No patient-level observations are
needed or retained. These definitions are examples and must be aligned with the
reporting organization before operational interpretation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


FIELD_DEFINITIONS = {
    "inpatient_days": ("Inpatient days", "Patient-days accrued by the discharge cohort during the period."),
    "discharges": ("Discharges", "Inpatient discharges included in the same cohort and period."),
    "occupied_bed_days": ("Occupied bed-days", "Occupied staffed bed-days during the selected reporting period."),
    "available_staffed_bed_days": ("Available staffed bed-days", "Staffed beds available each day, summed across the period."),
    "total_wait_minutes": ("Total consultation wait minutes", "Sum of elapsed wait minutes for included completed consultations."),
    "completed_consultations": ("Completed consultations", "Completed consultations included in the wait-time total."),
    "total_discharge_turnaround_minutes": ("Total discharge turnaround minutes", "Sum from documented discharge decision to bed-ready/release time, as locally defined."),
    "completed_discharges": ("Completed discharges", "Discharges included in the turnaround total."),
    "reports_received": ("Reports received", "Required reports received for the selected reporting cycle."),
    "reports_expected": ("Reports expected", "Reports due for the same reporting cycle and scope."),
}
COUNT_FIELDS = {
    "discharges", "completed_consultations", "completed_discharges",
    "reports_received", "reports_expected",
}


class HealthMetricInputError(ValueError):
    """Invalid aggregated healthcare metric input."""


def _decimal_value(field: str, raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (bool, float)):
        raise HealthMetricInputError(f"{field} must be supplied as a non-negative decimal string or integer.")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise HealthMetricInputError(f"{field} must be a finite non-negative number.") from None
    if not value.is_finite():
        raise HealthMetricInputError(f"{field} must be finite and non-negative.")
    if value < 0 or value > Decimal("1e15"):
        raise HealthMetricInputError(f"{field} must be finite and between 0 and 1e15.")
    return value


def analyze_health_kpis(payload: Any) -> dict[str, Any]:
    """Calculate only KPIs with compatible aggregated numerator/denominator."""
    if not isinstance(payload, dict):
        raise HealthMetricInputError("Request body must be a JSON object.")
    period = payload.get("period")
    if not isinstance(period, str) or not period.strip() or len(period.strip()) > 80:
        raise HealthMetricInputError("period must identify the reporting period.")
    source_url = payload.get("source_url")
    parsed = urlparse(source_url.strip()) if isinstance(source_url, str) else None
    if not parsed or parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise HealthMetricInputError("source_url must be an HTTPS URL without embedded credentials.")

    raw_values = payload.get("values")
    if not isinstance(raw_values, dict):
        raise HealthMetricInputError("values must be a JSON object containing aggregated totals.")
    unknown = set(raw_values) - set(FIELD_DEFINITIONS)
    if unknown:
        raise HealthMetricInputError("Unknown healthcare KPI inputs: " + ", ".join(sorted(unknown)))
    values = {field: _decimal_value(field, raw_values.get(field)) for field in FIELD_DEFINITIONS}
    for field in COUNT_FIELDS:
        if values[field] is not None and values[field] != values[field].to_integral_value():
            raise HealthMetricInputError(f"{field} must be a whole-number count.")
    metrics: dict[str, dict[str, str]] = {}
    limitations: list[str] = []

    definitions = [
        ("average_length_of_stay_days", "inpatient_days", "discharges", "days", "inpatient_days / discharges", "Average length of stay"),
        ("bed_occupancy_rate_percent", "occupied_bed_days", "available_staffed_bed_days", "percent", "occupied_bed_days / available_staffed_bed_days × 100", "Bed occupancy rate"),
        ("consultation_wait_minutes", "total_wait_minutes", "completed_consultations", "minutes", "total_wait_minutes / completed_consultations", "Mean consultation wait time"),
        ("discharge_turnaround_minutes", "total_discharge_turnaround_minutes", "completed_discharges", "minutes", "total_discharge_turnaround_minutes / completed_discharges", "Mean discharge turnaround"),
        ("reporting_completeness_percent", "reports_received", "reports_expected", "percent", "reports_received / reports_expected × 100", "Reporting completeness"),
    ]
    for key, numerator, denominator, unit, formula, label in definitions:
        top, bottom = values[numerator], values[denominator]
        if top is None or bottom is None:
            limitations.append(f"{label}: not calculated; provide {numerator} and {denominator} for the same period and scope.")
            continue
        if bottom == 0:
            limitations.append(f"{label}: not defined because {denominator} is zero.")
            continue
        if key in {"bed_occupancy_rate_percent", "reporting_completeness_percent"} and top > bottom:
            limitations.append(f"{label}: not calculated because {numerator} exceeds {denominator}; check scope, units, and source definitions.")
            continue
        result = top / bottom * (Decimal("100") if unit == "percent" else Decimal("1"))
        metrics[key] = {
            "label": label,
            "value_decimal": str(result),
            "value_display": format(result, ".2f"),
            "unit": unit,
            "formula": formula,
            "inputs": {numerator: str(top), denominator: str(bottom)},
        }

    return {
        "metadata": {
            "period": period.strip(),
            "source_url": source_url.strip(),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "review_status": "unreviewed",
            "human_review_required_before_publication": True,
        },
        "field_definitions": FIELD_DEFINITIONS,
        "metrics": metrics,
        "limitations": limitations,
        "privacy": "Only aggregate totals are accepted. Inputs are not stored by this endpoint; do not submit patient-level or identifying information.",
        "disclaimer": "Demonstration calculations only. KPI definitions, cohorts, exclusions, and reporting scope must be approved by the relevant organization before operational use.",
    }