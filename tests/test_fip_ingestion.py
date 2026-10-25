"""Integration tests for persistent, review-gated FIP ingestion modes."""
from __future__ import annotations

from flask import Flask

from fip import core, ingestion
from fip.routes import create_blueprint


ADMIN = {"X-FIP-Token": "fip-ingestion-admin-secret"}
REVIEWER = {"X-FIP-Token": "fip-ingestion-reviewer-secret"}


def make_client(database_path):
    app = Flask("fip_ingestion_test")
    app.config.update(TESTING=True, FIP_ADMIN_TOKEN=ADMIN["X-FIP-Token"],
                      FIP_REVIEWER_TOKEN=REVIEWER["X-FIP-Token"])
    app.register_blueprint(create_blueprint(database_path))
    return app.test_client()


def register_source(client):
    result = client.post("/api/fip/sources", headers=ADMIN, json={
        "title": "Example financial data", "publisher": "Example publisher",
        "category": "official_dataset", "jurisdiction_code": "IN",
        "url": "https://data.example.org/financial.csv", "publication_date": "2025-05-01",
        "retrieval_method": "Approved HTTPS connector", "expected_frequency": "Annual",
        "owner": "FIP data team", "access_requirements": "Public web access",
        "technical_limits": "CSV or JSON, max 5 MiB", "license_name": "Public terms",
        "permitted_use": "public_display", "retention_rule": "Retain source link",
        "failure_policy": "Route retrieval failures to review",
    })
    assert result.status_code == 200, result.get_json()
    source_id = result.get_json()["id"]
    approved = client.post(f"/api/fip/sources/{source_id}/approve", headers=REVIEWER,
                           json={"reason": "Publisher authority and public-display terms reviewed."})
    assert approved.status_code == 200, approved.get_json()
    return source_id


def register_entity(client):
    result = client.post("/api/fip/entities", headers=ADMIN, json={
        "entity_type": "company", "canonical_name": "Example Company Limited",
        "legal_name": "Example Company Limited", "jurisdiction_code": "IN",
    })
    assert result.status_code == 200, result.get_json()
    entity_id = result.get_json()["id"]
    verified = client.post(f"/api/fip/entities/{entity_id}/verify", headers=REVIEWER,
                           json={"reason": "Matched to the official company registry."})
    assert verified.status_code == 200, verified.get_json()
    return entity_id


def connector_config(entity_id):
    return {
        "field_map": {
            "entity_id": "entity_id", "domain": "domain", "reported_label": "reported_label",
            "value": "value", "period_id": "period_id",
            "evidence_reference": "evidence_reference",
        },
        "defaults": {
            "currency": "INR", "unit": "ones", "period_start": "2024-04-01",
            "period_end": "2025-03-31", "period_type": "annual",
            "reporting_basis": "Ind AS consolidated", "framework_id": "IND_AS",
            "framework_version": "2024", "freshness_status": "CURRENT",
        },
        "idempotency_field": "row_id",
    }


def configure_connector(client, source_id, entity_id, mode):
    config = connector_config(entity_id)
    config["defaults"]["entity_id"] = entity_id
    config["field_map"].pop("entity_id")
    result = client.put(f"/api/fip/sources/{source_id}/ingestion-settings", headers=ADMIN, json={
        "mode": mode, "connector_format": "csv", "connector_config": config,
        "schedule_interval_minutes": None,
    })
    assert result.status_code == 200, result.get_json()
    approved = client.post(f"/api/fip/sources/{source_id}/ingestion-settings/approve", headers=REVIEWER,
                           json={"reason": "Connector mappings, limits, and source-use conditions reviewed."})
    assert approved.status_code == 200, approved.get_json()


def manual_observation(source_id, entity_id, *, evidence="Table 1, revenue", value="1000"):
    return {
        "source_id": source_id, "entity_id": entity_id, "domain": "corporate",
        "metric_id": "revenue", "reported_label": "Revenue", "value": value,
        "currency": "INR", "unit": "ones", "period_id": "FY2025",
        "period_start": "2024-04-01", "period_end": "2025-03-31", "period_type": "annual",
        "reporting_basis": "Ind AS consolidated", "framework_id": "IND_AS",
        "framework_version": "2024", "freshness_status": "CURRENT",
        "evidence_reference": evidence,
    }


def test_modes_persist_and_privileged_settings_require_admin(tmp_path):
    client = make_client(tmp_path / "fip.sqlite3")
    page = client.get("/fip")
    assert page.status_code == 200
    assert b"Data Ingestion Settings" in page.data
    assert b"Both / Hybrid" in page.data
    public_workspace = page.data.split(b'<details id="staff-details" class="staff-details"', 1)[1].split(b'>', 1)[0]
    assert b"open" not in public_workspace
    staff_page = client.get("/fip/staff")
    assert staff_page.status_code == 200
    assert b'<details id="staff-details" class="staff-details" open>' in staff_page.data
    guide = client.get("/fip/guide")
    assert guide.status_code == 200
    assert b"New jobs default to" in guide.data
    settings = client.get("/api/fip/ingestion/settings", headers=ADMIN)
    assert settings.status_code == 200
    assert settings.get_json()["global_mode"] == "MANUAL"
    assert set(settings.get_json()["modes"]) == {"MANUAL", "AUTOMATIC", "HYBRID"}

    assert client.get("/api/fip/ingestion/settings").status_code == 401
    assert client.put("/api/fip/ingestion/settings", headers=REVIEWER, json={"mode": "HYBRID"}).status_code == 401
    assert client.put("/api/fip/ingestion/settings", headers=ADMIN, json={"mode": "SCHEDULED"}).status_code == 400
    updated = client.put("/api/fip/ingestion/settings", headers=ADMIN, json={"mode": "HYBRID"})
    assert updated.status_code == 200, updated.get_json()
    assert client.get("/api/fip/ingestion/settings", headers=ADMIN).get_json()["global_mode"] == "HYBRID"

    source_id = register_source(client)
    entity_id = register_entity(client)
    assert client.put(f"/api/fip/sources/{source_id}/ingestion-settings", headers=REVIEWER,
                      json={"mode": "MANUAL"}).status_code == 401
    direct_manual = client.post("/api/fip/observations", headers=ADMIN,
                                json=manual_observation(source_id, entity_id))
    assert direct_manual.status_code == 400
    assert "HYBRID mode" in direct_manual.get_json()["error"]
    inherited = client.post("/api/fip/ingestion/jobs", headers=ADMIN, json={"source_id": source_id})
    assert inherited.status_code == 200, inherited.get_json()
    assert inherited.get_json()["mode"] == "HYBRID"
    assert inherited.get_json()["status"] == "QUEUED"
    source_override = client.put(f"/api/fip/sources/{source_id}/ingestion-settings", headers=ADMIN,
                                 json={"mode": "AUTOMATIC"})
    assert source_override.status_code == 200, source_override.get_json()
    source_mode_job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                                  json={"source_id": source_id}).get_json()
    assert source_mode_job["mode"] == "AUTOMATIC"
    overridden = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                             json={"source_id": source_id, "mode": "MANUAL"})
    assert overridden.status_code == 200, overridden.get_json()
    assert overridden.get_json()["mode"] == "MANUAL"
    assert client.post("/api/fip/ingestion/jobs", json={"source_id": source_id}).status_code == 401

    events = client.get("/api/fip/audit", headers=REVIEWER).get_json()["events"]
    mode_event = next(item for item in events if item["action"] == "INGESTION_GLOBAL_MODE_UPDATED")
    assert mode_event["actor_role"] == "admin"
    assert mode_event["before"]["mode"] == "MANUAL"
    assert mode_event["after"]["mode"] == "HYBRID"


def test_manual_job_never_fetches_and_records_manual_workflow(tmp_path, monkeypatch):
    client = make_client(tmp_path / "fip.sqlite3")
    source_id = register_source(client)
    entity_id = register_entity(client)
    queued = client.post("/api/fip/ingestion/jobs", headers=ADMIN, json={"source_id": source_id})
    assert queued.status_code == 200 and queued.get_json()["mode"] == "MANUAL"
    job_id = queued.get_json()["id"]

    def should_not_fetch(_url):
        raise AssertionError("manual mode must never retrieve the source")

    monkeypatch.setattr(ingestion, "fetch_source", should_not_fetch)
    run = client.post(f"/api/fip/ingestion/jobs/{job_id}/run", headers=ADMIN)
    assert run.status_code == 400
    assert "do not fetch" in run.get_json()["error"]

    submitted = client.post(f"/api/fip/ingestion/jobs/{job_id}/observations", headers=ADMIN,
                            json={"observation": manual_observation(source_id, entity_id)})
    assert submitted.status_code == 200, submitted.get_json()
    assert submitted.get_json()["ingestion_mode"] == "MANUAL"
    assert submitted.get_json()["ingestion_job_id"] == job_id
    assert submitted.get_json()["extraction_method"] == "manual_transcription"
    job = client.get("/api/fip/ingestion/jobs", headers=ADMIN).get_json()["jobs"][0]
    assert job["status"] == "AWAITING_REVIEW"
    assert job["observations_created"] == 1
    observation_id = submitted.get_json()["id"]
    validated = client.post(f"/api/fip/observations/{observation_id}/validate", headers=REVIEWER,
                            json={"reason": "Checked the manual figure against its cited source."})
    assert validated.status_code == 200
    approved = client.post(f"/api/fip/observations/{observation_id}/approve", headers=REVIEWER,
                           json={"reason": "Approved after evidence review."})
    assert approved.status_code == 200
    published = client.post(f"/api/fip/observations/{observation_id}/publish", headers=REVIEWER,
                            json={"reason": "Included after reviewer sign-off."})
    assert published.status_code == 200
    completed = client.get("/api/fip/ingestion/jobs", headers=ADMIN).get_json()["jobs"][0]
    assert completed["status"] == "COMPLETED"


def test_automatic_connector_validates_and_is_idempotent(tmp_path, monkeypatch):
    client = make_client(tmp_path / "fip.sqlite3")
    source_id = register_source(client)
    entity_id = register_entity(client)
    configure_connector(client, source_id, entity_id, "AUTOMATIC")
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (
        "row_id,entity_id,domain,reported_label,value,period_id,evidence_reference\n"
        f"annual-revenue-2025,{entity_id},corporate,Revenue,1000,FY2025,Table 4 Revenue\n"
    ).encode())

    queued = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                         json={"source_id": source_id, "mode": "AUTOMATIC"})
    run = client.post(f"/api/fip/ingestion/jobs/{queued.get_json()['id']}/run", headers=ADMIN)
    assert run.status_code == 200, run.get_json()
    assert run.get_json()["status"] == "AWAITING_REVIEW"
    assert run.get_json()["observations_created"] == 1
    workspace = client.get("/api/fip/workspace", headers=ADMIN).get_json()
    observation = workspace["observations"][0]
    assert observation["status"] == "VALIDATED"
    assert observation["ingestion_mode"] == "AUTOMATIC"
    assert observation["extraction_method"] == "connector_csv"
    assert observation["ingestion_job_id"] == queued.get_json()["id"]
    assert client.get("/api/fip/publications").get_json()["release"] is None

    second = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                         json={"source_id": source_id, "mode": "AUTOMATIC"}).get_json()
    repeated = client.post(f"/api/fip/ingestion/jobs/{second['id']}/run", headers=ADMIN).get_json()
    assert repeated["status"] == "COMPLETED"
    assert repeated["observations_created"] == 0
    assert repeated["observations_skipped"] == 1
    assert len(client.get("/api/fip/workspace", headers=ADMIN).get_json()["observations"]) == 1


def test_json_connector_extracts_decimal_strings_and_never_publishes(tmp_path, monkeypatch):
    client = make_client(tmp_path / "fip.sqlite3")
    source_id = register_source(client)
    entity_id = register_entity(client)
    config = {
        "records_path": "data.rows",
        "field_map": {
            "domain": "domain", "reported_label": "metric", "value": "amount",
            "period_id": "period", "evidence_reference": "location",
        },
        "defaults": {
            "entity_id": entity_id, "currency": "INR", "unit": "ones",
            "period_start": "2024-04-01", "period_end": "2025-03-31", "period_type": "annual",
            "reporting_basis": "Ind AS consolidated", "framework_id": "IND_AS",
            "framework_version": "2024", "freshness_status": "CURRENT",
        },
        "idempotency_field": "id",
    }
    configured = client.put(f"/api/fip/sources/{source_id}/ingestion-settings", headers=ADMIN, json={
        "mode": "AUTOMATIC", "connector_format": "json", "connector_config": config,
    })
    assert configured.status_code == 200, configured.get_json()
    approved = client.post(f"/api/fip/sources/{source_id}/ingestion-settings/approve", headers=REVIEWER,
                           json={"reason": "Reviewed the JSON field mapping."})
    assert approved.status_code == 200
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (
        b'{"data":{"rows":[{"id":"revenue-2025","domain":"corporate",'
        b'"metric":"Revenue","amount":1234.56,"period":"FY2025","location":"table:4"}]}}'
    ))
    job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                      json={"source_id": source_id, "mode": "AUTOMATIC"}).get_json()
    result = client.post(f"/api/fip/ingestion/jobs/{job['id']}/run", headers=ADMIN).get_json()
    assert result["status"] == "AWAITING_REVIEW"
    observation = client.get("/api/fip/workspace", headers=ADMIN).get_json()["observations"][0]
    assert observation["value_decimal"] == "1234.56"
    assert observation["status"] == "VALIDATED"
    assert observation["extraction_method"] == "connector_json"
    assert observation["evidence_reference"] == "table:4"


def test_automatic_mode_refuses_a_source_without_public_display_rights(tmp_path, monkeypatch):
    database_path = tmp_path / "fip.sqlite3"
    client = make_client(database_path)
    source_id = register_source(client)
    entity_id = register_entity(client)
    configure_connector(client, source_id, entity_id, "AUTOMATIC")
    with core._conn(database_path) as db:
        db.execute("UPDATE sources SET permitted_use='link_only' WHERE id=?", (source_id,))
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (_ for _ in ()).throw(AssertionError("must not fetch")))
    job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                      json={"source_id": source_id, "mode": "AUTOMATIC"}).get_json()
    result = client.post(f"/api/fip/ingestion/jobs/{job['id']}/run", headers=ADMIN)
    assert result.status_code == 400
    assert "public-display" in result.get_json()["error"]


def test_hybrid_rows_can_be_corrected_but_automatic_rows_cannot(tmp_path, monkeypatch):
    client = make_client(tmp_path / "fip.sqlite3")
    source_id = register_source(client)
    entity_id = register_entity(client)
    configure_connector(client, source_id, entity_id, "HYBRID")
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (
        "row_id,domain,reported_label,value,period_id\n"
        "row-a,corporate,Turnover,1250,FY2025\n"
    ).encode())
    job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                      json={"source_id": source_id, "mode": "HYBRID"}).get_json()
    run = client.post(f"/api/fip/ingestion/jobs/{job['id']}/run", headers=ADMIN).get_json()
    assert run["status"] == "AWAITING_REVIEW"
    assert run["exceptions"][0]["status"] == "OPEN"
    correction = manual_observation(source_id, entity_id, evidence=f"https://data.example.org/financial.csv#record=row-a", value="1250")
    fixed = client.post(f"/api/fip/ingestion/jobs/{job['id']}/corrections", headers=ADMIN,
                        json={"row_number": 1, "observation": correction})
    assert fixed.status_code == 200, fixed.get_json()
    assert fixed.get_json()["ingestion_mode"] == "HYBRID"
    refreshed = client.get("/api/fip/ingestion/jobs", headers=ADMIN).get_json()["jobs"][0]
    assert refreshed["exceptions"][0]["status"] == "RESOLVED"
    assert refreshed["status"] == "AWAITING_REVIEW"

    auto_source = register_source(client)
    configure_connector(client, auto_source, entity_id, "AUTOMATIC")
    auto_job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                           json={"source_id": auto_source, "mode": "AUTOMATIC"}).get_json()
    auto_run = client.post(f"/api/fip/ingestion/jobs/{auto_job['id']}/run", headers=ADMIN).get_json()
    assert auto_run["exceptions"][0]["status"] == "OPEN"
    denied = client.post(f"/api/fip/ingestion/jobs/{auto_job['id']}/corrections", headers=ADMIN,
                         json={"row_number": 1, "observation": correction})
    assert denied.status_code == 400
    assert "only for HYBRID" in denied.get_json()["error"]


def test_scheduler_queues_due_approved_sources(tmp_path, monkeypatch):
    database_path = tmp_path / "fip.sqlite3"
    client = make_client(database_path)
    source_id = register_source(client)
    entity_id = register_entity(client)
    configure_connector(client, source_id, entity_id, "AUTOMATIC")
    configured = client.put(f"/api/fip/sources/{source_id}/ingestion-settings", headers=ADMIN, json={
        "mode": "AUTOMATIC", "schedule_interval_minutes": 1,
    })
    assert configured.status_code == 200, configured.get_json()
    with core._conn(database_path) as db:
        db.execute("UPDATE sources SET next_scheduled_at='2000-01-01T00:00:00+00:00' WHERE id=?", (source_id,))
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (
        "row_id,entity_id,domain,reported_label,value,period_id\n"
        f"scheduled-revenue,{entity_id},corporate,Revenue,1200,FY2025\n"
    ).encode())
    results = ingestion.run_due_schedules(database_path)
    assert len(results) == 1
    assert results[0]["trigger_type"] == "schedule"
    assert results[0]["mode"] == "AUTOMATIC"
    assert results[0]["status"] == "AWAITING_REVIEW"


def test_failed_automatic_job_can_be_retried_and_queued_job_cancelled(tmp_path, monkeypatch):
    client = make_client(tmp_path / "fip.sqlite3")
    source_id = register_source(client)
    entity_id = register_entity(client)
    configure_connector(client, source_id, entity_id, "AUTOMATIC")

    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (_ for _ in ()).throw(core.FIPError("temporary source outage")))
    queued = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                         json={"source_id": source_id, "mode": "AUTOMATIC"}).get_json()
    failed = client.post(f"/api/fip/ingestion/jobs/{queued['id']}/run", headers=ADMIN).get_json()
    assert failed["status"] == "FAILED"
    assert "temporary source outage" in failed["error_message"]
    assert failed["connector_version"].startswith("csv-json-1-csv-")
    assert client.post(f"/api/fip/ingestion/jobs/{queued['id']}/retry", headers=REVIEWER).status_code == 401

    retried = client.post(f"/api/fip/ingestion/jobs/{queued['id']}/retry", headers=ADMIN)
    assert retried.status_code == 200, retried.get_json()
    assert retried.get_json()["status"] == "QUEUED"
    assert retried.get_json()["attempt_count"] == 2
    monkeypatch.setattr(ingestion, "fetch_source", lambda _url: (
        "row_id,domain,reported_label,value,period_id\n" "retry-revenue,corporate,Revenue,1100,FY2025\n"
    ).encode())
    succeeded = client.post(f"/api/fip/ingestion/jobs/{queued['id']}/run", headers=ADMIN).get_json()
    assert succeeded["status"] == "AWAITING_REVIEW"
    assert succeeded["observations_created"] == 1
    assert succeeded["attempt_count"] == 2

    manual_job = client.post("/api/fip/ingestion/jobs", headers=ADMIN,
                             json={"source_id": source_id, "mode": "MANUAL"}).get_json()
    assert client.post(f"/api/fip/ingestion/jobs/{manual_job['id']}/cancel", headers=ADMIN, json={}).status_code == 400
    cancelled = client.post(f"/api/fip/ingestion/jobs/{manual_job['id']}/cancel", headers=ADMIN,
                            json={"reason": "Operator cancelled the duplicate request."})
    assert cancelled.status_code == 200, cancelled.get_json()
    assert cancelled.get_json()["status"] == "CANCELLED"
    assert client.post(f"/api/fip/ingestion/jobs/{manual_job['id']}/run", headers=ADMIN).status_code == 400
    assert client.post(f"/api/fip/ingestion/jobs/{manual_job['id']}/retry", headers=ADMIN).status_code == 400
