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
    "financing_cash_flow",
}
MAX_ABSOLUTE_AMOUNT = Decimal("1e18")
PERIOD_PATTERN = re.compile(r"^(?:FY)?\d{4}(?:-Q[1-4])?$", re.IGNORECASE)
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
UNIT_FACTORS = {
    "ones": Decimal("1"),
    "thousands": Decimal("1000"),
    "millions": Decimal("1000000"),
    "billions": Decimal("1000000000"),
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
        parsed[name] = value
    if not any(value is not None for value in parsed.values()):
        raise FinancialInputError("Enter at least one financial amount.")
    return parsed


def _metadata(payload: dict[str, Any]) -> dict[str, Any]:
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
    if not isinstance(source_url, str):
        raise FinancialInputError("source_url must identify the source document.")
    parsed_url = urlparse(source_url.strip())
    if parsed_url.scheme != "https" or not parsed_url.netloc or parsed_url.username or parsed_url.password:
        raise FinancialInputError("source_url must be an HTTPS URL without embedded credentials.")

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
        "currency": currency,
        "unit": unit,
        "period": period,
        "prior_period": prior_period,
        "source_url": source_url.strip(),
        "publication_date": publication_date,
        "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
        "review_status": "unreviewed",
        "human_review_required_before_publication": True,
        "normalization_factor_to_base_units": str(UNIT_FACTORS[unit]),
    }


def _metric(name: str, value: Decimal, formula: str, inputs: dict[str, Decimal]) -> dict[str, str]:
    if name.endswith("_percent"):
        units_note = "Percent"
    elif name.endswith("_ratio") or name == "current_ratio":
        units_note = "Ratio"
    else:
        units_note = "Same currency and unit as supplied inputs"
    return {
        "value_decimal": str(value),
        "value_display": format(value, ".2f"),
        "formula": formula,
        "inputs": {key: str(item) for key, item in inputs.items()},
        "units_note": units_note,
    }


def analyze_financials(payload: Any) -> dict[str, Any]:
    """Calculate only metrics whose required values and denominators are present."""
    if not isinstance(payload, dict):
        raise FinancialInputError("Request body must be a JSON object.")
    source_amounts = _parse_amounts(payload.get("amounts"))
    metadata = _metadata(payload)
    unit_factor = UNIT_FACTORS[metadata["unit"]]
    amounts = {
        name: (value * unit_factor if value is not None else None)
        for name, value in source_amounts.items()
    }
    metadata["calculation_unit"] = f"{metadata['currency']} units"
    metrics: dict[str, dict[str, Any]] = {}
    limitations: list[str] = []

    def ratio_metric(name: str, numerator: str, denominator: str, formula: str) -> None:
        top, bottom = amounts[numerator], amounts[denominator]
        if top is None or bottom is None:
            limitations.append(f"{name}: not calculated; {numerator} and {denominator} are both required.")
        elif bottom == 0:
            limitations.append(f"{name}: not defined because {denominator} is zero.")
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
    elif current_revenue == 0:
        limitations.append("Profit margins: not defined because revenue is zero.")
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

    ratio_metric("debt_to_equity_ratio", "total_debt", "total_equity", "total_debt / total_equity")
    ratio_metric("operating_cash_flow_to_net_income_ratio", "operating_cash_flow", "net_income", "operating_cash_flow / net_income")

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
        "limitations": limitations,
        "calculation_policy": {
            "arithmetic": "Decimal arithmetic with the standard 28-significant-digit context; source amounts are normalized to base currency units.",
            "rounding": "Display only is rounded half-even to two decimal places; value_decimal retains the calculation result at Decimal context precision.",
            "publication": "Inputs and derived metrics are unverified. Human review of cited sources is required before publication.",
            "persistence": "This endpoint does not store submissions.",
        },
    }