"""HTTP interface for the local FIP Release 1 foundation."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from . import core
from . import ingestion


def create_blueprint(db_path: Path) -> Blueprint:
    bp = Blueprint("fip", __name__)

    def payload() -> dict[str, Any] | tuple[Any, int]:
        if not request.is_json:
            return jsonify(error="Content-Type must be application/json."), 415
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="Request body must be a JSON object."), 400
        return body

    def require_role(role: str):
        expected = current_app.config.get("FIP_ADMIN_TOKEN" if role == "admin" else "FIP_REVIEWER_TOKEN")
        supplied = request.headers.get("X-FIP-Token", "")
        if not expected:
            return jsonify(error=f"FIP {role} access is not configured. Set the corresponding server environment variable and restart."), 503
        if role == "reviewer" and expected == current_app.config.get("FIP_ADMIN_TOKEN"):
            return jsonify(error="FIP admin and reviewer tokens must be different to preserve separation of duties."), 503
        if not core.constant_time_token_matches(supplied, expected):
            return jsonify(error=f"A valid FIP {role} token is required."), 401
        return None

    def staff_role():
        for role in ("admin", "reviewer"):
            expected = current_app.config.get("FIP_ADMIN_TOKEN" if role == "admin" else "FIP_REVIEWER_TOKEN")
            if core.constant_time_token_matches(request.headers.get("X-FIP-Token", ""), expected):
                return role
        return None

    def decision_reason():
        body = payload()
        if isinstance(body, tuple):
            return body
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return jsonify(error="A reviewer decision reason is required and will be retained in the audit log."), 400
        if len(reason.strip()) > 1000:
            return jsonify(error="Reviewer reason must be 1000 characters or fewer."), 400
        return reason.strip()

    def respond(operation, *args):
        try:
            return jsonify(operation(*args))
        except core.FIPError as error:
            return jsonify(error=str(error)), 400

    def readiness_payload():
        result = core.readiness(db_path)
        if staff_role() is None:
            for record in result["release_gates"]:
                record.pop("evidence_reference", None); record.pop("updated_by", None)
            for record in result["nonfunctional_requirements"]:
                record.pop("evidence_reference", None); record.pop("updated_by", None)
        return result

    @bp.get("/fip")
    def page():
        return render_template("fip.html", staff_open=False)

    @bp.get("/fip/staff")
    def staff_page():
        return render_template("fip.html", staff_open=True)

    @bp.get("/fip/guide")
    def guide():
        return render_template("fip_guide.html")

    @bp.get("/api/fip/health")
    def health():
        settings = ingestion.get_settings(db_path)
        return jsonify(status="ok", persistence="sqlite", source_retrieval="configurable",
                       default_ingestion_mode=settings["global_mode"], ingestion_modes=settings["modes"],
                       formula_catalog_version=core.FORMULA_CATALOG_VERSION, production_ready=False)

    @bp.get("/api/fip/metric-catalog")
    def metric_catalog():
        return jsonify(version=core.FORMULA_CATALOG_VERSION, corporate=core.CORPORATE_METRICS,
                       government=core.GOVERNMENT_METRICS,
                       definitions=core.METRIC_DEFINITIONS, formulas=core.FORMULA_DEFINITIONS,
                       frameworks=core.FRAMEWORKS,
                       approved_mappings=[item for item in core.staff_workspace(db_path)["metric_mappings"] if item["review_status"] == "VERIFIED"],
                       units=sorted(core.UNIT_FACTORS), period_types=sorted(core.PERIOD_TYPES),
                       fx_conventions=sorted(core.FX_CONVENTIONS))

    @bp.get("/api/fip/readiness")
    def readiness():
        return jsonify(readiness_payload())

    @bp.get("/api/fip/governance-catalog")
    def governance_catalog():
        return jsonify(frameworks=core.FRAMEWORKS, metrics=core.METRIC_DEFINITIONS,
                       formulas={"version": core.FORMULA_CATALOG_VERSION, "definitions": core.FORMULA_DEFINITIONS},
                       release_gates=core.RELEASE_GATE_CATALOG, nfr_register=core.NFR_CATALOG)

    @bp.get("/api/fip/release-gates")
    def release_gates():
        return jsonify(gates=readiness_payload()["release_gates"])

    @bp.get("/api/fip/nfr")
    def nfr_register():
        return jsonify(requirements=readiness_payload()["nonfunctional_requirements"])

    @bp.get("/api/fip/summary")
    def summary():
        return jsonify(core.summary(db_path))

    @bp.get("/api/fip/entities")
    def entities():
        visible = [item for item in core.list_entities(db_path) if item["review_status"] == "VERIFIED"]
        return jsonify(entities=visible)

    @bp.get("/api/fip/publications")
    def publications():
        return jsonify(core.list_publications(db_path,
                    request.args.get("entity_id") or None, request.args.get("period_id") or None,
                    request.args.get("release_id") or None))

    @bp.get("/api/fip/releases")
    def releases():
        return jsonify(releases=core.list_releases(db_path, approved_only=True))

    @bp.get("/api/fip/delivery/powerbi")
    def powerbi_delivery():
        publication = core.list_publications(db_path, release_id=request.args.get("release_id") or None)
        if publication["release"] is None:
            return jsonify(error="No approved publication release is available."), 404
        rows = _delivery_rows(publication)
        return jsonify({"release": publication["release"], "value": rows,
                        "currency_rates": publication["fx_rates"],
                        "model": {"grain": "one row per published observation or calculated metric",
                                  "measure_lineage": ["input_observations", "source_url", "evidence_reference"],
                                  "calculation_catalog_version": publication["release"].get("formula_catalog_version", core.FORMULA_CATALOG_VERSION)}})

    @bp.get("/api/fip/delivery/excel.csv")
    def excel_delivery():
        publication = core.list_publications(db_path, release_id=request.args.get("release_id") or None)
        if publication["release"] is None:
            return jsonify(error="No approved publication release is available."), 404
        columns = ["record_type", "release_version", "entity_id", "entity_name", "jurisdiction_code",
                   "domain", "metric_id", "reported_label", "value_decimal", "currency", "unit",
                   "standardized_value_decimal", "standardized_currency", "fx_rate_id", "conversion_convention",
                   "freshness_status", "validation_message", "validation_json",
                   "period_id", "period_start", "period_end", "reporting_basis", "framework_id", "framework_version", "fiscal_scope",
                   "budget_stage", "formula_id", "formula_version", "formula", "inputs_json",
                   "input_observations_json", "source_title", "source_url", "evidence_reference",
                   "observation_version", "publication_date"]
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in _delivery_rows(publication):
            writer.writerow({key: _csv_safe(value) for key, value in row.items()})
        filename = f"{publication['release']['version'].lower()}-financial-data.csv"
        return Response(output.getvalue(), mimetype="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    @bp.get("/api/fip/delivery/excel.xlsx")
    def excel_workbook_delivery():
        publication = core.list_publications(db_path, release_id=request.args.get("release_id") or None)
        if publication["release"] is None:
            return jsonify(error="No approved publication release is available."), 404
        try:
            from .workbook import build_release_workbook
            output = build_release_workbook(publication)
        except ModuleNotFoundError as error:
            if error.name == "openpyxl":
                return jsonify(error="Native Excel export is unavailable. Install the openpyxl dependency."), 503
            raise
        filename = f"{publication['release']['version'].lower()}-financial-data.xlsx"
        return Response(output.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    @bp.get("/api/fip/workspace")
    def workspace():
        role = staff_role()
        if role is None:
            return jsonify(error="A valid FIP staff token is required."), 401
        return jsonify(role=role, **core.staff_workspace(db_path))

    @bp.get("/api/fip/ingestion/settings")
    def ingestion_settings():
        if staff_role() is None:
            return jsonify(error="A valid FIP staff token is required."), 401
        return jsonify(ingestion.get_settings(db_path))

    @bp.put("/api/fip/ingestion/settings")
    def update_ingestion_settings():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(ingestion.set_global_mode, db_path, body.get("mode"), "admin")

    @bp.put("/api/fip/sources/<record_id>/ingestion-settings")
    def update_source_ingestion_settings(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(ingestion.set_source_settings, db_path, record_id, body, "admin")

    @bp.post("/api/fip/sources/<record_id>/ingestion-settings/approve")
    def approve_source_ingestion_settings(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(ingestion.approve_source_config, db_path, record_id, "reviewer", reason)

    @bp.get("/api/fip/ingestion/jobs")
    def ingestion_jobs():
        if staff_role() is None:
            return jsonify(error="A valid FIP staff token is required."), 401
        return jsonify(jobs=ingestion.list_jobs(db_path))

    @bp.post("/api/fip/ingestion/jobs")
    def queue_ingestion_job():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(ingestion.create_job, db_path, body.get("source_id"), body.get("mode"), "admin")

    @bp.post("/api/fip/ingestion/jobs/<record_id>/run")
    def run_ingestion_job(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        return respond(ingestion.run_job, db_path, record_id, "admin")

    @bp.post("/api/fip/ingestion/jobs/<record_id>/retry")
    def retry_ingestion_job(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        return respond(ingestion.retry_job, db_path, record_id, "admin")

    @bp.post("/api/fip/ingestion/jobs/<record_id>/cancel")
    def cancel_ingestion_job(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        reason = body.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 1000:
            return jsonify(error="A cancellation reason of 1 to 1000 characters is required and will be audited."), 400
        return respond(ingestion.cancel_job, db_path, record_id, "admin", reason.strip())

    @bp.post("/api/fip/ingestion/jobs/<record_id>/observations")
    def add_manual_job_observation(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        observation = body.get("observation")
        if not isinstance(observation, dict):
            return jsonify(error="observation must be a JSON object."), 400
        return respond(ingestion.add_manual_observation, db_path, record_id, observation, "admin")

    @bp.post("/api/fip/ingestion/jobs/<record_id>/corrections")
    def correct_hybrid_job_exception(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        observation = body.get("observation")
        if not isinstance(observation, dict):
            return jsonify(error="observation must be a JSON object."), 400
        return respond(ingestion.correct_hybrid_exception, db_path, record_id,
                       body.get("row_number"), observation, "admin")

    @bp.get("/api/fip/audit")
    def audit():
        denied = require_role("reviewer")
        if denied:
            return denied
        return jsonify(events=core.audit_events(db_path, request.args.get("record_type"), request.args.get("record_id")))

    @bp.post("/api/fip/entities")
    def create_entity():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_entity, db_path, body, "admin")

    @bp.post("/api/fip/sources")
    def create_source():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_source, db_path, body, "admin")

    @bp.post("/api/fip/fx-rates")
    def create_fx_rate():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_fx_rate, db_path, body, "admin")

    @bp.post("/api/fip/observations")
    def create_observation():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_observation, db_path, body, "admin")

    @bp.post("/api/fip/entities/<record_id>/aliases")
    def create_entity_alias(record_id: str):
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        body["entity_id"] = record_id
        return respond(core.create_entity_alias, db_path, body, "admin")

    @bp.post("/api/fip/relationships")
    def create_relationship():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_entity_relationship, db_path, body, "admin")

    @bp.post("/api/fip/metric-mappings")
    def create_metric_mapping():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_metric_mapping, db_path, body, "admin")

    @bp.post("/api/fip/calculations")
    def calculate():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.calculate, db_path, body, "admin")

    @bp.post("/api/fip/reconciliation")
    def reconcile():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.reconcile, db_path, body, "admin")

    @bp.post("/api/fip/releases")
    def create_release():
        denied = require_role("admin")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.create_release, db_path, body.get("notes"), "admin")

    @bp.post("/api/fip/entities/<record_id>/verify")
    def verify_entity(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.verify_entity, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/entity-aliases/<record_id>/verify")
    def verify_entity_alias(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.review_governance_record, db_path, "entity_aliases", record_id, reason, "reviewer")

    @bp.post("/api/fip/relationships/<record_id>/verify")
    def verify_relationship(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.review_governance_record, db_path, "entity_relationships", record_id, reason, "reviewer")

    @bp.post("/api/fip/metric-mappings/<record_id>/verify")
    def verify_metric_mapping(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.review_governance_record, db_path, "metric_mappings", record_id, reason, "reviewer")

    @bp.put("/api/fip/release-gates/<gate_id>")
    def update_release_gate(gate_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.update_release_gate, db_path, gate_id, body, "reviewer")

    @bp.put("/api/fip/nfr/<requirement_id>")
    def update_nfr(requirement_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        body = payload()
        if isinstance(body, tuple): return body
        return respond(core.update_nfr, db_path, requirement_id, body, "reviewer")

    @bp.post("/api/fip/sources/<record_id>/approve")
    def approve_source(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.approve_source, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/fx-rates/<record_id>/approve")
    def approve_fx_rate(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.approve_fx_rate, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/observations/<record_id>/validate")
    def validate_observation(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.validate_observation, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/observations/<record_id>/approve")
    def approve_observation(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.approve_observation, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/observations/<record_id>/publish")
    def publish_observation(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.publish_observation, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/calculations/<record_id>/publish")
    def publish_calculation(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.publish_calculation, db_path, record_id, "reviewer", reason)

    @bp.post("/api/fip/releases/<record_id>/approve")
    def approve_release(record_id: str):
        denied = require_role("reviewer")
        if denied: return denied
        reason = decision_reason()
        if isinstance(reason, tuple): return reason
        return respond(core.approve_release, db_path, record_id, reason, "reviewer")

    return bp


def _delivery_rows(publication: dict[str, Any]) -> list[dict[str, Any]]:
    release_version = publication["release"]["version"]
    rows: list[dict[str, Any]] = []
    for rate in publication.get("fx_rates", []):
        rows.append({"record_type": "fx_rate", "release_version": release_version,
                     "metric_id": "approved_fx_rate", "value_decimal": rate["rate_decimal"],
                     "currency": rate["target_currency"], "unit": f"{rate['target_currency']} per {rate['source_currency']}",
                     "period_id": rate["period_id"], "formula_id": rate["conversion_convention"],
                     "source_title": rate["source_title"], "source_url": rate["source_url"],
                     "fx_rate_id": rate["id"], "evidence_reference": rate["source_id"]})
    for item in publication["observations"]:
        rows.append({
            "record_type": "observation", "release_version": release_version,
            "entity_id": item["entity_id"], "entity_name": item["canonical_name"],
            "jurisdiction_code": item["jurisdiction_code"], "domain": item["domain"],
            "metric_id": item["metric_id"], "reported_label": item["reported_label"],
            "value_decimal": item["value_decimal"], "currency": item["currency"], "unit": item["unit"],
            "standardized_value_decimal": item["standardized_value_decimal"],
            "standardized_currency": item["standardized_currency"], "fx_rate_id": item["fx_rate_id"],
            "conversion_convention": item["conversion_convention"],
            "freshness_status": item["freshness_status"], "validation_message": item["validation_message"],
            "validation_json": item["validation_json"],
            "period_id": item["period_id"], "period_start": item["period_start"],
            "period_end": item["period_end"], "reporting_basis": item["reporting_basis"],
            "framework_id": item["framework_id"], "framework_version": item["framework_version"],
            "fiscal_scope": item["fiscal_scope"], "budget_stage": item["budget_stage"],
            "source_title": item["source_title"], "source_url": item["source_url"],
            "evidence_reference": item["evidence_reference"], "observation_version": item["version"],
            "publication_date": item["publication_date"],
            "input_observations_json": "[]",
        })
    for run in publication["calculations"]:
        result = run["result"]
        input_records = result.get("input_observations", [])
        for metric_id, metric in result["metrics"].items():
            rows.append({
                "record_type": "calculation", "release_version": release_version,
                "entity_id": run["entity_id"], "entity_name": run["canonical_name"],
                "jurisdiction_code": "", "domain": run["domain"], "metric_id": metric_id,
                "reported_label": metric["label"], "value_decimal": metric["value_decimal"],
                "currency": result["currency"], "unit": metric["unit"],
                "standardized_currency": result["currency"],
                "period_id": run["period_id"], "period_start": "", "period_end": "",
                "reporting_basis": result["reporting_basis"],
                "framework_id": result["framework_id"], "framework_version": result["framework_version"],
                "fiscal_scope": result.get("fiscal_scope", ""), "budget_stage": result.get("budget_stage", ""),
                "formula_id": metric["formula_id"], "formula_version": metric["formula_version"],
                "formula": metric["formula"], "inputs_json": json.dumps(metric["inputs"], sort_keys=True),
                "input_observations_json": json.dumps(input_records, sort_keys=True),
                "source_title": "", "source_url": "", "evidence_reference": "",
                "observation_version": "", "publication_date": "",
            })
    return rows


def _csv_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value
