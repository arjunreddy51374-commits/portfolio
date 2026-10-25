from decimal import Decimal

import pytest

from finance.calculations import FinancialInputError, analyze_financials


@pytest.fixture()
def statement():
    return {
        "currency": "INR",
        "unit": "millions",
        "period": "FY2025",
        "prior_period": "FY2024",
        "source_url": "https://example.com/reports/fy2025.pdf",
        "publication_date": "2025-05-01",
        "retrieved_at": "2025-05-03T12:00:00+05:30",
        "amounts": {
            "revenue": "200",
            "prior_revenue": "160",
            "gross_profit": "120",
            "operating_profit": "50",
            "net_income": "30",
            "current_assets": "150",
            "current_liabilities": "100",
            "inventory": "25",
            "total_debt": "100",
            "total_equity": "200",
            "operating_cash_flow": "45",
            "investing_cash_flow": "-20",
            "financing_cash_flow": "-5",
        },
    }


def test_financial_metrics_use_decimal_values_and_trace_inputs(statement):
    result = analyze_financials(statement)
    metrics = result["metrics"]

    assert Decimal(metrics["revenue_growth_percent"]["value_decimal"]) == Decimal("25")
    assert Decimal(metrics["gross_margin_percent"]["value_decimal"]) == Decimal("60")
    assert Decimal(metrics["operating_margin_percent"]["value_decimal"]) == Decimal("25")
    assert Decimal(metrics["net_margin_percent"]["value_decimal"]) == Decimal("15")
    assert Decimal(metrics["working_capital"]["value_decimal"]) == Decimal("50000000")
    assert Decimal(metrics["current_ratio"]["value_decimal"]) == Decimal("1.5")
    assert Decimal(metrics["quick_ratio"]["value_decimal"]) == Decimal("1.25")
    assert Decimal(metrics["debt_to_equity_ratio"]["value_decimal"]) == Decimal("0.5")
    assert Decimal(metrics["operating_cash_flow_to_net_income_ratio"]["value_decimal"]) == Decimal("1.5")
    assert Decimal(metrics["cash_flow_category_subtotal"]["value_decimal"]) == Decimal("20000000")
    assert metrics["gross_margin_percent"]["inputs"] == {"gross_profit": "120000000", "revenue": "200000000"}
    assert result["source_inputs"]["revenue"] == "200"
    assert result["normalized_inputs"]["revenue"] == "200000000"
    assert result["metadata"]["review_status"] == "unreviewed"
    assert result["metadata"]["source_url"] == statement["source_url"]
    assert result["calculation_policy"]["persistence"].endswith("does not store submissions.")


def test_zero_revenue_denominators_are_not_silently_calculated(statement):
    statement["amounts"].update({"revenue": "0", "prior_revenue": "0", "current_liabilities": "0", "total_equity": "0", "net_income": "0"})
    result = analyze_financials(statement)
    assert "gross_margin_percent" not in result["metrics"]
    assert "revenue_growth_percent" not in result["metrics"]
    assert "current_ratio" not in result["metrics"]
    assert "debt_to_equity_ratio" not in result["metrics"]
    assert "not defined" in " ".join(result["limitations"])


def test_missing_amounts_yield_only_available_metrics(statement):
    statement["amounts"] = {"revenue": "10", "prior_revenue": "8"}
    result = analyze_financials(statement)
    assert set(result["metrics"]) == {"revenue_growth_percent"}
    assert any("gross_profit is missing" in note for note in result["limitations"])


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [
        ("currency", "IN", "three-letter"),
        ("period", "future-ish", "period must"),
        ("unit", "crores", "unit must"),
        ("source_url", "http://example.com/filing", "HTTPS"),
        ("publication_date", "not-a-date", "YYYY-MM-DD"),
    ],
)
def test_metadata_validation(statement, key, value, error):
    statement[key] = value
    with pytest.raises(FinancialInputError, match=error):
        analyze_financials(statement)


@pytest.mark.parametrize("prior_period", ["FY2025", "2024-Q1", "FY2026", "invalid"])
def test_revenue_growth_rejects_incompatible_or_nonprior_period(statement, prior_period):
    statement["prior_period"] = prior_period
    with pytest.raises(FinancialInputError, match="prior_period|period and"):
        analyze_financials(statement)


def test_large_decimal_display_is_rounded_without_overflow(statement):
    statement["unit"] = "billions"
    statement["amounts"] = {"revenue": "999999999999999999.99", "prior_revenue": "1"}
    result = analyze_financials(statement)
    assert result["metrics"]["revenue_growth_percent"]["value_display"].endswith(".00")


def test_decimal_amounts_reject_binary_floats_and_nonfinite_values(statement):
    statement["amounts"]["revenue"] = 0.1
    with pytest.raises(FinancialInputError, match="finite decimal amount"):
        analyze_financials(statement)
    statement["amounts"]["revenue"] = "NaN"
    with pytest.raises(FinancialInputError, match="finite and within"):
        analyze_financials(statement)


def test_unknown_fields_and_empty_amounts_are_rejected(statement):
    statement["amounts"] = {"revenue": "1", "invented_metric": "2"}
    with pytest.raises(FinancialInputError, match="Unknown financial fields"):
        analyze_financials(statement)
    statement["amounts"] = {}
    with pytest.raises(FinancialInputError, match="at least one"):
        analyze_financials(statement)
