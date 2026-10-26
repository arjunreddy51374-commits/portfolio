"""Deterministic, source-linked annual scenario projections."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from finance.calculations import FinancialInputError, analyze_financials


MODEL_VERSION = "three-statement-scenarios-1.0.0"
DEFAULTS_VERSION = "model-assumptions-1.0.0"
REQUIRED_BASE_FIELDS = {
    "revenue", "gross_profit", "operating_profit", "net_income", "current_assets",
    "current_liabilities", "inventory", "total_debt", "total_equity",
    "cash_and_equivalents", "noncurrent_assets", "other_liabilities",
}
ASSUMPTION_FIELDS = {
    "revenue_growth_pct", "gross_margin_pct", "operating_expense_pct", "tax_rate_pct",
    "interest_rate_pct", "depreciation_pct", "capex_pct", "noncash_current_assets_pct",
    "current_liabilities_pct", "inventory_pct", "dividend_payout_pct", "annual_debt_change",
    "upside_growth_delta_pct", "downside_growth_delta_pct", "operating_margin_delta_pp",
}


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, (bool, float)) or value is None:
        raise FinancialInputError(f"{name} must be a finite decimal string or integer.")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise FinancialInputError(f"{name} must be a finite decimal string or integer.") from None
    if not number.is_finite() or abs(number) > Decimal("1e18"):
        raise FinancialInputError(f"{name} must be finite and within ±1e18.")
    return number


def _raw_amounts(statement: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Decimal | None]:
    return {
        key: (Decimal(value) if value is not None else None)
        for key, value in analysis["source_inputs"].items()
    }


def _require_base(statement: Any) -> tuple[dict[str, Any], dict[str, Decimal | None]]:
    analysis = analyze_financials(statement)
    amounts = _raw_amounts(statement, analysis)
    missing = sorted(field for field in REQUIRED_BASE_FIELDS if amounts.get(field) is None)
    if missing:
        raise FinancialInputError("A three-statement model requires these base-period values: " + ", ".join(missing) + ".")
    period = analysis["metadata"]["period"]
    if "-Q" in period:
        raise FinancialInputError("Annual scenario modelling requires an annual base period, not a quarter.")
    revenue = amounts["revenue"]
    if revenue is None or revenue <= 0:
        raise FinancialInputError("A three-statement model requires positive base-period revenue.")
    for field in ("current_assets", "current_liabilities", "inventory", "total_debt", "cash_and_equivalents", "noncurrent_assets", "other_liabilities"):
        if amounts[field] is not None and amounts[field] < 0:
            raise FinancialInputError(f"{field} cannot be negative in the opening balance sheet.")
    if amounts["cash_and_equivalents"] > amounts["current_assets"]:
        raise FinancialInputError("cash_and_equivalents cannot exceed current_assets.")
    noncash_current_assets = amounts["current_assets"] - amounts["cash_and_equivalents"]
    if amounts["inventory"] > noncash_current_assets:
        raise FinancialInputError("inventory cannot exceed current assets excluding cash.")
    return analysis, amounts


def default_assumptions(statement: Any) -> dict[str, Any]:
    """Derive transparent driver starting points from one validated annual statement."""
    analysis, amounts = _require_base(statement)
    revenue = amounts["revenue"]
    assert revenue is not None and revenue > 0

    def pct(numerator: Decimal | None, denominator: Decimal = revenue, fallback: str = "0") -> str:
        if numerator is None or denominator == 0:
            return fallback
        return str(numerator / denominator * Decimal("100"))

    prior = amounts.get("prior_revenue")
    growth = str((revenue - prior) / prior * Decimal("100")) if prior is not None and prior > 0 else "10"
    gross_profit = amounts["gross_profit"]
    operating_profit = amounts["operating_profit"]
    current_assets = amounts["current_assets"]
    current_liabilities = amounts["current_liabilities"]
    cash = amounts["cash_and_equivalents"]
    debt = amounts["total_debt"]
    interest = amounts.get("interest_expense")
    tax_expense = amounts.get("tax_expense")
    pre_tax = (operating_profit - interest) if operating_profit is not None and interest is not None else None
    candidate_tax_rate = tax_expense / pre_tax * Decimal("100") if tax_expense is not None and pre_tax is not None and pre_tax > 0 else None
    tax_rate = str(candidate_tax_rate) if candidate_tax_rate is not None and Decimal("0") <= candidate_tax_rate <= Decimal("100") else "25"
    candidate_interest_rate = interest / debt * Decimal("100") if interest is not None and debt is not None and debt > 0 else None
    interest_rate = str(candidate_interest_rate) if candidate_interest_rate is not None and Decimal("0") <= candidate_interest_rate <= Decimal("100") else "8"
    assumptions = {
        "revenue_growth_pct": growth,
        "gross_margin_pct": pct(gross_profit),
        "operating_expense_pct": pct(gross_profit - operating_profit if gross_profit is not None and operating_profit is not None else None, fallback="0"),
        "tax_rate_pct": tax_rate,
        "interest_rate_pct": interest_rate,
        "depreciation_pct": pct(amounts.get("depreciation"), fallback="3.5"),
        "capex_pct": pct(amounts.get("capital_expenditure"), fallback="6.5"),
        "noncash_current_assets_pct": pct(current_assets - cash if current_assets is not None and cash is not None else None, fallback="50"),
        "current_liabilities_pct": pct(current_liabilities, fallback="30"),
        "inventory_pct": pct(amounts.get("inventory"), fallback="12"),
        "dividend_payout_pct": "0",
        "annual_debt_change": "0",
        "upside_growth_delta_pct": "5",
        "downside_growth_delta_pct": "5",
        "operating_margin_delta_pp": "2",
    }
    origins = {
        "revenue_growth_pct": "last reported period comparison, or 10% starting assumption when unavailable",
        "gross_margin_pct": "gross_profit / revenue",
        "operating_expense_pct": "(gross_profit - operating_profit) / revenue",
        "tax_rate_pct": "reported tax_expense / estimated pre-tax operating profit, or 25% starting assumption",
        "interest_rate_pct": "reported interest_expense / total_debt, or 8% starting assumption",
        "depreciation_pct": "reported depreciation / revenue, or 3.5% starting assumption",
        "capex_pct": "reported capital_expenditure / revenue, or 6.5% starting assumption",
        "noncash_current_assets_pct": "(current_assets - cash_and_equivalents) / revenue",
        "current_liabilities_pct": "current_liabilities / revenue",
        "inventory_pct": "inventory / revenue",
        "dividend_payout_pct": "user starting assumption; defaults to zero",
        "annual_debt_change": "user starting assumption in the selected currency and unit; defaults to zero",
        "upside_growth_delta_pct": "user starting assumption; defaults to +5 percentage points",
        "downside_growth_delta_pct": "user starting assumption; defaults to -5 percentage points",
        "operating_margin_delta_pp": "user starting assumption; defaults to ±2 percentage points",
    }
    return {
        "model_version": MODEL_VERSION,
        "assumption_version": DEFAULTS_VERSION,
        "source_period": analysis["metadata"]["period"],
        "currency": analysis["metadata"]["currency"],
        "unit": analysis["metadata"]["unit"],
        "values": assumptions,
        "origins": origins,
    }


def _assumptions(raw: Any, defaults: dict[str, Any], years: Any) -> tuple[dict[str, Decimal], int]:
    if raw is None:
        raw = defaults["values"]
    if not isinstance(raw, dict):
        raise FinancialInputError("assumptions must be a JSON object.")
    unknown = sorted(set(raw) - ASSUMPTION_FIELDS)
    if unknown:
        raise FinancialInputError("Unknown model assumptions: " + ", ".join(unknown))
    values: dict[str, Decimal] = {}
    for key in ASSUMPTION_FIELDS:
        if key not in raw:
            raise FinancialInputError(f"Missing model assumption: {key}.")
        values[key] = _decimal(raw[key], key)
    bounds = {
        "revenue_growth_pct": (Decimal("-99"), Decimal("300")),
        "gross_margin_pct": (Decimal("-100"), Decimal("100")),
        "operating_expense_pct": (Decimal("-100"), Decimal("200")),
        "tax_rate_pct": (Decimal("0"), Decimal("100")),
        "interest_rate_pct": (Decimal("0"), Decimal("100")),
        "depreciation_pct": (Decimal("0"), Decimal("100")),
        "capex_pct": (Decimal("0"), Decimal("200")),
        "noncash_current_assets_pct": (Decimal("0"), Decimal("500")),
        "current_liabilities_pct": (Decimal("0"), Decimal("500")),
        "inventory_pct": (Decimal("0"), Decimal("500")),
        "dividend_payout_pct": (Decimal("0"), Decimal("100")),
        "upside_growth_delta_pct": (Decimal("0"), Decimal("100")),
        "downside_growth_delta_pct": (Decimal("0"), Decimal("100")),
        "operating_margin_delta_pp": (Decimal("0"), Decimal("100")),
    }
    for key, (low, high) in bounds.items():
        if not low <= values[key] <= high:
            raise FinancialInputError(f"{key} must be between {low} and {high}.")
    if values["revenue_growth_pct"] - values["downside_growth_delta_pct"] <= -100:
        raise FinancialInputError("Downside revenue growth must remain above -100%.")
    debt_change = values["annual_debt_change"]
    values["annual_debt_change"] = debt_change
    if isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 10:
        raise FinancialInputError("forecast_years must be an integer from 1 to 10.")
    return values, years


def _model_value(value: Decimal, formula_id: str, formula: str, inputs: list[str], unit: str, warnings: list[str] | None = None) -> dict[str, Any]:
    return {
        "value_decimal": str(value),
        "value_display": format(value, ".2f"),
        "unit": unit,
        "formula_id": formula_id,
        "formula_version": MODEL_VERSION,
        "formula": formula,
        "input_references": inputs,
        "validation_state": "WARNING" if warnings else "CALCULATED",
        "warnings": warnings or [],
    }


def _projection(base: dict[str, Decimal | None], assumptions: dict[str, Decimal], years: int, growth_delta: Decimal, margin_delta: Decimal, scenario: str, unit: str, base_year: int) -> list[dict[str, Any]]:
    revenue = base["revenue"]
    cash = base["cash_and_equivalents"]
    noncash_assets = base["current_assets"] - cash
    current_liabilities = base["current_liabilities"]
    noncurrent_assets = base["noncurrent_assets"]
    debt = base["total_debt"]
    other_liabilities = base["other_liabilities"]
    equity = base["total_equity"]
    previous_nwc = noncash_assets - current_liabilities
    growth = assumptions["revenue_growth_pct"] + growth_delta
    opex_ratio = assumptions["operating_expense_pct"] - margin_delta
    output = []

    for offset in range(1, years + 1):
        period_revenue = revenue * (Decimal("1") + growth / Decimal("100"))
        gross_profit = period_revenue * assumptions["gross_margin_pct"] / Decimal("100")
        operating_expense = period_revenue * opex_ratio / Decimal("100")
        operating_profit = gross_profit - operating_expense
        depreciation = period_revenue * assumptions["depreciation_pct"] / Decimal("100")
        ebitda = operating_profit + depreciation
        debt_change = assumptions["annual_debt_change"]
        ending_debt = debt + debt_change
        if ending_debt < 0:
            raise FinancialInputError(f"{scenario} scenario produces negative debt in FY{base_year + offset}.")
        interest = (debt + ending_debt) / Decimal("2") * assumptions["interest_rate_pct"] / Decimal("100")
        pretax = operating_profit - interest
        tax = max(pretax, Decimal("0")) * assumptions["tax_rate_pct"] / Decimal("100")
        net_income = pretax - tax
        dividends = max(net_income, Decimal("0")) * assumptions["dividend_payout_pct"] / Decimal("100")
        capex = period_revenue * assumptions["capex_pct"] / Decimal("100")
        ending_noncash_assets = period_revenue * assumptions["noncash_current_assets_pct"] / Decimal("100")
        ending_current_liabilities = period_revenue * assumptions["current_liabilities_pct"] / Decimal("100")
        inventory = period_revenue * assumptions["inventory_pct"] / Decimal("100")
        if inventory > ending_noncash_assets:
            raise FinancialInputError(f"Inventory exceeds non-cash current assets in {scenario} scenario FY{base_year + offset}.")
        change_nwc = (ending_noncash_assets - ending_current_liabilities) - previous_nwc
        cfo = net_income + depreciation - change_nwc
        cfi = -capex
        financing = debt_change - dividends
        net_change_cash = cfo + cfi + financing
        ending_cash = cash + net_change_cash
        ending_noncurrent_assets = noncurrent_assets + capex - depreciation
        ending_equity = equity + net_income - dividends
        current_assets = ending_cash + ending_noncash_assets
        total_assets = current_assets + ending_noncurrent_assets
        total_liabilities_equity = ending_current_liabilities + ending_debt + other_liabilities + ending_equity
        balance_difference = total_assets - total_liabilities_equity
        year_warnings = []
        if ending_cash < 0:
            year_warnings.append("Forecast ending cash is negative.")
        if ending_noncurrent_assets < 0:
            year_warnings.append("Forecast non-current assets are negative.")
        if inventory < 0:
            year_warnings.append("Forecast inventory is negative.")
        balance_status = "PASS" if balance_difference == 0 else "WARNING"
        if balance_difference != 0:
            year_warnings.append("Balance sheet does not reconcile; inspect opening balance and assumptions.")

        def value(v: Decimal, ident: str, formula: str, inputs: list[str]) -> dict[str, Any]:
            return _model_value(v, ident, formula, inputs, unit, list(year_warnings) if year_warnings else None)

        output.append({
            "period": f"FY{base_year + offset}",
            "scenario": scenario,
            "assumptions": {"revenue_growth_pct": str(growth), "operating_expense_pct": str(opex_ratio)},
            "income_statement": {
                "revenue": value(period_revenue, "forecast.revenue", "prior_revenue × (1 + growth)", ["prior_period.revenue", "assumptions.revenue_growth_pct"]),
                "gross_profit": value(gross_profit, "forecast.gross_profit", "revenue × gross_margin", ["forecast.revenue", "assumptions.gross_margin_pct"]),
                "operating_expense": value(operating_expense, "forecast.operating_expense", "revenue × operating_expense_ratio", ["forecast.revenue", "assumptions.operating_expense_pct"]),
                "operating_profit": value(operating_profit, "forecast.operating_profit", "gross_profit - operating_expense", ["forecast.gross_profit", "forecast.operating_expense"]),
                "depreciation": value(depreciation, "forecast.depreciation", "revenue × depreciation_ratio", ["forecast.revenue", "assumptions.depreciation_pct"]),
                "ebitda": value(ebitda, "forecast.ebitda", "operating_profit + depreciation", ["forecast.operating_profit", "forecast.depreciation"]),
                "interest_expense": value(interest, "forecast.interest_expense", "average_debt × interest_rate", ["prior_period.total_debt", "forecast.total_debt", "assumptions.interest_rate_pct"]),
                "pre_tax_profit": value(pretax, "forecast.pre_tax_profit", "operating_profit - interest_expense", ["forecast.operating_profit", "forecast.interest_expense"]),
                "tax_expense": value(tax, "forecast.tax_expense", "max(pre_tax_profit, 0) × tax_rate", ["forecast.pre_tax_profit", "assumptions.tax_rate_pct"]),
                "net_income": value(net_income, "forecast.net_income", "pre_tax_profit - tax_expense", ["forecast.pre_tax_profit", "forecast.tax_expense"]),
            },
            "cash_flow_statement": {
                "operating_cash_flow": value(cfo, "forecast.operating_cash_flow", "net_income + depreciation - change_in_net_working_capital", ["forecast.net_income", "forecast.depreciation", "forecast.net_working_capital"]),
                "capital_expenditure": value(cfi, "forecast.capital_expenditure", "-revenue × capex_ratio", ["forecast.revenue", "assumptions.capex_pct"]),
                "financing_cash_flow": value(financing, "forecast.financing_cash_flow", "debt_change - dividends", ["assumptions.annual_debt_change", "forecast.dividends"]),
                "dividends": value(-dividends, "forecast.dividends", "-max(net_income, 0) × payout_ratio", ["forecast.net_income", "assumptions.dividend_payout_pct"]),
                "net_change_in_cash": value(net_change_cash, "forecast.net_change_in_cash", "operating_cash_flow + investing_cash_flow + financing_cash_flow", ["forecast.operating_cash_flow", "forecast.capital_expenditure", "forecast.financing_cash_flow"]),
            },
            "balance_sheet": {
                "cash_and_equivalents": value(ending_cash, "forecast.cash", "prior_cash + net_change_in_cash", ["prior_period.cash_and_equivalents", "forecast.net_change_in_cash"]),
                "noncash_current_assets": value(ending_noncash_assets, "forecast.noncash_current_assets", "revenue × noncash_current_assets_ratio", ["forecast.revenue", "assumptions.noncash_current_assets_pct"]),
                "current_assets": value(current_assets, "forecast.current_assets", "cash + noncash_current_assets", ["forecast.cash_and_equivalents", "forecast.noncash_current_assets"]),
                "inventory": value(inventory, "forecast.inventory", "revenue × inventory_ratio", ["forecast.revenue", "assumptions.inventory_pct"]),
                "current_liabilities": value(ending_current_liabilities, "forecast.current_liabilities", "revenue × current_liabilities_ratio", ["forecast.revenue", "assumptions.current_liabilities_pct"]),
                "total_debt": value(ending_debt, "forecast.total_debt", "prior_debt + annual_debt_change", ["prior_period.total_debt", "assumptions.annual_debt_change"]),
                "other_liabilities": value(other_liabilities, "forecast.other_liabilities", "held constant at base period", ["base_period.other_liabilities"]),
                "noncurrent_assets": value(ending_noncurrent_assets, "forecast.noncurrent_assets", "prior_noncurrent_assets + capex - depreciation", ["prior_period.noncurrent_assets", "forecast.capital_expenditure", "forecast.depreciation"]),
                "total_equity": value(ending_equity, "forecast.total_equity", "prior_equity + net_income - dividends", ["prior_period.total_equity", "forecast.net_income", "forecast.dividends"]),
                "total_assets": value(total_assets, "forecast.total_assets", "current_assets + noncurrent_assets", ["forecast.current_assets", "forecast.noncurrent_assets"]),
                "total_liabilities_and_equity": value(total_liabilities_equity, "forecast.total_liabilities_and_equity", "current_liabilities + debt + other_liabilities + equity", ["forecast.current_liabilities", "forecast.total_debt", "forecast.other_liabilities", "forecast.total_equity"]),
                "balance_difference": value(balance_difference, "forecast.balance_difference", "total_assets - total_liabilities_and_equity", ["forecast.total_assets", "forecast.total_liabilities_and_equity"]),
                "balance_status": balance_status,
            },
            "validation_state": "WARNING" if year_warnings else "CALCULATED",
            "warnings": year_warnings,
        })
        revenue = period_revenue
        cash = ending_cash
        noncash_assets = ending_noncash_assets
        current_liabilities = ending_current_liabilities
        noncurrent_assets = ending_noncurrent_assets
        debt = ending_debt
        equity = ending_equity
        previous_nwc = ending_noncash_assets - ending_current_liabilities
    return output


def build_scenarios(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise FinancialInputError("Request body must be a JSON object.")
    statement = payload.get("statement")
    analysis, base = _require_base(statement)
    defaults = default_assumptions(statement)
    assumptions, years = _assumptions(payload.get("assumptions"), defaults, payload.get("forecast_years", 5))
    period = analysis["metadata"]["period"]
    base_year = int(period.removeprefix("FY"))
    assets = base["current_assets"] + base["noncurrent_assets"]
    liabilities_equity = base["current_liabilities"] + base["total_debt"] + base["other_liabilities"] + base["total_equity"]
    balance_difference = assets - liabilities_equity
    tolerance = max(Decimal("0.01"), abs(assets) * Decimal("0.005"))
    if abs(balance_difference) > tolerance:
        raise FinancialInputError(
            f"Opening balance sheet does not reconcile: assets minus liabilities/equity is {balance_difference} {analysis['metadata']['currency']} {analysis['metadata']['unit']}; allowed tolerance is {tolerance}."
        )
    base_warnings = [] if balance_difference == 0 else ["Opening balance sheet is within the 0.5% tolerance but does not exactly reconcile."]
    cases = {
        "downside": (-assumptions["downside_growth_delta_pct"], assumptions["operating_margin_delta_pp"]),
        "base": (Decimal("0"), Decimal("0")),
        "upside": (assumptions["upside_growth_delta_pct"], -assumptions["operating_margin_delta_pp"]),
    }
    scenarios = {}
    for name, (growth_delta, margin_delta) in cases.items():
        scenarios[name] = _projection(
            base, assumptions, years, growth_delta, margin_delta, name,
            f"{analysis['metadata']['currency']} {analysis['metadata']['unit']}", base_year,
        )
    return {
        "model_version": MODEL_VERSION,
        "formula_catalog_version": analysis["metrics"].get("gross_margin_percent", {}).get("formula_version", "2.0.0"),
        "base_period": period,
        "currency": analysis["metadata"]["currency"],
        "unit": analysis["metadata"]["unit"],
        "source_kind": analysis["metadata"]["source_kind"],
        "source_url": analysis["metadata"]["source_url"],
        "review_status": "illustrative_only" if analysis["metadata"]["source_kind"] == "ILLUSTRATIVE_SAMPLE" else "forecast_unreviewed",
        "human_review_required_before_publication": analysis["metadata"]["source_kind"] == "PUBLIC_SOURCE",
        "opening_balance_check": {
            "total_assets": str(assets),
            "total_liabilities_and_equity": str(liabilities_equity),
            "difference": str(balance_difference),
            "tolerance": str(tolerance),
            "status": "PASS" if balance_difference == 0 else "WARNING",
            "warnings": base_warnings,
        },
        "assumption_origins": defaults["origins"],
        "assumptions": {key: str(value) for key, value in assumptions.items()},
        "scenarios": scenarios,
        "limitations": [
            "Projections are deterministic illustrations of the entered assumptions, not predictions or investment advice.",
            "Other liabilities are held constant; working-capital and fixed-asset schedules use the displayed revenue drivers.",
            "Tax is floored at zero when pre-tax profit is negative; tax benefits are not modelled.",
            "Every forecast remains unreviewed and must not be presented as a reported fact.",
        ],
    }
