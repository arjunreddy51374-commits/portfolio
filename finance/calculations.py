"""Decimal-safe financial statement calculations with source traceability.

Values are accepted as decimal strings and returned without mutating or storing
the submitted inputs. Published figures and derived metrics remain explicitly
marked unreviewed until a human validates the cited source.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlparse


AMOUNT_FIELDS = {
    "revenue", "prior_revenue", "gross_profit", "operating_profit",
    "net_income", "current_assets", "current_liabilities", "total_debt",
    "total_equity", "inventory", "operating_cash_flow", "investing_cash_flow",
    "financing_cash_flow", "cost_of_revenue", "average_total_assets",
    "average_total_equity", "average_inventory", "average_receivables",
    "average_payables", "capital_expenditure", "cash_and_equivalents",
    "noncurrent_assets", "other_liabilities", "depreciation",
    "interest_expense", "tax_expense",
}
FORMULA_CATALOG_VERSION = "2.0.0"
MAX_ABSOLUTE_AMOUNT = Decimal("1e18")
PERIOD_PATTERN = re.compile(r"^(?:FY)?\d{4}(?:-Q[1-4])?$", re.IGNORECASE)
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
UNIT_FACTORS = {
    "ones": Decimal("1"),
    "thousands": Decimal("1000"),
    "millions": Decimal("1000000"),
    "billions": Decimal("1000000000"),
}
METRIC_DEPENDENCIES = {
    "revenue_growth_percent": ("revenue", "prior_revenue"),
    "gross_margin_percent": ("gross_profit", "revenue"),
    "operating_margin_percent": ("operating_profit", "revenue"),
    "net_margin_percent": ("net_income", "revenue"),
    "ebitda": ("operating_profit", "depreciation"),
    "ebitda_margin_percent": ("operating_profit", "depreciation", "revenue"),
    "working_capital": ("current_assets", "current_liabilities"),
    "current_ratio": ("current_assets", "current_liabilities"),
    "quick_ratio": ("current_assets", "inventory", "current_liabilities"),
    "debt_to_equity_ratio": ("total_debt", "total_equity"),
    "net_debt": ("total_debt", "cash_and_equivalents"),
    "interest_coverage_ratio": ("operating_profit", "interest_expense"),
    "return_on_assets_percent": ("net_income", "average_total_assets"),
    "return_on_equity_percent": ("net_income", "average_total_equity"),
    "asset_turnover_ratio": ("revenue", "average_total_assets"),
    "inventory_turnover_ratio": ("cost_of_revenue", "average_inventory"),
    "inventory_days": ("average_inventory", "cost_of_revenue"),
    "receivables_days": ("average_receivables", "revenue"),
    "payables_days": ("average_payables", "cost_of_revenue"),
    "working_capital_days": ("current_assets", "current_liabilities", "revenue"),
    "cash_conversion_cycle_days": ("average_inventory", "cost_of_revenue", "average_receivables", "revenue", "average_payables"),
    "operating_cash_flow_to_net_income_ratio": ("operating_cash_flow", "net_income"),
    "free_cash_flow": ("operating_cash_flow", "capital_expenditure"),
    "cash_flow_category_subtotal": ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow"),
}


class FinancialInputError(ValueError):
    """Invalid financial statement metadata or amount input."""


def _parse_amounts(raw_amounts: Any) -> dict[str, Decimal | None]:
    if not isinstance(raw_amounts, dict):
        raise FinancialInputError("amounts must be a JSON object.")
    unknown = sorted(set(raw_amounts) - AMOUNT_FIELDS)
    if unknown:
        raise FinancialInputError("Unknown financial fields: " + ", ".join(unknown))

    parsed: dict[str, Decimal | None] = {name: None for name in AMOUNT_FIELDS}
    for name, raw in raw_amounts.items():
        if raw is None or raw == "":
            continue
        if isinstance(raw, (bool, float)):
            raise FinancialInputError(f"{name} must be a finite decimal amount.")
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            raise FinancialInputError(f"{name} must be a finite decimal amount.") from None
        if not value.is_finite() or abs(value) > MAX_ABSOLUTE_AMOUNT:
            raise FinancialInputError(f"{name} must be finite and within ±1e18.")
        if name in {"revenue", "prior_revenue"} and value < 0:
            raise FinancialInputError(f"{name} cannot be negative; correct or classify the source value before analysis.")
        if name in {"cost_of_revenue", "capital_expenditure", "depreciation", "interest_expense"} and value < 0:
            raise FinancialInputError(f"{name} must be a non-negative expense amount.")
        if name in {
            "current_assets", "current_liabilities", "inventory", "total_debt",
            "cash_and_equivalents", "noncurrent_assets", "other_liabilities",
            "average_total_assets", "average_inventory", "average_receivables", "average_payables",
        } and value < 0:
            raise FinancialInputError(f"{name} cannot be negative.")
        parsed[name] = value
    if not any(value is not None for value in parsed.values()):
        raise FinancialInputError("Enter at least one financial amount.")
    return parsed


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
    source_kind = payload.get("source_kind", "PUBLIC_SOURCE")
    if not isinstance(source_kind, str) or source_kind not in {"PUBLIC_SOURCE", "ILLUSTRATIVE_SAMPLE"}:
        raise FinancialInputError("source_kind must be PUBLIC_SOURCE or ILLUSTRATIVE_SAMPLE.")
    currency = payload.get("currency")
    if not isinstance(currency, str) or not CURRENCY_PATTERN.fullmatch(currency):
        raise FinancialInputError("currency must be a three-letter code, e.g. INR.")
    unit = payload.get("unit")
    if unit not in UNIT_FACTORS:
        raise FinancialInputError("unit must be ones, thousands, millions, or billions.")
    period = payload.get("period")
    if not isinstance(period, str) or not PERIOD_PATTERN.fullmatch(period.strip()):
        raise FinancialInputError("period must be a year/FY year or a period such as 2025-Q1.")
    period = period.strip().upper()
    prior_period = payload.get("prior_period")
    if payload.get("amounts", {}).get("prior_revenue") not in (None, ""):
        if not isinstance(prior_period, str) or not PERIOD_PATTERN.fullmatch(prior_period.strip()):
            raise FinancialInputError("prior_period is required and must match the reporting-period format when prior_revenue is supplied.")
        prior_period = prior_period.strip().upper()
        current_quarter = "-Q" in period
        previous_quarter = "-Q" in prior_period
        if current_quarter != previous_quarter or prior_period == period:
            raise FinancialInputError("period and prior_period must be different periods with the same annual/quarterly frequency.")
        def period_order(value: str) -> tuple[int, int]:
            normalized = value.removeprefix("FY")
            if "-Q" in normalized:
                year, quarter = normalized.split("-Q")
                return int(year), int(quarter)
            return int(normalized), 0
        if period_order(prior_period) >= period_order(period):
            raise FinancialInputError("prior_period must be earlier than period.")
    else:
        prior_period = prior_period.strip().upper() if isinstance(prior_period, str) else None

    source_url = payload.get("source_url")
    if source_kind == "ILLUSTRATIVE_SAMPLE":
        if source_url not in (None, ""):
            raise FinancialInputError("Illustrative sample values cannot claim an external source URL.")
        source_url = None
    else:
        if not isinstance(source_url, str) or not source_url.strip():
            raise FinancialInputError("source_url must identify the source document.")
        parsed_url = urlparse(source_url.strip())
        if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username or parsed_url.password:
            raise FinancialInputError("source_url must be an HTTPS URL without embedded credentials.")
        source_url = source_url.strip()

    publication_date = payload.get("publication_date") or None
    if publication_date:
        try:
            datetime.strptime(publication_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            raise FinancialInputError("publication_date must use YYYY-MM-DD format.") from None

    retrieved_at = datetime.now(timezone.utc)
    raw_retrieved = payload.get("retrieved_at")
    if raw_retrieved:
        try:
            retrieved_at = datetime.fromisoformat(str(raw_retrieved).replace("Z", "+00:00"))
        except ValueError:
            raise FinancialInputError("retrieved_at must be an ISO-8601 timestamp.") from None
        if retrieved_at.tzinfo is None:
            raise FinancialInputError("retrieved_at must include a timezone.")

    return {
        "source_kind": source_kind,
        "currency": currency,
        "unit": unit,
        "period": period,
        "prior_period": prior_period,
        "source_url": source_url,
        "publication_date": publication_date,
        "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
        "review_status": "illustrative_only" if source_kind == "ILLUSTRATIVE_SAMPLE" else "unreviewed",
        "human_review_required_before_publication": source_kind == "PUBLIC_SOURCE",
        "normalization_factor_to_base_units": str(UNIT_FACTORS[unit]),
    }


def _metric(name: str, value: Decimal, formula: str, inputs: dict[str, Decimal]) -> dict[str, Any]:
    if name.endswith("_percent"):
        units_note = "Percent"
    elif name.endswith("_ratio") or name == "current_ratio":
        units_note = "Ratio"
    else:
        units_note = "Same currency and normalized base units"
    return {
        "formula_id": name,
        "formula_version": FORMULA_CATALOG_VERSION,
        "value_decimal": str(value),
        "value_display": format(value, ".2f"),
        "formula": formula,
        "inputs": {key: str(item) for key, item in inputs.items()},
        "input_references": list(inputs),
        "units_note": units_note,
        "validation_state": "CALCULATED",
        "warnings": [],
    }


def _metric_states(amounts: dict[str, Decimal | None], metrics: dict[str, Any], not_applicable: set[str]) -> dict[str, str]:
    states: dict[str, str] = {}
    positive_denominators = {
        "revenue_growth_percent": ("prior_revenue",),
        "gross_margin_percent": ("revenue",),
        "operating_margin_percent": ("revenue",),
        "net_margin_percent": ("revenue",),
        "ebitda_margin_percent": ("revenue",),
        "current_ratio": ("current_liabilities",),
        "quick_ratio": ("current_liabilities",),
        "debt_to_equity_ratio": ("total_equity",),
        "interest_coverage_ratio": ("interest_expense",),
        "return_on_assets_percent": ("average_total_assets",),
        "return_on_equity_percent": ("average_total_equity",),
        "asset_turnover_ratio": ("average_total_assets",),
        "inventory_turnover_ratio": ("average_inventory",),
        "inventory_days": ("cost_of_revenue",),
        "receivables_days": ("revenue",),
        "payables_days": ("cost_of_revenue",),
        "working_capital_days": ("revenue",),
        "cash_conversion_cycle_days": ("cost_of_revenue", "revenue"),
    }
    for metric_id, dependencies in METRIC_DEPENDENCIES.items():
        if metric_id in metrics:
            states[metric_id] = "CALCULATED"
        elif any(field in not_applicable for field in dependencies):
            states[metric_id] = "NOT_APPLICABLE"
        elif all(amounts[field] is not None for field in dependencies) and any(
            amounts[field] is not None and amounts[field] <= 0
            for field in positive_denominators.get(metric_id, ())
        ):
            states[metric_id] = "INVALID"
        else:
            states[metric_id] = "MISSING"
    return states


def analyze_financials(payload: Any) -> dict[str, Any]:
    """Calculate only metrics whose required values and denominators are present."""
    if not isinstance(payload, dict):
        raise FinancialInputError("Request body must be a JSON object.")
    source_amounts = _parse_amounts(payload.get("amounts"))
    metadata = _metadata(payload)
    raw_not_applicable = payload.get("not_applicable_fields", [])
    if not isinstance(raw_not_applicable, list) or any(not isinstance(item, str) for item in raw_not_applicable):
        raise FinancialInputError("not_applicable_fields must be a list of financial field names.")
    unknown_not_applicable = sorted(set(raw_not_applicable) - AMOUNT_FIELDS)
    if unknown_not_applicable:
        raise FinancialInputError("Unknown not-applicable fields: " + ", ".join(unknown_not_applicable))
    not_applicable = set(raw_not_applicable)
    if any(source_amounts[field] is not None for field in not_applicable):
        raise FinancialInputError("A field cannot contain a value and be marked not applicable.")
    unit_factor = UNIT_FACTORS[metadata["unit"]]
    amounts = {
        name: (value * unit_factor if value is not None else None)
        for name, value in source_amounts.items()
    }
    metadata["calculation_unit"] = f"{metadata['currency']} units"
    metrics: dict[str, dict[str, Any]] = {}
    limitations: list[str] = []

    def ratio_metric(name: str, numerator: str, denominator: str, formula: str, *, positive_denominator: bool = False) -> None:
        top, bottom = amounts[numerator], amounts[denominator]
        if top is None or bottom is None:
            limitations.append(f"{name}: not calculated; {numerator} and {denominator} are both required.")
        elif bottom == 0 or (positive_denominator and bottom < 0):
            requirement = "positive" if positive_denominator else "non-zero"
            limitations.append(f"{name}: not defined because {denominator} must be {requirement}.")
        else:
            metrics[name] = _metric(name, top / bottom, formula, {numerator: top, denominator: bottom})

    current_revenue, previous_revenue = amounts["revenue"], amounts["prior_revenue"]
    if current_revenue is not None and previous_revenue is not None:
        if previous_revenue <= 0:
            limitations.append("revenue_growth_percent: not defined unless prior_revenue is positive.")
        else:
            metrics["revenue_growth_percent"] = _metric(
                "revenue_growth_percent",
                (current_revenue - previous_revenue) / previous_revenue * Decimal("100"),
                "(revenue - prior_revenue) / prior_revenue × 100",
                {"revenue": current_revenue, "prior_revenue": previous_revenue},
            )

    if current_revenue is None:
        limitations.append("Profit margins: not calculated; revenue is required.")
    elif current_revenue <= 0:
        limitations.append("Profit margins: not defined because revenue is zero or negative.")
    else:
        for field, metric_name in (
            ("gross_profit", "gross_margin_percent"),
            ("operating_profit", "operating_margin_percent"),
            ("net_income", "net_margin_percent"),
        ):
            profit = amounts[field]
            if profit is None:
                limitations.append(f"{metric_name}: not calculated; {field} is missing.")
            else:
                metrics[metric_name] = _metric(
                    metric_name, profit / current_revenue * Decimal("100"),
                    f"{field} / revenue × 100", {field: profit, "revenue": current_revenue},
                )

    assets, liabilities = amounts["current_assets"], amounts["current_liabilities"]
    if assets is not None and liabilities is not None:
        metrics["working_capital"] = _metric(
            "working_capital", assets - liabilities,
            "current_assets - current_liabilities",
            {"current_assets": assets, "current_liabilities": liabilities},
        )
        if liabilities == 0:
            limitations.append("current_ratio: not defined because current_liabilities is zero.")
        else:
            metrics["current_ratio"] = _metric(
                "current_ratio", assets / liabilities,
                "current_assets / current_liabilities",
                {"current_assets": assets, "current_liabilities": liabilities},
            )
    else:
        limitations.append("working_capital/current_ratio: not calculated; current_assets and current_liabilities are both required.")

    inventory = amounts["inventory"]
    if assets is not None and liabilities is not None and inventory is not None:
        if liabilities == 0:
            limitations.append("quick_ratio: not defined because current_liabilities is zero.")
        else:
            metrics["quick_ratio"] = _metric(
                "quick_ratio", (assets - inventory) / liabilities,
                "(current_assets - inventory) / current_liabilities",
                {"current_assets": assets, "inventory": inventory, "current_liabilities": liabilities},
            )
    else:
        limitations.append("quick_ratio: not calculated; current_assets, inventory, and current_liabilities are required.")

    ratio_metric("debt_to_equity_ratio", "total_debt", "total_equity", "total_debt / total_equity", positive_denominator=True)
    ratio_metric("operating_cash_flow_to_net_income_ratio", "operating_cash_flow", "net_income", "operating_cash_flow / net_income")

    depreciation = amounts["depreciation"]
    operating_profit = amounts["operating_profit"]
    if depreciation is not None and operating_profit is not None:
        ebitda = operating_profit + depreciation
        metrics["ebitda"] = _metric("ebitda", ebitda, "operating_profit + depreciation", {"operating_profit": operating_profit, "depreciation": depreciation})
        if current_revenue is not None and current_revenue > 0:
            metrics["ebitda_margin_percent"] = _metric("ebitda_margin_percent", ebitda / current_revenue * Decimal("100"), "(operating_profit + depreciation) / revenue × 100", {"operating_profit": operating_profit, "depreciation": depreciation, "revenue": current_revenue})

    if amounts["total_debt"] is not None and amounts["cash_and_equivalents"] is not None:
        metrics["net_debt"] = _metric("net_debt", amounts["total_debt"] - amounts["cash_and_equivalents"], "total_debt - cash_and_equivalents", {"total_debt": amounts["total_debt"], "cash_and_equivalents": amounts["cash_and_equivalents"]})

    if operating_profit is not None and amounts["interest_expense"] is not None:
        if amounts["interest_expense"] <= 0:
            limitations.append("interest_coverage_ratio: not defined because interest_expense must be positive.")
        else:
            metrics["interest_coverage_ratio"] = _metric("interest_coverage_ratio", operating_profit / amounts["interest_expense"], "operating_profit / interest_expense", {"operating_profit": operating_profit, "interest_expense": amounts["interest_expense"]})

    for metric_id, numerator_field, denominator_field, formula in (
        ("return_on_assets_percent", "net_income", "average_total_assets", "net_income / average_total_assets × 100"),
        ("return_on_equity_percent", "net_income", "average_total_equity", "net_income / average_total_equity × 100"),
        ("asset_turnover_ratio", "revenue", "average_total_assets", "revenue / average_total_assets"),
    ):
        numerator, denominator = amounts[numerator_field], amounts[denominator_field]
        if numerator is not None and denominator is not None:
            if denominator <= 0:
                limitations.append(f"{metric_id}: not defined because {denominator_field} must be positive.")
            else:
                value = numerator / denominator * (Decimal("100") if metric_id.endswith("_percent") else Decimal("1"))
                metrics[metric_id] = _metric(metric_id, value, formula, {numerator_field: numerator, denominator_field: denominator})

    for metric_id, numerator_field, denominator_field, formula, scale in (
        ("inventory_turnover_ratio", "cost_of_revenue", "average_inventory", "cost_of_revenue / average_inventory", Decimal("1")),
        ("inventory_days", "average_inventory", "cost_of_revenue", "average_inventory / cost_of_revenue × 365", Decimal("365")),
        ("receivables_days", "average_receivables", "revenue", "average_receivables / revenue × 365", Decimal("365")),
        ("payables_days", "average_payables", "cost_of_revenue", "average_payables / cost_of_revenue × 365", Decimal("365")),
    ):
        numerator, denominator = amounts[numerator_field], amounts[denominator_field]
        if numerator is not None and denominator is not None:
            if denominator <= 0:
                limitations.append(f"{metric_id}: not defined because {denominator_field} must be positive.")
            else:
                metrics[metric_id] = _metric(metric_id, numerator / denominator * scale, formula, {numerator_field: numerator, denominator_field: denominator})

    if assets is not None and liabilities is not None and current_revenue is not None:
        if current_revenue <= 0:
            limitations.append("working_capital_days: not defined because revenue must be positive.")
        else:
            working_capital = assets - liabilities
            metrics["working_capital_days"] = _metric("working_capital_days", working_capital / current_revenue * Decimal("365"), "(current_assets - current_liabilities) / revenue × 365", {"current_assets": assets, "current_liabilities": liabilities, "revenue": current_revenue})

    ccc_fields = ("average_inventory", "cost_of_revenue", "average_receivables", "revenue", "average_payables")
    if all(amounts[field] is not None for field in ccc_fields):
        if amounts["cost_of_revenue"] <= 0 or amounts["revenue"] <= 0:
            limitations.append("cash_conversion_cycle_days: not defined because revenue and cost_of_revenue must be positive.")
        else:
            inventory_days = amounts["average_inventory"] / amounts["cost_of_revenue"] * Decimal("365")
            receivable_days = amounts["average_receivables"] / amounts["revenue"] * Decimal("365")
            payable_days = amounts["average_payables"] / amounts["cost_of_revenue"] * Decimal("365")
            metrics["cash_conversion_cycle_days"] = _metric("cash_conversion_cycle_days", inventory_days + receivable_days - payable_days, "inventory_days + receivables_days - payables_days", {field: amounts[field] for field in ccc_fields})

    if amounts["operating_cash_flow"] is not None and amounts["capital_expenditure"] is not None:
        metrics["free_cash_flow"] = _metric("free_cash_flow", amounts["operating_cash_flow"] - amounts["capital_expenditure"], "operating_cash_flow - capital_expenditure (entered as a positive outflow)", {"operating_cash_flow": amounts["operating_cash_flow"], "capital_expenditure": amounts["capital_expenditure"]})

    cashflow_fields = ("operating_cash_flow", "investing_cash_flow", "financing_cash_flow")
    if all(amounts[name] is not None for name in cashflow_fields):
        values = {name: amounts[name] for name in cashflow_fields}
        net_change = sum(values.values(), Decimal("0"))
        metrics["cash_flow_category_subtotal"] = _metric(
            "cash_flow_category_subtotal", net_change,
            "operating_cash_flow + investing_cash_flow + financing_cash_flow", values,
        )
        limitations.append("Cash-flow category subtotal is not necessarily the statement's net change in cash; exchange-rate effects and statement-specific reconciliation items are not included unless supplied as part of a category.")
    else:
        limitations.append("Cash-flow total: not calculated; operating, investing, and financing cash flows are all required.")

    return {
        "metadata": metadata,
        "source_inputs": {name: (str(value) if value is not None else None) for name, value in source_amounts.items()},
        "normalized_inputs": {name: (str(value) if value is not None else None) for name, value in amounts.items()},
        "metrics": metrics,
        "metric_states": _metric_states(amounts, metrics, not_applicable),
        "not_applicable_fields": sorted(not_applicable),
        "limitations": limitations,
        "calculation_policy": {
            "arithmetic": "Decimal arithmetic with the standard 28-significant-digit context; source amounts are normalized to base currency units.",
            "rounding": "Display only is rounded half-even to two decimal places; value_decimal retains the calculation result at Decimal context precision.",
            "publication": (
                "Illustrative sample values are synthetic, have no external source, and must not be published as reported facts."
                if metadata["source_kind"] == "ILLUSTRATIVE_SAMPLE"
                else "Inputs and derived metrics are unverified. Human review of cited sources is required before publication."
            ),
            "persistence": "This endpoint does not store submissions.",
        },
    }
