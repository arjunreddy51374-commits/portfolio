"""Regression coverage for multi-period analysis, modelling, and imports."""
from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook

from finance.calculations import FinancialInputError, analyze_financials
from finance.history import analyze_history
from finance.importing import analyze_statement_upload, parse_statement_upload
from finance.modeling import build_scenarios, default_assumptions


def statement(year: int, revenue: int = 1000, unit: str = "millions", *, source_kind: str = "PUBLIC_SOURCE") -> dict:
    amounts = {
        "revenue": str(revenue),
        "gross_profit": str(revenue * 42 // 100),
        "operating_profit": str(revenue * 15 // 100),
        "net_income": str(revenue * 92 // 1000),
        "cost_of_revenue": str(revenue * 58 // 100),
        "depreciation": str(revenue * 35 // 1000),
        "interest_expense": str(revenue * 2 // 100),
        "tax_expense": str(revenue * 38 // 1000),
        "current_assets": str(revenue * 60 // 100),
        "current_liabilities": str(revenue * 30 // 100),
        "inventory": str(revenue * 12 // 100),
        "total_debt": str(revenue * 24 // 100),
        "total_equity": str(revenue * 40 // 100),
        "cash_and_equivalents": str(revenue * 10 // 100),
        "noncurrent_assets": str(revenue * 50 // 100),
        "other_liabilities": str(revenue * 16 // 100),
        "operating_cash_flow": str(revenue * 11 // 100),
        "capital_expenditure": str(revenue * 65 // 1000),
        "average_total_assets": str(revenue * 105 // 100),
        "average_total_equity": str(revenue * 38 // 100),
        "average_inventory": str(revenue * 11 // 100),
        "average_receivables": str(revenue * 14 // 100),
        "average_payables": str(revenue * 12 // 100),
    }
    payload = {
        "currency": "INR",
        "unit": unit,
        "period": f"FY{year}",
        "source_kind": source_kind,
        "amounts": amounts,
    }
    if source_kind == "PUBLIC_SOURCE":
        payload["source_url"] = f"https://example.com/reports/{year}.pdf"
    return payload


def csv_history(rows: list[tuple[str, int]]) -> bytes:
    lines = ["company_name,period,currency,unit,source_url,revenue,gross_profit,operating_profit,net_income,current_assets,current_liabilities,inventory,total_debt,total_equity,cash_and_equivalents,noncurrent_assets,other_liabilities"]
    for period, revenue in rows:
        values = [
            "Example Ltd", period, "INR", "millions", f"https://example.com/{period}.pdf",
            str(revenue), str(revenue * 42 // 100), str(revenue * 15 // 100), str(revenue * 92 // 1000),
            str(revenue * 60 // 100), str(revenue * 30 // 100), str(revenue * 12 // 100),
            str(revenue * 24 // 100), str(revenue * 40 // 100), str(revenue * 10 // 100),
            str(revenue * 50 // 100), str(revenue * 16 // 100),
        ]
        lines.append(",".join(values))
    return ("\n".join(lines) + "\n").encode()


def test_extended_metrics_include_lineage_and_explicit_missing_states():
    result = analyze_financials(statement(2025))
    assert Decimal(result["metrics"]["free_cash_flow"]["value_decimal"]) == Decimal("45000000.00")
    assert result["metrics"]["free_cash_flow"]["formula_version"] == "2.0.0"
    assert result["metrics"]["free_cash_flow"]["input_references"] == ["operating_cash_flow", "capital_expenditure"]
    assert result["metric_states"]["receivables_days"] == "CALCULATED"
    assert result["metric_states"]["cash_conversion_cycle_days"] == "CALCULATED"
    assert result["metric_states"]["net_debt"] == "CALCULATED"

    partial = statement(2025)
    partial["amounts"] = {"revenue": "100", "gross_profit": "40"}
    partial_result = analyze_financials(partial)
    assert partial_result["metric_states"]["operating_margin_percent"] == "MISSING"


@pytest.mark.parametrize("field", ["revenue", "prior_revenue", "capital_expenditure", "interest_expense", "cash_and_equivalents"])
def test_invalid_negative_financial_inputs_are_rejected(field):
    payload = statement(2025)
    payload["amounts"][field] = "-1"
    with pytest.raises(FinancialInputError, match="negative|non-negative"):
        analyze_financials(payload)


def test_explicit_not_applicable_is_distinct_from_missing():
    payload = statement(2025)
    payload["amounts"].pop("capital_expenditure")
    payload["not_applicable_fields"] = ["capital_expenditure"]
    result = analyze_financials(payload)
    assert result["metric_states"]["free_cash_flow"] == "NOT_APPLICABLE"


def test_negative_equity_does_not_produce_a_misleading_debt_to_equity_ratio():
    payload = statement(2025)
    payload["amounts"]["total_equity"] = "-10"
    result = analyze_financials(payload)
    assert "debt_to_equity_ratio" not in result["metrics"]
    assert result["metric_states"]["debt_to_equity_ratio"] == "INVALID"


def test_history_sorts_normalizes_and_calculates_expected_cagr():
    fy2024 = statement(2024, 870, unit="thousands")
    fy2024["amounts"] = {name: str(Decimal(value) * 1000) for name, value in fy2024["amounts"].items()}
    history = analyze_history({
        "company_name": "Example Ltd",
        "periods": [statement(2025, 1000), statement(2023, 720), fy2024],
    })
    assert [item["metadata"]["period"] for item in history["periods"]] == ["FY2023", "FY2024", "FY2025"]
    assert Decimal(history["periods"][1]["normalized_inputs"]["revenue"]) == Decimal("870000000")
    assert Decimal(history["revenue_cagr"]["value_decimal"]).quantize(Decimal("0.01")) == Decimal("17.85")
    assert Decimal(history["metric_series"]["revenue_growth_percent"][0]["value_decimal"]).quantize(Decimal("0.01")) == Decimal("20.83")
    assert any("Input units varied" in warning for warning in history["warnings"])
    assert history["validation_state"] == "UNREVIEWED"


def test_history_rejects_mixed_currency_and_duplicate_periods():
    with pytest.raises(FinancialInputError, match="same currency"):
        analyze_history({"company_name": "Example", "periods": [statement(2024), {**statement(2025), "currency": "USD"}]})
    with pytest.raises(FinancialInputError, match="Duplicate reporting periods"):
        analyze_history({"company_name": "Example", "periods": [statement(2025), statement(2025)]})


def test_history_does_not_invent_growth_across_a_missing_year():
    history = analyze_history({"company_name": "Example", "periods": [statement(2023, 720), statement(2025, 1000)]})
    assert "revenue_growth_percent" not in history["metric_series"]
    assert any("periods are missing" in warning for warning in history["warnings"])
    assert Decimal(history["revenue_cagr"]["value_decimal"]).quantize(Decimal("0.01")) == Decimal("17.85")


def test_three_scenarios_reconcile_and_expose_formula_lineage():
    base = statement(2025)
    defaults = default_assumptions(base)
    result = build_scenarios({"statement": base, "assumptions": defaults["values"], "forecast_years": 3})
    assert result["opening_balance_check"]["status"] == "PASS"
    assert result["review_status"] == "forecast_unreviewed"
    assert result["human_review_required_before_publication"] is True
    for scenario in ("downside", "base", "upside"):
        years = result["scenarios"][scenario]
        assert len(years) == 3
        for forecast in years:
            assert Decimal(forecast["balance_sheet"]["balance_difference"]["value_decimal"]) == 0
            assert forecast["balance_sheet"]["balance_status"] == "PASS"
            assert forecast["income_statement"]["revenue"]["input_references"] == ["prior_period.revenue", "assumptions.revenue_growth_pct"]
    assert Decimal(result["scenarios"]["downside"][0]["income_statement"]["revenue"]["value_decimal"]) < Decimal(result["scenarios"]["base"][0]["income_statement"]["revenue"]["value_decimal"])
    assert Decimal(result["scenarios"]["base"][0]["income_statement"]["revenue"]["value_decimal"]) < Decimal(result["scenarios"]["upside"][0]["income_statement"]["revenue"]["value_decimal"])


def test_model_rejects_unbalanced_or_quarterly_opening_statements():
    unbalanced = statement(2025)
    unbalanced["amounts"]["total_equity"] = "450"
    with pytest.raises(FinancialInputError, match="does not reconcile"):
        build_scenarios({"statement": unbalanced})
    quarterly = statement(2025)
    quarterly["period"] = "2025-Q4"
    with pytest.raises(FinancialInputError, match="Annual scenario modelling"):
        default_assumptions(quarterly)


def test_model_warns_on_negative_forecast_cash_instead_of_hiding_it():
    base = statement(2025)
    assumptions = default_assumptions(base)["values"]
    assumptions["capex_pct"] = "200"
    result = build_scenarios({"statement": base, "assumptions": assumptions, "forecast_years": 1})
    forecast = result["scenarios"]["base"][0]
    assert Decimal(forecast["balance_sheet"]["cash_and_equivalents"]["value_decimal"]) < 0
    assert "Forecast ending cash is negative." in forecast["warnings"]


def test_csv_import_and_peer_comparison_are_stateless():
    content = csv_history([("FY2024", 900), ("FY2025", 1000)])
    parsed = parse_statement_upload("history.csv", content)
    assert len(parsed) == 1
    result = analyze_statement_upload("history.csv", content)
    assert result["row_count"] == 2
    assert result["stored"] is False
    assert result["peer_comparison"] is None
    assert result["companies"][0]["revenue_cagr"] is not None


def test_csv_import_rejects_ragged_rows_and_incompatible_units():
    content = csv_history([("FY2024", 900)]).decode().replace("\n", "\n")
    header, row, *_ = content.splitlines()
    with pytest.raises(FinancialInputError, match="does not match"):
        parse_statement_upload("history.csv", f"{header}\n{row},extra\n".encode())

    mixed_units = csv_history([("FY2024", 900), ("FY2025", 1000)]).decode().replace("FY2025,INR,millions", "FY2025,USD,millions")
    with pytest.raises(FinancialInputError, match="same currency"):
        analyze_statement_upload("history.csv", mixed_units.encode())


def test_xlsx_import_rejects_formula_cells():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["company_name", "period", "currency", "unit", "source_url", "revenue"])
    sheet.append(["Example", "FY2024", "INR", "millions", "https://example.com/report", "=1+1"])
    stream = BytesIO()
    workbook.save(stream)
    with pytest.raises(FinancialInputError, match="Formula cells"):
        parse_statement_upload("history.xlsx", stream.getvalue())
