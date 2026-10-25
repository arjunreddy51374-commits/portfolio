"""Integration tests for FIP identity, evidence, calculation, and release workflows."""
from __future__ import annotations

from importlib.util import find_spec
from io import BytesIO
from decimal import Decimal

from flask import Flask

from fip.core import CORPORATE_METRICS
from fip.routes import create_blueprint


ADMIN = {"X-FIP-Token": "fip-admin-test-secret"}
REVIEWER = {"X-FIP-Token": "fip-reviewer-test-secret"}


def make_app(database_path, admin_token="fip-admin-test-secret", reviewer_token="fip-reviewer-test-secret"):
    app = Flask("fip_test")
    app.config.update(TESTING=True, FIP_ADMIN_TOKEN=admin_token, FIP_REVIEWER_TOKEN=reviewer_token)
    app.register_blueprint(create_blueprint(database_path))
    return app


def register_source(client):
    response = client.post("/api/fip/sources", headers=ADMIN, json={
        "title": "Example audited annual report", "publisher": "Example issuer",
        "category": "corporate_filing", "jurisdiction_code": "IN",
        "url": "https://example.org/reports/annual-report.pdf", "publication_date": "2025-05-01",
        "retrieval_method": "Manual download", "expected_frequency": "Annual",
        "owner": "FIP data team", "access_requirements": "Public web access",
        "technical_limits": "PDF file", "license_name": "Example public terms",
        "permitted_use": "public_display", "retention_rule": "Retain source link",
        "failure_policy": "Mark stale and route for review",
    })
    assert response.status_code == 200, response.get_json()
    source = response.get_json()
    approved = client.post(f"/api/fip/sources/{source['id']}/approve", headers=REVIEWER,
                           json={"reason": "Reviewed source authority and reuse terms."})
    assert approved.status_code == 200, approved.get_json()
    return source["id"]


def register_verified_entity(client):
    response = client.post("/api/fip/entities", headers=ADMIN, json={
        "entity_type": "company", "canonical_name": "Example Industries Limited",
        "legal_name": "Example Industries Limited", "jurisdiction_code": "IN",
        "external_id": "REG-12345",
    })
    assert response.status_code == 200, response.get_json()
    entity = response.get_json()
    verified = client.post(f"/api/fip/entities/{entity['id']}/verify", headers=REVIEWER,
                           json={"reason": "Matched to the official company registry."})
    assert verified.status_code == 200, verified.get_json()
    return entity["id"]


def publish_observation(client, *, entity_id, source_id, metric_id, value, period_id="FY2025",
                        period_start="2024-04-01", period_end="2025-03-31", evidence_reference=None):
    label = CORPORATE_METRICS[metric_id]
    response = client.post("/api/fip/observations", headers=ADMIN, json={
        "entity_id": entity_id, "source_id": source_id, "domain": "corporate", "metric_id": metric_id,
        "reported_label": label, "value": str(value), "currency": "INR", "unit": "ones",
        "period_id": period_id, "period_start": period_start, "period_end": period_end,
        "period_type": "annual", "reporting_basis": "Ind AS consolidated",
        "framework_id": "IND_AS", "framework_version": "2024",
        "freshness_status": "CURRENT", "evidence_reference": evidence_reference or f"Table 1, {label}",
    })
    assert response.status_code == 200, response.get_json()
    record = response.get_json()
    validation = client.post(f"/api/fip/observations/{record['id']}/validate", headers=REVIEWER,
                             json={"reason": "Checked the recorded fields against source evidence."})
    assert validation.status_code == 200, validation.get_json()
    assert validation.get_json()["status"] == "VALIDATED"
    approved = client.post(f"/api/fip/observations/{record['id']}/approve", headers=REVIEWER,
                           json={"reason": "Identity, rights, freshness, and evidence checks passed."})
    assert approved.status_code == 200, approved.get_json()
    published = client.post(f"/api/fip/observations/{record['id']}/publish", headers=REVIEWER,
                            json={"reason": "Approved for the publication dataset."})
    assert published.status_code == 200, published.get_json()
    return record["id"]


def test_fip_calculations_reconciliation_release_and_delivery(tmp_path):
    client = make_app(tmp_path / "fip.sqlite3").test_client()
    source_id = register_source(client)
    entity_id = register_verified_entity(client)

    values = {
        "revenue": "1000", "gross_profit": "600", "operating_profit": "150", "net_income": "100",
        "current_assets": "300", "current_liabilities": "150", "inventory": "50", "total_debt": "200",
        "total_equity": "400", "total_assets": "500", "total_liabilities": "100", "operating_cash_flow": "150",
        "investing_cash_flow": "-30", "financing_cash_flow": "-15", "opening_cash": "100", "closing_cash": "205",
        "fx_effect_cash": "0", "other_cash_reconciliation": "0", "capital_expenditure": "50", "ebit": "150",
        "interest_expense": "30",
    }
    for metric_id, value in values.items():
        publish_observation(client, entity_id=entity_id, source_id=source_id, metric_id=metric_id, value=value)
    publish_observation(client, entity_id=entity_id, source_id=source_id, metric_id="revenue", value="800",
                        period_id="FY2024", period_start="2023-04-01", period_end="2024-03-31",
                        evidence_reference="Table 1, prior-year revenue")

    calculated = client.post("/api/fip/calculations", headers=ADMIN, json={
        "entity_id": entity_id, "domain": "corporate", "period_id": "FY2025", "prior_period_id": "FY2024",
    })
    assert calculated.status_code == 200, calculated.get_json()
    run = calculated.get_json()
    metrics = run["metrics"]
    assert Decimal(metrics["revenue_growth_percent"]["value_decimal"]) == Decimal("25")
    assert Decimal(metrics["revenue_change"]["value_decimal"]) == Decimal("200")
    assert Decimal(metrics["current_ratio"]["value_decimal"]) == Decimal("2")
    assert Decimal(metrics["return_on_assets_percent"]["value_decimal"]) == Decimal("20")
    assert Decimal(metrics["free_cash_flow"]["value_decimal"]) == Decimal("100")
    assert len(run["input_observations"]) >= 10

    reconciliation = client.post("/api/fip/reconciliation", headers=ADMIN, json={
        "entity_id": entity_id, "domain": "corporate", "period_id": "FY2025",
    })
    assert reconciliation.status_code == 200, reconciliation.get_json()
    assert reconciliation.get_json()["overall_status"] == "PASS"

    published_run = client.post(f"/api/fip/calculations/{run['id']}/publish", headers=REVIEWER,
                                json={"reason": "Reviewed formulas, input periods, and lineage."})
    assert published_run.status_code == 200, published_run.get_json()
    release_response = client.post("/api/fip/releases", headers=ADMIN, json={"notes": "FY2025 reference release."})
    assert release_response.status_code == 200, release_response.get_json()
    release = release_response.get_json()
    approved_release = client.post(f"/api/fip/releases/{release['id']}/approve", headers=REVIEWER,
                                   json={"reason": "Reviewed release coverage and publication evidence."})
    assert approved_release.status_code == 200, approved_release.get_json()

    public_data = client.get("/api/fip/publications").get_json()
    assert public_data["release"]["version"] == release["version"]
    assert len(public_data["observations"]) == len(values) + 1
    assert len(public_data["calculations"]) == 1
    powerbi = client.get("/api/fip/delivery/powerbi")
    assert powerbi.status_code == 200
    assert powerbi.get_json()["release"]["formula_catalog_version"] == "1.1.0"
    csv_response = client.get("/api/fip/delivery/excel.csv")
    assert csv_response.status_code == 200
    assert b"input_observations_json" in csv_response.data
    assert b"revenue_growth_percent" in csv_response.data

    workbook_response = client.get("/api/fip/delivery/excel.xlsx")
    if find_spec("openpyxl"):
        assert workbook_response.status_code == 200
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(workbook_response.data), read_only=True, data_only=False)
        assert workbook.sheetnames == ["Dashboard", "Controls", "Corporate", "Government", "Statements",
                                       "Engine", "Raw Data", "Standardized Data", "Source Register", "FX Rates", "Audit"]
        engine_values = {row[4].value: row[6].value for row in workbook["Engine"].iter_rows(min_row=2)}
        assert Decimal(engine_values["revenue_growth_percent"]) == Decimal("25")
    else:
        assert workbook_response.status_code == 503


def test_fip_requires_distinct_admin_and_reviewer_tokens(tmp_path):
    client = make_app(tmp_path / "fip.sqlite3", "shared-secret", "shared-secret").test_client()
    response = client.get("/api/fip/audit", headers={"X-FIP-Token": "shared-secret"})
    assert response.status_code == 503
    assert "must be different" in response.get_json()["error"]


def test_fip_rejects_anonymous_admin_actions(tmp_path):
    client = make_app(tmp_path / "fip.sqlite3").test_client()
    response = client.post("/api/fip/entities", json={
        "entity_type": "company", "canonical_name": "No token", "jurisdiction_code": "IN",
    })
    assert response.status_code == 401
