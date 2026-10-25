"""Selectable, review-gated FIP source ingestion workflows.

Automatic connectors accept only approved HTTPS CSV/JSON sources. Extracted
records are structurally validated and remain unpublished until reviewer action.
"""
from __future__ import annotations

import csv
import hashlib
import http.client
import ipaddress
import io
import json
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from . import core


MODES = core.INGESTION_MODES
CONNECTOR_FORMATS = {"manual_only", "csv", "json"}
MAX_SOURCE_BYTES = 5 * 1024 * 1024
MAX_RECORDS = 10_000
CONNECTOR_IMPLEMENTATION_VERSION = "csv-json-1"
REQUIRED_FIELDS = {
    "entity_id", "domain", "reported_label", "value", "currency", "unit",
    "period_id", "period_start", "period_end", "period_type", "reporting_basis",
    "framework_id", "framework_version",
}
OPTIONAL_FIELDS = {"fiscal_scope", "budget_stage", "freshness_status", "evidence_reference"}


def _validate_mode(value: Any, *, allow_inherit: bool = False) -> str | None:
    if allow_inherit and value in (None, ""):
        return None
    if not isinstance(value, str) or value not in MODES:
        raise core.FIPError("ingestion mode must be MANUAL, AUTOMATIC, or HYBRID.")
    return value


def _parse_config(value: Any, connector_format: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise core.FIPError("connector_config must be a JSON object.")
    if connector_format == "manual_only":
        if value:
            raise core.FIPError("manual_only connectors do not accept extraction configuration.")
        return {}
    if connector_format not in {"csv", "json"}:
        raise core.FIPError("connector_format must be manual_only, csv, or json.")
    allowed = {"field_map", "defaults", "records_path", "idempotency_field"}
    if set(value) - allowed:
        raise core.FIPError("connector_config contains unsupported keys.")
    field_map = value.get("field_map", {})
    defaults = value.get("defaults", {})
    if not isinstance(field_map, dict) or not isinstance(defaults, dict):
        raise core.FIPError("connector_config field_map and defaults must be JSON objects.")
    accepted_fields = REQUIRED_FIELDS | OPTIONAL_FIELDS
    if set(field_map) - accepted_fields or set(defaults) - accepted_fields:
        raise core.FIPError("connector_config maps an unsupported observation field.")
    for target, path in field_map.items():
        if not isinstance(path, str) or not path.strip() or len(path) > 200:
            raise core.FIPError(f"field_map.{target} must name a source column or JSON path.")
    if not REQUIRED_FIELDS.issubset(set(field_map) | set(defaults)):
        missing = sorted(REQUIRED_FIELDS - (set(field_map) | set(defaults)))
        raise core.FIPError("connector_config is missing required fields: " + ", ".join(missing))
    if "records_path" in value and (not isinstance(value["records_path"], str) or len(value["records_path"]) > 200):
        raise core.FIPError("records_path must be a JSON path of 200 characters or fewer.")
    if "idempotency_field" in value and (not isinstance(value["idempotency_field"], str) or len(value["idempotency_field"]) > 200):
        raise core.FIPError("idempotency_field must be a source field name of 200 characters or fewer.")
    for target, default in defaults.items():
        if isinstance(default, (dict, list, bool)):
            raise core.FIPError(f"defaults.{target} must be a string or number.")
        if default is not None and len(str(default)) > 500:
            raise core.FIPError(f"defaults.{target} must be 500 characters or fewer.")
    return {"field_map": field_map, "defaults": defaults,
            **({"records_path": value["records_path"]} if value.get("records_path") else {}),
            **({"idempotency_field": value["idempotency_field"]} if value.get("idempotency_field") else {})}


def _settings_value(db: Any) -> str:
    row = db.execute("SELECT setting_value FROM fip_settings WHERE setting_key='global_ingestion_mode'").fetchone()
    return row["setting_value"] if row else "MANUAL"


def _connector_version(connector_format: str, config_json: str) -> str:
    digest = hashlib.sha256(f"{CONNECTOR_IMPLEMENTATION_VERSION}\0{connector_format}\0{config_json}".encode("utf-8")).hexdigest()[:12]
    return f"{CONNECTOR_IMPLEMENTATION_VERSION}-{connector_format}-{digest}"


def get_settings(db_path: Path) -> dict[str, Any]:
    with core._conn(db_path) as db:
        global_mode = _settings_value(db)
        sources = [dict(row) for row in db.execute(
            "SELECT id,title,approval_status,permitted_use,ingestion_mode,connector_format,connector_config_json,"
            "schedule_interval_minutes,next_scheduled_at,ingestion_config_status,ingestion_config_approved_by,"
            "ingestion_config_approved_at FROM sources ORDER BY title")]
        jobs = [_job_record(row) for row in db.execute(
            "SELECT j.*,s.title AS source_title FROM ingestion_jobs j JOIN sources s ON s.id=j.source_id "
            "ORDER BY j.created_at DESC LIMIT 50")]
    for source in sources:
        source["connector_config"] = json.loads(source.pop("connector_config_json"))
    return {"global_mode": global_mode, "modes": sorted(MODES), "connector_formats": sorted(CONNECTOR_FORMATS),
            "sources": sources, "jobs": jobs}


def _job_record(row: Any) -> dict[str, Any]:
    record = dict(row)
    record["exceptions"] = json.loads(record.pop("exceptions_json"))
    if record.get("cancelled_at"):
        record["status"] = "CANCELLED"
    return record


def list_jobs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    with core._conn(db_path) as db:
        rows = db.execute("SELECT j.*,s.title AS source_title FROM ingestion_jobs j JOIN sources s ON s.id=j.source_id "
                          "ORDER BY j.created_at DESC LIMIT ?", (max(1, min(limit, 200)),)).fetchall()
    return [_job_record(row) for row in rows]


def set_global_mode(db_path: Path, mode: Any, role: str) -> dict[str, Any]:
    mode = _validate_mode(mode)
    with core._conn(db_path) as db:
        before = {"mode": _settings_value(db)}
        now = datetime.now(timezone.utc)
        if mode == "MANUAL":
            paused = db.execute("UPDATE sources SET next_scheduled_at=NULL WHERE ingestion_mode IS NULL "
                                "AND schedule_interval_minutes IS NOT NULL AND next_scheduled_at IS NOT NULL").rowcount
            armed = 0
        else:
            paused = 0
            sources = db.execute("SELECT id,schedule_interval_minutes FROM sources WHERE ingestion_mode IS NULL "
                                 "AND schedule_interval_minutes IS NOT NULL AND next_scheduled_at IS NULL "
                                 "AND connector_format!='manual_only' AND ingestion_config_status='APPROVED' "
                                 "AND approval_status='APPROVED' AND permitted_use='public_display'").fetchall()
            for source in sources:
                next_at = (now + timedelta(minutes=int(source["schedule_interval_minutes"]))).isoformat(timespec="seconds")
                db.execute("UPDATE sources SET next_scheduled_at=? WHERE id=?", (next_at, source["id"]))
            armed = len(sources)
        db.execute("UPDATE fip_settings SET setting_value=?,updated_by=?,updated_at=? WHERE setting_key='global_ingestion_mode'",
                   (mode, role, core.now_utc()))
        after = {"mode": mode, "updated_by": role, "schedules_paused": paused, "schedules_armed": armed}
        core._audit(db, role, "INGESTION_GLOBAL_MODE_UPDATED", "ingestion_settings", "global",
                    before=before, after=after,
                    reason=f"Global default changed to {mode}; no job was started. {paused} inherited schedules paused, {armed} armed.")
    return after


def set_source_settings(db_path: Path, source_id: str, values: dict[str, Any], role: str) -> dict[str, Any]:
    allowed = {"mode", "connector_format", "connector_config", "schedule_interval_minutes"}
    if set(values) - allowed:
        raise core.FIPError("Unsupported source ingestion setting.")
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise core.FIPError("source was not found.")
        before = dict(row)
        mode = _validate_mode(values.get("mode", row["ingestion_mode"]), allow_inherit=True)
        connector_format = values.get("connector_format", row["connector_format"])
        if connector_format not in CONNECTOR_FORMATS:
            raise core.FIPError("connector_format must be manual_only, csv, or json.")
        config_value = values.get("connector_config", json.loads(row["connector_config_json"]))
        config = _parse_config(config_value, connector_format)
        interval = values.get("schedule_interval_minutes", row["schedule_interval_minutes"])
        if interval in (None, ""):
            interval = None
        elif isinstance(interval, bool) or not isinstance(interval, int) or not 1 <= interval <= 525600:
            raise core.FIPError("schedule_interval_minutes must be an integer from 1 to 525600, or null to disable scheduling.")
        config_changed = connector_format != row["connector_format"] or core._json(config) != row["connector_config_json"]
        config_status = "PENDING_REVIEW" if config_changed else row["ingestion_config_status"]
        approved_by = None if config_changed else row["ingestion_config_approved_by"]
        approved_at = None if config_changed else row["ingestion_config_approved_at"]
        if config_changed:
            interval = None
        effective = mode or _settings_value(db)
        if effective == "MANUAL":
            interval = None
        if interval is not None:
            if connector_format == "manual_only":
                raise core.FIPError("A schedule requires a configured CSV or JSON connector.")
            if config_status != "APPROVED":
                raise core.FIPError("A reviewer must approve the source connector before scheduling it.")
        next_scheduled_at = None
        if interval is not None:
            if interval == row["schedule_interval_minutes"] and row["next_scheduled_at"]:
                next_scheduled_at = row["next_scheduled_at"]
            else:
                next_scheduled_at = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat(timespec="seconds")
        db.execute("""UPDATE sources SET ingestion_mode=?,connector_format=?,connector_config_json=?,
                   schedule_interval_minutes=?,next_scheduled_at=?,ingestion_config_status=?,
                   ingestion_config_approved_by=?,ingestion_config_approved_at=? WHERE id=?""",
                   (mode, connector_format, core._json(config), interval, next_scheduled_at,
                    config_status, approved_by, approved_at, source_id))
        after = dict(db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        core._audit(db, role, "INGESTION_SOURCE_SETTINGS_UPDATED", "source_ingestion_settings", source_id,
                    before=before, after=after,
                    reason=("Connector changed and now requires reviewer approval." if config_changed else "Source ingestion policy updated; no job was started."))
    after["connector_config"] = json.loads(after.pop("connector_config_json"))
    return after


def approve_source_config(db_path: Path, source_id: str, role: str, reason: str) -> dict[str, Any]:
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise core.FIPError("source was not found.")
        if row["approval_status"] != "APPROVED" or row["permitted_use"] != "public_display":
            raise core.FIPError("The source itself must be approved for public display before its connector can be approved.")
        if row["ingestion_config_status"] == "APPROVED":
            raise core.FIPError("The source connector is already approved.")
        _parse_config(json.loads(row["connector_config_json"]), row["connector_format"])
        before = dict(row)
        db.execute("UPDATE sources SET ingestion_config_status='APPROVED',ingestion_config_approved_by=?,ingestion_config_approved_at=? WHERE id=?",
                   (role, core.now_utc(), source_id))
        after = dict(db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone())
        core._audit(db, role, "INGESTION_CONNECTOR_APPROVED", "source_ingestion_settings", source_id,
                    before=before, after=after, reason=reason)
    after["connector_config"] = json.loads(after.pop("connector_config_json"))
    return after


def create_job(db_path: Path, source_id: str, mode_override: Any, role: str,
               trigger_type: str = "user") -> dict[str, Any]:
    override = _validate_mode(mode_override, allow_inherit=True)
    if trigger_type not in {"user", "schedule"}:
        raise core.FIPError("trigger_type is not supported.")
    with core._conn(db_path) as db:
        source = db.execute("SELECT * FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None:
            raise core.FIPError("source was not found.")
        mode = override or source["ingestion_mode"] or _settings_value(db)
        config_json = source["connector_config_json"]
        job = {"id": core.new_id("FIP-ING"), "source_id": source_id, "mode": mode,
               "mode_override": override, "status": "QUEUED", "trigger_type": trigger_type,
               "requested_by": role, "created_at": core.now_utc(), "started_at": None,
               "completed_at": None, "records_seen": 0, "observations_created": 0,
               "observations_skipped": 0, "exceptions_json": "[]", "error_message": None,
               "attempt_count": 1, "connector_version": _connector_version(source["connector_format"], config_json),
               "last_updated_at": core.now_utc(), "cancelled_at": None}
        columns = ",".join(job)
        db.execute(f"INSERT INTO ingestion_jobs({columns}) VALUES({','.join('?' for _ in job)})", tuple(job.values()))
        core._audit(db, role, "INGESTION_JOB_QUEUED", "ingestion_job", job["id"], after={**job, "exceptions_json": []},
                    reason=f"{mode} ingestion job queued for source {source_id}.")
        result = dict(job); result["source_title"] = source["title"]; result["exceptions"] = []
        result.pop("exceptions_json")
    return result


def retry_job(db_path: Path, job_id: str, role: str) -> dict[str, Any]:
    with core._conn(db_path) as db:
        row = db.execute("SELECT j.*,s.approval_status,s.permitted_use,s.connector_format,s.connector_config_json,"
                         "s.ingestion_config_status FROM ingestion_jobs j JOIN sources s ON s.id=j.source_id WHERE j.id=?",
                         (job_id,)).fetchone()
        if row is None:
            raise core.FIPError("ingestion job was not found.")
        job = dict(row)
        if job["status"] != "FAILED" or job["cancelled_at"]:
            raise core.FIPError("Only failed, non-cancelled ingestion jobs can be retried.")
        if job["mode"] != "MANUAL":
            if job["approval_status"] != "APPROVED" or job["permitted_use"] != "public_display":
                raise core.FIPError("Retry requires an approved public-display source.")
            if job["ingestion_config_status"] != "APPROVED" or job["connector_format"] == "manual_only":
                raise core.FIPError("Retry requires a reviewer-approved CSV or JSON connector.")
        before = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        now = core.now_utc()
        version = _connector_version(job["connector_format"], job["connector_config_json"])
        db.execute("UPDATE ingestion_jobs SET status='QUEUED',attempt_count=attempt_count+1,connector_version=?,"
                   "started_at=NULL,completed_at=NULL,last_updated_at=?,cancelled_at=NULL,exceptions_json='[]',"
                   "error_message=NULL WHERE id=?", (version, now, job_id))
        after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        core._audit(db, role, "INGESTION_JOB_RETRY_QUEUED", "ingestion_job", job_id,
                    before=before, after=after,
                    reason=f"Attempt {after['attempt_count']} queued with connector {version}; prior observation idempotency is retained.")
        result = _job_record(after)
    return result


def cancel_job(db_path: Path, job_id: str, role: str, reason: str) -> dict[str, Any]:
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise core.FIPError("ingestion job was not found.")
        if row["status"] != "QUEUED" or row["cancelled_at"]:
            raise core.FIPError("Only queued ingestion jobs can be cancelled.")
        before = dict(row)
        now = core.now_utc()
        # Keep the original SQLite status constraint compatible; the public job
        # representation reports CANCELLED from the durable cancellation stamp.
        db.execute("UPDATE ingestion_jobs SET status='FAILED',cancelled_at=?,completed_at=?,last_updated_at=?,error_message=? WHERE id=?",
                   (now, now, now, "Cancelled by administrator.", job_id))
        after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        core._audit(db, role, "INGESTION_JOB_CANCELLED", "ingestion_job", job_id,
                    before=before, after=after, reason=reason)
    return _job_record(after)


def _public_host(url: str) -> tuple[str, int, list[str]]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise core.FIPError("Automatic retrieval requires an HTTPS URL without embedded credentials.")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)}
    except OSError:
        raise core.FIPError("Source host could not be resolved.") from None
    if not addresses or any(not ipaddress.ip_address(item).is_global for item in addresses):
        raise core.FIPError("Automatic retrieval is restricted to public internet hosts.")
    return parsed.hostname, parsed.port or 443, sorted(addresses)


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Request | None:
        target = urljoin(req.full_url, newurl)
        _public_host(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to the public address already checked while keeping TLS host verification."""
    def __init__(self, host: str, addresses: list[str], **kwargs: Any):
        self._resolved_addresses = addresses
        super().__init__(host, **kwargs)

    def connect(self) -> None:
        last_error: OSError | None = None
        for address in self._resolved_addresses:
            try:
                sock = socket.create_connection((address, self.port), self.timeout, self.source_address)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as error:
                last_error = error
        if last_error:
            raise last_error
        raise OSError("Source host has no usable public address.")


class _PinnedHTTPSHandler(HTTPSHandler):
    def https_open(self, req: Request):
        host, port, addresses = _public_host(req.full_url)
        return self.do_open(lambda _host, **kwargs: _PinnedHTTPSConnection(host, addresses, port=port, **kwargs), req)


def fetch_source(url: str) -> bytes:
    _public_host(url)
    request = Request(url, headers={"User-Agent": "FIP-Financial-Intelligence/1.0", "Accept": "text/csv, application/json, */*"})
    opener = build_opener(ProxyHandler({}), _SafeRedirectHandler(), _PinnedHTTPSHandler())
    try:
        with opener.open(request, timeout=15) as response:
            if response.status < 200 or response.status >= 300:
                raise core.FIPError(f"Source returned HTTP {response.status}.")
            data = response.read(MAX_SOURCE_BYTES + 1)
    except HTTPError as error:
        raise core.FIPError(f"Source returned HTTP {error.code}.") from None
    except (URLError, TimeoutError, OSError) as error:
        raise core.FIPError(f"Source retrieval failed: {error.reason if isinstance(error, URLError) else 'connection error'}.") from None
    if len(data) > MAX_SOURCE_BYTES:
        raise core.FIPError("Source response exceeds the 5 MiB connector limit.")
    return data


def _path_value(record: Any, path: str, *, json_mode: bool) -> Any:
    if not json_mode:
        return record.get(path)
    value = record
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


def _read_records(data: bytes, source: dict[str, Any]) -> list[dict[str, Any]]:
    connector_format = source["connector_format"]
    config = json.loads(source["connector_config_json"])
    if connector_format == "csv":
        try:
            text = data.decode("utf-8-sig")
            records = list(csv.DictReader(io.StringIO(text)))
        except (UnicodeDecodeError, csv.Error):
            raise core.FIPError("The source is not valid UTF-8 CSV.") from None
    elif connector_format == "json":
        try:
            document = json.loads(data.decode("utf-8-sig"), parse_float=str, parse_int=str)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise core.FIPError("The source is not valid UTF-8 JSON.") from None
        path = config.get("records_path", "")
        records = _path_value(document, path, json_mode=True) if path else document
        if not isinstance(records, list):
            raise core.FIPError("JSON connector records_path must resolve to an array.")
    else:
        raise core.FIPError("The source has no automatic connector configured.")
    if len(records) > MAX_RECORDS:
        raise core.FIPError(f"The source exceeds the {MAX_RECORDS:,} record connector limit.")
    if any(not isinstance(record, dict) for record in records):
        raise core.FIPError("Every connector record must be an object or CSV row.")
    return records


def _resolve_metric(db_path: Path, source: dict[str, Any], data: dict[str, Any]) -> str:
    domain = data.get("domain")
    metrics = core.CORPORATE_METRICS if domain == "corporate" else core.GOVERNMENT_METRICS if domain == "government" else {}
    if not metrics:
        raise core.FIPError("domain must be corporate or government.")
    label = str(data.get("reported_label", "")).strip()
    canonical = {value.casefold(): key for key, value in metrics.items()}
    if label.casefold() in canonical:
        return canonical[label.casefold()]
    with core._conn(db_path) as db:
        entity = db.execute("SELECT jurisdiction_code FROM entities WHERE id=?", (data.get("entity_id"),)).fetchone()
        if entity is None:
            raise core.FIPError("entity_id was not found.")
        rows = db.execute("""SELECT DISTINCT metric_id FROM metric_mappings WHERE source_id=? AND domain=?
                            AND lower(trim(reported_label))=? AND framework_id=? AND framework_version=?
                            AND jurisdiction_code=? AND review_status='VERIFIED'
                            AND (valid_from IS NULL OR valid_from<=?) AND (valid_to IS NULL OR valid_to>=?)""",
                          (source["id"], domain, label.casefold(), data.get("framework_id"),
                           data.get("framework_version"), entity["jurisdiction_code"],
                           data.get("period_start"), data.get("period_end"))).fetchall()
    mapped = {row["metric_id"] for row in rows}
    if len(mapped) != 1 or next(iter(mapped), None) not in metrics:
        raise core.FIPError("reported_label does not match a canonical metric or a unique approved source mapping.")
    return next(iter(mapped))


def _mapped_payload(db_path: Path, source: dict[str, Any], record: dict[str, Any], row_number: int,
                    json_mode: bool, config: dict[str, Any]) -> dict[str, Any]:
    data = dict(config.get("defaults", {}))
    for target, path in config.get("field_map", {}).items():
        value = _path_value(record, path, json_mode=json_mode)
        if value is not None:
            data[target] = value
    if not data.get("evidence_reference"):
        row_id = _path_value(record, config["idempotency_field"], json_mode=json_mode) if config.get("idempotency_field") else row_number
        data["evidence_reference"] = f"{source['url']}#record={row_id}"
    data.setdefault("freshness_status", "UNKNOWN")
    data["source_id"] = source["id"]
    if "value" in data and not isinstance(data["value"], str):
        data["value"] = str(data["value"])
    if "fiscal_scope" not in data and data.get("domain") == "government":
        raise core.FIPError("government connector records require fiscal_scope.")
    if "budget_stage" not in data and data.get("domain") == "government":
        raise core.FIPError("government connector records require budget_stage.")
    data["metric_id"] = _resolve_metric(db_path, source, data)
    metrics = core.CORPORATE_METRICS if data["domain"] == "corporate" else core.GOVERNMENT_METRICS
    data.setdefault("reported_label", metrics[data["metric_id"]])
    return data


def _idempotency_key(source_id: str, record: dict[str, Any], config: dict[str, Any], row_number: int,
                     json_mode: bool) -> str:
    if config.get("idempotency_field"):
        stable = _path_value(record, config["idempotency_field"], json_mode=json_mode)
        if stable is None or str(stable).strip() == "":
            raise core.FIPError("configured idempotency_field is missing from this source row.")
    else:
        stable = row_number
    value = f"{source_id}\0{stable}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _same_observation(existing: dict[str, Any], payload: dict[str, Any]) -> bool:
    for field in ("entity_id", "domain", "metric_id", "currency", "unit", "period_id", "period_start",
                  "period_end", "reporting_basis", "framework_id", "framework_version"):
        if str(existing.get(field)) != str(payload.get(field)):
            return False
    try:
        return core._amount(existing["value_decimal"]) == core._amount(payload["value"])
    except core.FIPError:
        return False


def _finish_job(db_path: Path, job_id: str, *, status: str, records_seen: int = 0,
                created: int = 0, skipped: int = 0, exceptions: list[dict[str, Any]] | None = None,
                error: str | None = None, role: str = "system") -> dict[str, Any]:
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise core.FIPError("ingestion job was not found.")
        before = dict(row)
        finished_at = core.now_utc()
        db.execute("UPDATE ingestion_jobs SET status=?,completed_at=?,last_updated_at=?,records_seen=?,observations_created=?,"
                   "observations_skipped=?,exceptions_json=?,error_message=? WHERE id=?",
                   (status, finished_at, finished_at, records_seen, created, skipped, core._json(exceptions or []), error, job_id))
        after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        core._audit(db, role, "INGESTION_JOB_FINISHED", "ingestion_job", job_id, before=before, after=after,
                    reason=error or f"Job finished with status {status}; {created} observations created, {skipped} idempotent rows skipped.")
        return _job_record(after)


def run_job(db_path: Path, job_id: str, role: str = "admin") -> dict[str, Any]:
    with core._conn(db_path) as db:
        row = db.execute("SELECT j.*,s.title AS source_title,s.url,s.approval_status,s.permitted_use,"
                         "s.connector_format,s.connector_config_json,s.ingestion_config_status,s.ingestion_mode "
                         "AS source_mode FROM ingestion_jobs j JOIN sources s ON s.id=j.source_id WHERE j.id=?", (job_id,)).fetchone()
        if row is None:
            raise core.FIPError("ingestion job was not found.")
        job = dict(row)
        if job["status"] != "QUEUED":
            raise core.FIPError("Only queued ingestion jobs can be run.")
        if job["mode"] == "MANUAL":
            raise core.FIPError("Manual jobs wait for a user to enter observations; they do not fetch sources.")
        if job["approval_status"] != "APPROVED" or job["permitted_use"] != "public_display":
            raise core.FIPError("Automatic retrieval requires an approved public-display source.")
        if job["ingestion_config_status"] != "APPROVED":
            raise core.FIPError("A reviewer must approve this source's connector configuration before retrieval.")
        if job["connector_format"] == "manual_only":
            raise core.FIPError("The source has no CSV or JSON connector configured.")
        current_version = _connector_version(job["connector_format"], job["connector_config_json"])
        if job["connector_version"] != current_version:
            error = "The approved source connector changed after this job was queued. Retry the failed job after reviewing the new connector configuration."
            finished_at = core.now_utc()
            before = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
            db.execute("UPDATE ingestion_jobs SET status='FAILED',error_message=?,completed_at=?,last_updated_at=? WHERE id=?",
                       (error, finished_at, finished_at, job_id))
            after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
            core._audit(db, role, "INGESTION_JOB_FAILED", "ingestion_job", job_id, before=before, after=after, reason=error)
            return _job_record(after)
        before = {key: job[key] for key in ("status", "mode", "started_at")}
        started_at = core.now_utc()
        db.execute("UPDATE ingestion_jobs SET status='RUNNING',started_at=?,last_updated_at=? WHERE id=?",
                   (started_at, started_at, job_id))
        core._audit(db, role, "INGESTION_JOB_STARTED", "ingestion_job", job_id, before=before,
                    after={"status": "RUNNING", "mode": job["mode"]}, reason=f"{job['mode']} connector run started.")

    try:
        source = {**job, "id": job["source_id"]}
        source_data = fetch_source(job["url"])
        records = _read_records(source_data, source)
        config = json.loads(job["connector_config_json"])
        json_mode = job["connector_format"] == "json"
    except core.FIPError as error:
        return _finish_job(db_path, job_id, status="FAILED", error=str(error), role=role)

    exceptions: list[dict[str, Any]] = []
    created = skipped = 0
    for index, record in enumerate(records, start=1):
        try:
            payload = _mapped_payload(db_path, source, record, index, json_mode, config)
            key = _idempotency_key(source["id"], record, config, index, json_mode)
            with core._conn(db_path) as db:
                existing = db.execute("SELECT * FROM observations WHERE idempotency_key=?", (key,)).fetchone()
            if existing:
                if _same_observation(dict(existing), payload):
                    skipped += 1
                else:
                    exceptions.append({"row_number": index, "status": "OPEN",
                                      "error": "Source row changed since the last run. Submit a reviewed correction; the stored observation was not overwritten."})
                continue
            workflow = {"job_id": job_id, "mode": job["mode"], "idempotency_key": key,
                        "extraction_method": f"connector_{job['connector_format']}"}
            observation = core.create_observation(db_path, payload, "system", workflow=workflow)
            core.validate_observation(db_path, observation["id"], "system",
                                      "Automated structural validation. Reviewer approval is still required before publication.")
            created += 1
        except core.FIPError as error:
            exceptions.append({"row_number": index, "status": "OPEN", "error": str(error)})
        except Exception as error:  # preserve the job and route row failures into the review queue
            exceptions.append({"row_number": index, "status": "OPEN", "error": f"Record processing failed: {type(error).__name__}."})

    status = "AWAITING_REVIEW" if created or exceptions else "COMPLETED"
    return _finish_job(db_path, job_id, status=status, records_seen=len(records), created=created,
                       skipped=skipped, exceptions=exceptions, role=role)


def add_manual_observation(db_path: Path, job_id: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    with core._conn(db_path) as db:
        job = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if job is None:
            raise core.FIPError("ingestion job was not found.")
        if job["mode"] != "MANUAL":
            raise core.FIPError("Only MANUAL jobs accept direct manual observation entry.")
        if job["status"] not in {"QUEUED", "AWAITING_REVIEW"}:
            raise core.FIPError("This manual job is closed.")
        if payload.get("source_id") != job["source_id"]:
            raise core.FIPError("The observation source must match the manual job source.")
    workflow = {"job_id": job_id, "mode": "MANUAL", "extraction_method": "manual_transcription",
                "idempotency_key": hashlib.sha256(f"manual\0{job_id}\0{payload.get('entity_id')}\0{payload.get('metric_id')}\0{payload.get('period_id')}\0{payload.get('evidence_reference')}".encode()).hexdigest()}
    observation = core.create_observation(db_path, payload, role, workflow=workflow)
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        before = dict(row)
        updated_at = core.now_utc()
        db.execute("UPDATE ingestion_jobs SET status='AWAITING_REVIEW',observations_created=observations_created+1,"
                   "records_seen=records_seen+1,completed_at=?,last_updated_at=? WHERE id=?", (updated_at, updated_at, job_id))
        after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        core._audit(db, role, "INGESTION_MANUAL_RECORD_ADDED", "ingestion_job", job_id,
                    before=before, after=after, reason=f"Manual observation {observation['id']} entered; reviewer validation and publication are still required.")
    return observation


def correct_hybrid_exception(db_path: Path, job_id: str, row_number: int,
                              payload: dict[str, Any], role: str) -> dict[str, Any]:
    if isinstance(row_number, bool) or not isinstance(row_number, int) or row_number < 1:
        raise core.FIPError("row_number must be a positive integer.")
    with core._conn(db_path) as db:
        row = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise core.FIPError("ingestion job was not found.")
        job = dict(row)
        if job["mode"] != "HYBRID":
            raise core.FIPError("Manual corrections are available only for HYBRID jobs.")
        if job["status"] != "AWAITING_REVIEW":
            raise core.FIPError("Only hybrid jobs awaiting review can be corrected.")
        exceptions = json.loads(job["exceptions_json"])
        issue = next((item for item in exceptions if item["row_number"] == row_number and item["status"] == "OPEN"), None)
        if issue is None:
            raise core.FIPError("No open exception exists for this row number.")
        if payload.get("source_id") != job["source_id"]:
            raise core.FIPError("The correction source must match the hybrid job source.")
    key_material = core._json({"job": job_id, "row": row_number, "payload": payload})
    workflow = {"job_id": job_id, "mode": "HYBRID", "extraction_method": "hybrid_correction",
                "idempotency_key": hashlib.sha256(key_material.encode("utf-8")).hexdigest()}
    observation = core.create_observation(db_path, payload, role, workflow=workflow)
    core.validate_observation(db_path, observation["id"], "system",
                              "Hybrid user correction passed automated structural checks; reviewer approval is still required.")
    with core._conn(db_path) as db:
        current = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        current_exceptions = json.loads(current["exceptions_json"])
        for item in current_exceptions:
            if item["row_number"] == row_number and item["status"] == "OPEN":
                item.update(status="RESOLVED", resolved_at=core.now_utc(), correction_observation_id=observation["id"])
                break
        before = dict(current)
        db.execute("UPDATE ingestion_jobs SET exceptions_json=?,observations_created=observations_created+1,last_updated_at=? WHERE id=?",
                   (core._json(current_exceptions), core.now_utc(), job_id))
        after = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
        core._audit(db, role, "INGESTION_HYBRID_EXCEPTION_CORRECTED", "ingestion_job", job_id,
                    before=before, after=after,
                    reason=f"Row {row_number} resolved with observation {observation['id']}; reviewer approval is still required.")
    return observation


def _queue_due_sources(db_path: Path, now: datetime | None = None, limit: int = 20) -> list[str]:
    now = now or datetime.now(timezone.utc)
    now_text = now.isoformat(timespec="seconds")
    job_ids: list[str] = []
    with core._conn(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        global_mode = _settings_value(db)
        rows = db.execute("SELECT * FROM sources WHERE schedule_interval_minutes IS NOT NULL "
                          "AND next_scheduled_at IS NOT NULL AND next_scheduled_at<=? "
                          "ORDER BY next_scheduled_at LIMIT ?", (now_text, limit)).fetchall()
        for source_row in rows:
            source = dict(source_row)
            mode = source["ingestion_mode"] or global_mode
            if mode not in {"AUTOMATIC", "HYBRID"} or source["connector_format"] == "manual_only" or source["ingestion_config_status"] != "APPROVED":
                continue
            if source["approval_status"] != "APPROVED" or source["permitted_use"] != "public_display":
                continue
            active = db.execute("SELECT 1 FROM ingestion_jobs WHERE source_id=? AND status IN ('QUEUED','RUNNING','AWAITING_REVIEW') LIMIT 1", (source["id"],)).fetchone()
            interval = int(source["schedule_interval_minutes"])
            next_at = (now + timedelta(minutes=interval)).isoformat(timespec="seconds")
            db.execute("UPDATE sources SET next_scheduled_at=? WHERE id=?", (next_at, source["id"]))
            if active:
                continue
            job = {"id": core.new_id("FIP-ING"), "source_id": source["id"], "mode": mode,
                   "mode_override": None, "status": "QUEUED", "trigger_type": "schedule",
                   "requested_by": "scheduler", "created_at": core.now_utc(), "started_at": None,
                   "completed_at": None, "records_seen": 0, "observations_created": 0,
                   "observations_skipped": 0, "exceptions_json": "[]", "error_message": None,
                   "attempt_count": 1,
                   "connector_version": _connector_version(source["connector_format"], source["connector_config_json"]),
                   "last_updated_at": core.now_utc(), "cancelled_at": None}
            db.execute(f"INSERT INTO ingestion_jobs({','.join(job)}) VALUES({','.join('?' for _ in job)})", tuple(job.values()))
            core._audit(db, "scheduler", "INGESTION_JOB_QUEUED", "ingestion_job", job["id"], after=job,
                        reason=f"Scheduled {mode} ingestion job queued for source {source['id']}.")
            job_ids.append(job["id"])
    return job_ids


def run_due_schedules(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    results = []
    for job_id in _queue_due_sources(db_path, limit=limit):
        try:
            results.append(run_job(db_path, job_id, role="scheduler"))
        except core.FIPError as error:
            results.append(_finish_job(db_path, job_id, status="FAILED", error=str(error), role="scheduler"))
    return results


def scheduler_loop(db_path: Path, poll_seconds: int = 60) -> None:
    if poll_seconds < 5:
        raise ValueError("poll_seconds must be at least 5")
    while True:
        try:
            run_due_schedules(db_path)
        except Exception:
            logging.getLogger(__name__).exception("FIP scheduled ingestion cycle failed")
        time.sleep(poll_seconds)
