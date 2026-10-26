"""Validated, stateless multi-period company analysis."""
from __future__ import annotations

from decimal import Decimal, localcontext
from typing import Any

from finance.calculations import FORMULA_CATALOG_VERSION, FinancialInputError, analyze_financials


def _period_key(period: str) -> tuple[int, int]:
    value = period.upper().removeprefix("FY")
    if "-Q" in value:
        year, quarter = value.split("-Q")
        return int(year), int(quarter)
    return int(value), 0


def analyze_history(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FinancialInputError("Request body must be a JSON object.")
    name = payload.get("company_name")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
        raise FinancialInputError("company_name must contain 1 to 200 characters.")
    statements = payload.get("periods")
    if not isinstance(statements, list) or not 2 <= len(statements) <= 20:
        raise FinancialInputError("periods must contain between 2 and 20 financial statements.")

    analyses = [analyze_financials(statement) for statement in statements]
    currencies = {item["metadata"]["currency"] for item in analyses}
    if len(currencies) != 1:
        raise FinancialInputError("All periods must use the same currency; convert with a reviewed FX rate before comparison.")
    source_kinds = {item["metadata"]["source_kind"] for item in analyses}
    if len(source_kinds) != 1:
        raise FinancialInputError("A history cannot mix reported-source and illustrative-sample periods.")
    frequency = {"quarterly" if "-Q" in item["metadata"]["period"] else "annual" for item in analyses}
    if len(frequency) != 1:
        raise FinancialInputError("All periods must be annual or all must be quarterly; mixed period frequencies cannot be compared.")

    keyed = [(_period_key(item["metadata"]["period"]), item) for item in analyses]
    if len({key for key, _ in keyed}) != len(keyed):
        raise FinancialInputError("Duplicate reporting periods are not allowed.")
    keyed.sort(key=lambda entry: entry[0])
    ordered = [item for _, item in keyed]
    period_labels = [item["metadata"]["period"] for item in ordered]
    history_metrics: dict[str, list[dict[str, str]]] = {}
    metric_names = sorted({metric for item in ordered for metric in item["metrics"]})
    for metric_id in metric_names:
        points = []
        for item in ordered:
            metric = item["metrics"].get(metric_id)
            if metric:
                points.append({"period": item["metadata"]["period"], "value_decimal": metric["value_decimal"], "formula_id": metric["formula_id"], "formula_version": metric["formula_version"], "source_url": item["metadata"]["source_url"]})
        history_metrics[metric_id] = points

    revenue_growth_points = []
    previous_revenue = None
    previous_key = None
    has_period_gap = False
    for (key, item) in keyed:
        current_revenue = item["normalized_inputs"].get("revenue")
        if previous_key is not None:
            prior_index = previous_key[0] * 4 + previous_key[1] if next(iter(frequency)) == "quarterly" else previous_key[0]
            current_index = key[0] * 4 + key[1] if next(iter(frequency)) == "quarterly" else key[0]
            adjacent_period = current_index - prior_index == 1
            has_period_gap = has_period_gap or not adjacent_period
        else:
            adjacent_period = False
        if adjacent_period and current_revenue is not None and previous_revenue is not None and Decimal(previous_revenue) > 0:
            prior = Decimal(previous_revenue)
            current = Decimal(current_revenue)
            revenue_growth_points.append({
                "period": item["metadata"]["period"],
                "value_decimal": str((current - prior) / prior * Decimal("100")),
                "formula_id": "revenue_growth_percent",
                "formula_version": FORMULA_CATALOG_VERSION,
                "source_url": item["metadata"]["source_url"],
            })
        previous_revenue = current_revenue
        previous_key = key
    if revenue_growth_points:
        history_metrics["revenue_growth_percent"] = revenue_growth_points

    summaries = {}
    for metric_id, points in history_metrics.items():
        if len(points) < 2:
            continue
        first = Decimal(points[0]["value_decimal"])
        last = Decimal(points[-1]["value_decimal"])
        delta = last - first
        summary: dict[str, str] = {"first_period": points[0]["period"], "last_period": points[-1]["period"], "change_value": str(delta)}
        if metric_id.endswith("_percent") or metric_id.endswith("_ratio") or metric_id.endswith("_days"):
            summary["change_kind"] = "absolute_change"
        elif first != 0:
            summary["change_percent"] = str(delta / abs(first) * Decimal("100"))
            summary["change_kind"] = "relative_change"
        else:
            summary["change_kind"] = "relative_change_undefined_from_zero"
        summaries[metric_id] = summary

    cagr = None
    if next(iter(frequency)) == "annual":
        first_revenue = ordered[0]["normalized_inputs"].get("revenue")
        last_revenue = ordered[-1]["normalized_inputs"].get("revenue")
        year_span = keyed[-1][0][0] - keyed[0][0][0]
        if first_revenue is not None and last_revenue is not None and year_span > 0:
            start, end = Decimal(first_revenue), Decimal(last_revenue)
            if start > 0 and end > 0:
                with localcontext() as context:
                    context.prec = 28
                    annual_factor = Decimal(year_span) ** -1
                    cagr = {
                        "value_decimal": str(((end / start) ** annual_factor - Decimal("1")) * Decimal("100")),
                        "formula_id": "revenue_cagr_percent",
                        "formula_version": FORMULA_CATALOG_VERSION,
                        "formula": "(ending_revenue / beginning_revenue) ^ (1 / years) - 1",
                        "input_references": ["beginning_revenue", "ending_revenue", "year_span"],
                        "periods": [period_labels[0], period_labels[-1]],
                        "validation_state": "CALCULATED",
                    }

    warnings = []
    if len(ordered) < 3:
        warnings.append("Fewer than three reporting periods are present; interpret trend summaries cautiously.")
    if len({item["metadata"]["unit"] for item in ordered}) > 1:
        warnings.append("Input units varied by period; values were normalized to base currency units before comparison.")
    if has_period_gap:
        warnings.append("One or more reporting periods are missing; period-over-period revenue growth is shown only for adjacent periods.")
    if any(item["metadata"]["source_kind"] == "ILLUSTRATIVE_SAMPLE" for item in ordered):
        warnings.append("Illustrative periods are synthetic and must not be published as reported company facts.")

    return {
        "company_name": name.strip(),
        "currency": next(iter(currencies)),
        "period_frequency": next(iter(frequency)),
        "periods": ordered,
        "metric_series": history_metrics,
        "metric_changes": summaries,
        "revenue_cagr": cagr,
        "formula_catalog_version": FORMULA_CATALOG_VERSION,
        "validation_state": "ILLUSTRATIVE_ONLY" if next(iter(source_kinds)) == "ILLUSTRATIVE_SAMPLE" else "UNREVIEWED",
        "warnings": warnings,
        "persistence": "This analysis is stateless and is not stored by the server.",
    }
