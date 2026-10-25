"""Persistent evidence, review, publication, and calculation services for FIP.

Manual entry remains the default. Reviewed Automatic and Hybrid ingestion
connectors preserve evidence, validation, audit, and reviewer-publication gates.
All monetary values use Decimal and calculations retain input lineage.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


UNIT_FACTORS = {
    "ones": Decimal("1"), "hundreds": Decimal("100"),
    "thousands": Decimal("1000"), "millions": Decimal("1000000"),
    "billions": Decimal("1000000000"), "trillions": Decimal("1000000000000"), "lakhs": Decimal("100000"),
    "crores": Decimal("10000000"),
}
ENTITY_TYPES = {"company", "government", "public_enterprise", "department", "other"}
SOURCE_CATEGORIES = {"corporate_filing", "government_report", "official_dataset", "registry", "other"}
REUSE_POLICIES = {"public_display", "link_only", "internal_only"}
FX_CONVENTIONS = {"period_average", "period_end", "transaction_date", "budget_rate"}
FRESHNESS_STATES = {"CURRENT", "STALE", "UNKNOWN"}
PERIOD_TYPES = {"annual", "semi_annual", "quarterly", "monthly", "custom"}
INGESTION_MODES = {"MANUAL", "AUTOMATIC", "HYBRID"}
ALIAS_TYPES = {"legal_name", "trading_name", "previous_name", "abbreviation", "registry_name"}
RELATIONSHIP_TYPES = {"parent_subsidiary", "ownership", "control", "government_hierarchy", "funding", "successor"}
FRAMEWORKS = {
    "IFRS": {"name": "International Financial Reporting Standards", "owner": "IFRS Foundation", "versioning": "framework version and effective date required"},
    "US_GAAP": {"name": "United States Generally Accepted Accounting Principles", "owner": "FASB", "versioning": "framework version and effective date required"},
    "IND_AS": {"name": "Indian Accounting Standards", "owner": "Ministry of Corporate Affairs, India", "versioning": "Ind AS applicability and effective date required"},
    "IPSAS": {"name": "International Public Sector Accounting Standards", "owner": "IPSASB", "versioning": "IPSAS edition and effective date required"},
    "NATIONAL": {"name": "National or jurisdictional reporting framework", "owner": "Relevant local standard setter", "versioning": "jurisdiction, version and effective date required"},
    "GOVERNMENT_FISCAL": {"name": "Government fiscal classification and reporting basis", "owner": "Relevant government authority", "versioning": "budget stage, institutional coverage and effective date required"},
}
CORPORATE_METRICS = {
    "revenue": "Revenue", "expenses": "Expenses", "cost_of_revenue": "Cost of revenue",
    "gross_profit": "Gross profit", "ebitda": "EBITDA", "ebit": "EBIT", "operating_profit": "Operating profit",
    "net_income": "Net income", "total_assets": "Total assets", "total_liabilities": "Total liabilities",
    "opening_cash": "Opening cash", "closing_cash": "Closing cash", "fx_effect_cash": "FX effect on cash",
    "other_cash_reconciliation": "Other cash reconciliation", "capital_expenditure": "Capital expenditure",
    "current_assets": "Current assets",
    "current_liabilities": "Current liabilities", "inventory": "Inventory",
    "total_debt": "Total debt", "total_equity": "Total equity",
    "operating_cash_flow": "Operating cash flow", "investing_cash_flow": "Investing cash flow",
    "financing_cash_flow": "Financing cash flow", "interest_expense": "Interest expense",
}
GOVERNMENT_METRICS = {
    "receipts": "Receipts", "tax_revenue": "Tax revenue", "non_tax_revenue": "Non-tax revenue",
    "grants": "Grants", "transfers": "Transfers", "expenditure": "Expenditure", "public_debt": "Public debt",
    "interest_expenditure": "Interest expenditure", "reported_fiscal_balance": "Reported fiscal balance",
    "capital_expenditure": "Capital expenditure", "revenue_expenditure": "Revenue expenditure",
}


class FIPError(ValueError):
    """A user-correctable FIP request or lifecycle error."""


RELEASE_GATE_CATALOG = [
    ("source_governance", "Approved public-source catalogue and reuse rules"),
    ("evidence_capture", "Evidence references and retrieval metadata retained"),
    ("source_freshness", "Source freshness and failure handling tested"),
    ("identity_registry", "Entity matching and relationship history validated"),
    ("ontology_basis", "Financial ontology and reporting-basis mappings approved"),
    ("period_currency_units", "Currency, unit and period controls tested"),
    ("observation_exceptions", "Missing, duplicated and conflicting observations handled"),
    ("corporate_reference", "Corporate reference case passes"),
    ("government_reference", "Government reference case passes"),
    ("formula_tests", "In-scope formulas have approved versions and reference tests"),
    ("calculation_reproduction", "Calculation lineage and historical reproduction demonstrated"),
    ("excel_reconciliation", "Excel delivery reconciles to approved results"),
    ("web_reconciliation", "Web delivery reconciles to approved results"),
    ("powerbi_reconciliation", "Power BI delivery reconciles to approved results"),
    ("security_audit_recovery", "Access control, audit, security and recovery tests pass"),
    ("data_rights_nfr", "Data rights and operational targets reviewed"),
    ("release_approval", "Release evidence and formal approval recorded"),
    ("production_deployment", "Production deployment and operational performance verified"),
]
NFR_CATALOG = [
    ("performance", "Expected response times, report sizes and calculation workload"),
    ("availability", "Service availability target and maintenance expectations"),
    ("freshness", "Acceptable delay by source and metric type"),
    ("recovery", "Recovery time objective and recovery point objective"),
    ("capacity", "Expected entities, observations, documents and concurrent users"),
    ("monitoring", "Ingestion failures, stale data, calculation failures and service health"),
    ("accessibility", "Keyboard support, contrast and accessible reporting"),
    ("security", "Access tests, secret handling, dependency updates and incident procedures"),
    ("data_retention", "Retention by evidence type, permitted use and legal obligations"),
    ("maintainability", "Versioned configurations, documented interfaces and automated tests"),
]
FORMULA_CATALOG_VERSION = "1.1.0"
FORMULA_DEFINITIONS = {
    "revenue_growth_percent": {"formula_id": "revenue_growth", "domain": "corporate", "formula": "(current revenue - prior revenue) / prior revenue × 100", "required_inputs": ["revenue", "prior revenue"], "denominator_rule": "prior revenue must be positive"},
    "revenue_change": {"formula_id": "revenue_change", "domain": "corporate", "formula": "current revenue - prior revenue", "required_inputs": ["revenue", "prior revenue"]},
    "gross_margin_percent": {"formula_id": "gross_margin", "domain": "corporate", "formula": "gross profit / revenue × 100", "required_inputs": ["gross_profit", "revenue"], "denominator_rule": "revenue must be non-zero"},
    "ebitda_margin_percent": {"formula_id": "ebitda_margin", "domain": "corporate", "formula": "EBITDA / revenue × 100", "required_inputs": ["ebitda", "revenue"], "denominator_rule": "revenue must be non-zero"},
    "ebit_margin_percent": {"formula_id": "ebit_margin", "domain": "corporate", "formula": "EBIT / revenue × 100", "required_inputs": ["ebit", "revenue"], "denominator_rule": "revenue must be non-zero"},
    "operating_margin_percent": {"formula_id": "operating_margin", "domain": "corporate", "formula": "operating profit / revenue × 100", "required_inputs": ["operating_profit", "revenue"], "denominator_rule": "revenue must be non-zero"},
    "net_margin_percent": {"formula_id": "net_margin", "domain": "corporate", "formula": "net income / revenue × 100", "required_inputs": ["net_income", "revenue"], "denominator_rule": "revenue must be non-zero"},
    "working_capital": {"formula_id": "working_capital", "domain": "corporate", "formula": "current assets - current liabilities", "required_inputs": ["current_assets", "current_liabilities"]},
    "current_ratio": {"formula_id": "current_ratio", "domain": "corporate", "formula": "current assets / current liabilities", "required_inputs": ["current_assets", "current_liabilities"], "denominator_rule": "current liabilities must be non-zero"},
    "quick_ratio": {"formula_id": "quick_ratio", "domain": "corporate", "formula": "(current assets - inventory) / current liabilities", "required_inputs": ["current_assets", "inventory", "current_liabilities"], "denominator_rule": "current liabilities must be non-zero"},
    "debt_to_equity_ratio": {"formula_id": "debt_to_equity", "domain": "corporate", "formula": "total debt / total equity", "required_inputs": ["total_debt", "total_equity"], "denominator_rule": "total equity must be non-zero"},
    "return_on_assets_percent": {"formula_id": "return_on_assets_period_end", "domain": "corporate", "formula": "net income / period-end total assets × 100", "required_inputs": ["net_income", "total_assets"], "denominator_rule": "period-end total assets must be non-zero"},
    "return_on_equity_percent": {"formula_id": "return_on_equity_period_end", "domain": "corporate", "formula": "net income / period-end total equity × 100", "required_inputs": ["net_income", "total_equity"], "denominator_rule": "period-end total equity must be non-zero"},
    "cash_ratio": {"formula_id": "cash_ratio", "domain": "corporate", "formula": "period-end cash and cash equivalents / current liabilities", "required_inputs": ["closing_cash", "current_liabilities"], "denominator_rule": "current liabilities must be non-zero"},
    "net_debt": {"formula_id": "net_debt", "domain": "corporate", "formula": "total debt - period-end cash and cash equivalents", "required_inputs": ["total_debt", "closing_cash"]},
    "interest_coverage_ratio": {"formula_id": "interest_coverage", "domain": "corporate", "formula": "EBIT / interest expense", "required_inputs": ["ebit", "interest_expense"], "denominator_rule": "interest expense must be non-zero"},
    "free_cash_flow": {"formula_id": "free_cash_flow", "domain": "corporate", "formula": "operating cash flow - capital expenditure", "required_inputs": ["operating_cash_flow", "capital_expenditure"], "limitation": "Capital expenditure is treated as a positive amount for this formula; confirm source sign and mapping."},
    "operating_cash_flow_to_net_income_ratio": {"formula_id": "ocf_to_net_income", "domain": "corporate", "formula": "operating cash flow / net income", "required_inputs": ["operating_cash_flow", "net_income"], "denominator_rule": "net income must be non-zero"},
    "cash_flow_category_subtotal": {"formula_id": "cash_flow_category_subtotal", "domain": "corporate", "formula": "operating + investing + financing cash flows", "required_inputs": ["operating_cash_flow", "investing_cash_flow", "financing_cash_flow"], "limitation": "Not necessarily net change in cash; FX and other reconciliation items may be absent."},
    "net_budget_position": {"formula_id": "net_budget_position", "domain": "government", "formula": "receipts - expenditure", "required_inputs": ["receipts", "expenditure"], "limitation": "Descriptive only; not necessarily an official fiscal balance or deficit."},
    "receipts_change": {"formula_id": "receipts_change", "domain": "government", "formula": "current receipts - prior receipts", "required_inputs": ["receipts", "prior receipts"]},
    "receipts_growth_percent": {"formula_id": "receipts_growth", "domain": "government", "formula": "(current receipts - prior receipts) / prior receipts × 100", "required_inputs": ["receipts", "prior receipts"], "denominator_rule": "prior receipts must be positive"},
    "expenditure_change": {"formula_id": "expenditure_change", "domain": "government", "formula": "current expenditure - prior expenditure", "required_inputs": ["expenditure", "prior expenditure"]},
    "expenditure_growth_percent": {"formula_id": "expenditure_growth", "domain": "government", "formula": "(current expenditure - prior expenditure) / prior expenditure × 100", "required_inputs": ["expenditure", "prior expenditure"], "denominator_rule": "prior expenditure must be positive"},
    "public_debt_change": {"formula_id": "public_debt_change", "domain": "government", "formula": "current public debt - prior public debt", "required_inputs": ["public_debt", "prior public_debt"]},
    "public_debt_growth_percent": {"formula_id": "public_debt_growth", "domain": "government", "formula": "(current public debt - prior public debt) / prior public debt × 100", "required_inputs": ["public_debt", "prior public_debt"], "denominator_rule": "prior public debt must be positive"},
    "interest_to_receipts_percent": {"formula_id": "interest_to_receipts", "domain": "government", "formula": "interest expenditure / receipts × 100", "required_inputs": ["interest_expenditure", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "public_debt_to_receipts_ratio": {"formula_id": "debt_to_receipts", "domain": "government", "formula": "public debt / receipts", "required_inputs": ["public_debt", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "tax_revenue_share_percent": {"formula_id": "tax_revenue_share", "domain": "government", "formula": "tax revenue / receipts × 100", "required_inputs": ["tax_revenue", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "non_tax_revenue_share_percent": {"formula_id": "non_tax_revenue_share", "domain": "government", "formula": "non-tax revenue / receipts × 100", "required_inputs": ["non_tax_revenue", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "grants_share_percent": {"formula_id": "grants_share", "domain": "government", "formula": "grants / receipts × 100", "required_inputs": ["grants", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "transfers_share_percent": {"formula_id": "transfers_share", "domain": "government", "formula": "transfers / receipts × 100", "required_inputs": ["transfers", "receipts"], "denominator_rule": "receipts must be non-zero"},
    "capital_expenditure_share_percent": {"formula_id": "capex_share", "domain": "government", "formula": "capital expenditure / expenditure × 100", "required_inputs": ["capital_expenditure", "expenditure"], "denominator_rule": "expenditure must be non-zero"},
    "revenue_expenditure_share_percent": {"formula_id": "revenue_expenditure_share", "domain": "government", "formula": "revenue expenditure / expenditure × 100", "required_inputs": ["revenue_expenditure", "expenditure"], "denominator_rule": "expenditure must be non-zero"},
}
METRIC_DEFINITIONS = {
    "corporate": {key: {"label": value, "unit_type": "monetary", "comparison_basis": "framework and reporting basis required",
                         "sign_convention": "Preserve the reported sign; never invert silently.",
                         "aggregation_rule": "Aggregate additive amounts only with compatible entity, period, basis and currency; ratios are not additive.",
                         "ontology_version": FORMULA_CATALOG_VERSION}
                   for key, value in CORPORATE_METRICS.items()},
    "government": {key: {"label": value, "unit_type": "monetary", "comparison_basis": "fiscal scope, budget stage, framework and reporting basis required",
                         "sign_convention": "Preserve the reported sign; fiscal balance definitions remain jurisdiction-specific.",
                         "aggregation_rule": "Aggregate only with matching government coverage, fiscal period, budget stage, basis and currency.",
                         "ontology_version": FORMULA_CATALOG_VERSION}
                    for key, value in GOVERNMENT_METRICS.items()},
}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16].upper()}"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _seed_registers(connection: sqlite3.Connection) -> None:
    for gate_id, label in RELEASE_GATE_CATALOG:
        connection.execute("INSERT OR IGNORE INTO release_gates(gate_id,label,status) VALUES(?,?,'PENDING')", (gate_id, label))
    for requirement_id, description in NFR_CATALOG:
        connection.execute("INSERT OR IGNORE INTO nfr_register(requirement_id,category,description,status) VALUES(?,?,?,'PENDING')",
                           (requirement_id, requirement_id, description))


def _required_text(value: Any, field: str, maximum: int = 300) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FIPError(f"{field} is required.")
    value = value.strip()
    if len(value) > maximum:
        raise FIPError(f"{field} must be {maximum} characters or fewer.")
    return value


def _optional_text(value: Any, field: str, maximum: int = 300) -> str | None:
    if value is None or value == "":
        return None
    return _required_text(value, field, maximum)


def _date(value: Any, field: str) -> str:
    try:
        return date.fromisoformat(_required_text(value, field, 10)).isoformat()
    except FIPError:
        raise
    except ValueError:
        raise FIPError(f"{field} must use YYYY-MM-DD format.") from None


def _amount(value: Any) -> Decimal:
    if isinstance(value, bool) or value is None or isinstance(value, float):
        raise FIPError("value must be supplied as a decimal string.")
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError):
        raise FIPError("value must be a valid decimal string.") from None
    if not amount.is_finite() or abs(amount) > Decimal("1e24"):
        raise FIPError("value must be finite and within ±1e24.")
    return amount


def _conn(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY, entity_type TEXT NOT NULL, canonical_name TEXT NOT NULL,
            legal_name TEXT, jurisdiction_code TEXT NOT NULL, external_id TEXT,
            review_status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entity_aliases (
            id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), alias TEXT NOT NULL,
            alias_type TEXT NOT NULL, source_id TEXT NOT NULL REFERENCES sources(id),
            valid_from TEXT, valid_to TEXT, review_status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sources (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, publisher TEXT NOT NULL,
            category TEXT NOT NULL, jurisdiction_code TEXT NOT NULL, url TEXT NOT NULL,
            publication_date TEXT, retrieval_method TEXT NOT NULL, expected_frequency TEXT NOT NULL,
            license_name TEXT NOT NULL, permitted_use TEXT NOT NULL, retention_rule TEXT NOT NULL,
            approval_status TEXT NOT NULL, created_at TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT '', access_requirements TEXT NOT NULL DEFAULT '',
            technical_limits TEXT NOT NULL DEFAULT '', failure_policy TEXT NOT NULL DEFAULT '',
            ingestion_mode TEXT, connector_format TEXT NOT NULL DEFAULT 'manual_only',
            connector_config_json TEXT NOT NULL DEFAULT '{}', schedule_interval_minutes INTEGER,
            next_scheduled_at TEXT, ingestion_config_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
            ingestion_config_approved_by TEXT, ingestion_config_approved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS fip_settings (
            setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL,
            updated_by TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ingestion_jobs (
            id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(id),
            mode TEXT NOT NULL CHECK(mode IN ('MANUAL','AUTOMATIC','HYBRID')),
            mode_override TEXT CHECK(mode_override IS NULL OR mode_override IN ('MANUAL','AUTOMATIC','HYBRID')),
            status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','AWAITING_REVIEW','COMPLETED','FAILED')),
            trigger_type TEXT NOT NULL, requested_by TEXT NOT NULL, created_at TEXT NOT NULL,
            started_at TEXT, completed_at TEXT, records_seen INTEGER NOT NULL DEFAULT 0,
            observations_created INTEGER NOT NULL DEFAULT 0, observations_skipped INTEGER NOT NULL DEFAULT 0,
            exceptions_json TEXT NOT NULL DEFAULT '[]', error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS fx_rates (
            id TEXT PRIMARY KEY, source_currency TEXT NOT NULL, target_currency TEXT NOT NULL,
            rate_decimal TEXT NOT NULL, period_id TEXT NOT NULL, conversion_convention TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES sources(id), status TEXT NOT NULL,
            created_at TEXT NOT NULL, approved_at TEXT
        );
        CREATE TABLE IF NOT EXISTS entity_relationships (
            id TEXT PRIMARY KEY, from_entity_id TEXT NOT NULL REFERENCES entities(id),
            to_entity_id TEXT NOT NULL REFERENCES entities(id), relationship_type TEXT NOT NULL,
            source_id TEXT NOT NULL REFERENCES sources(id), valid_from TEXT, valid_to TEXT,
            review_status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metric_mappings (
            id TEXT PRIMARY KEY, domain TEXT NOT NULL, reported_label TEXT NOT NULL,
            metric_id TEXT NOT NULL, framework_id TEXT NOT NULL, framework_version TEXT NOT NULL,
            jurisdiction_code TEXT NOT NULL, valid_from TEXT, valid_to TEXT,
            source_id TEXT NOT NULL REFERENCES sources(id), definition_notes TEXT NOT NULL,
            review_status TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id),
            source_id TEXT NOT NULL REFERENCES sources(id), domain TEXT NOT NULL,
            metric_id TEXT NOT NULL, reported_label TEXT NOT NULL, value_decimal TEXT NOT NULL,
            standardized_value_decimal TEXT NOT NULL, standardized_currency TEXT NOT NULL,
            fx_rate_id TEXT, conversion_convention TEXT,
            currency TEXT NOT NULL, unit TEXT NOT NULL, period_id TEXT NOT NULL,
            period_start TEXT NOT NULL, period_end TEXT NOT NULL, period_type TEXT NOT NULL,
            reporting_basis TEXT NOT NULL, framework_id TEXT NOT NULL, framework_version TEXT NOT NULL,
            fiscal_scope TEXT, budget_stage TEXT, freshness_status TEXT NOT NULL DEFAULT 'UNKNOWN',
            evidence_reference TEXT NOT NULL, extraction_method TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1, supersedes_id TEXT, status TEXT NOT NULL,
            validation_message TEXT, validation_json TEXT, created_at TEXT NOT NULL,
            ingestion_job_id TEXT REFERENCES ingestion_jobs(id), ingestion_mode TEXT,
            idempotency_key TEXT
        );
        CREATE TABLE IF NOT EXISTS calculation_runs (
            id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id),
            domain TEXT NOT NULL, period_id TEXT NOT NULL, prior_period_id TEXT,
            status TEXT NOT NULL, formula_catalog_version TEXT NOT NULL,
            result_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS calculation_inputs (
            calculation_id TEXT NOT NULL REFERENCES calculation_runs(id),
            observation_id TEXT NOT NULL REFERENCES observations(id),
            observation_version INTEGER NOT NULL,
            PRIMARY KEY(calculation_id, observation_id)
        );
        CREATE TABLE IF NOT EXISTS reconciliation_runs (
            id TEXT PRIMARY KEY, entity_id TEXT NOT NULL REFERENCES entities(id), domain TEXT NOT NULL,
            period_id TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS publication_releases (
            id TEXT PRIMARY KEY, release_number INTEGER NOT NULL UNIQUE, version TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
            notes TEXT NOT NULL, snapshot_json TEXT NOT NULL, approved_at TEXT, approval_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS release_gates (
            gate_id TEXT PRIMARY KEY, label TEXT NOT NULL, status TEXT NOT NULL,
            evidence_reference TEXT, updated_at TEXT, updated_by TEXT
        );
        CREATE TABLE IF NOT EXISTS nfr_register (
            requirement_id TEXT PRIMARY KEY, category TEXT NOT NULL, description TEXT NOT NULL,
            target TEXT, status TEXT NOT NULL, evidence_reference TEXT, updated_at TEXT, updated_by TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, actor_role TEXT NOT NULL,
            action TEXT NOT NULL, record_type TEXT NOT NULL, record_id TEXT NOT NULL,
            before_json TEXT, after_json TEXT, created_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS observations_lookup
            ON observations(entity_id, domain, period_id, metric_id, status);
        CREATE INDEX IF NOT EXISTS audit_record_lookup
            ON audit_events(record_type, record_id, id);
    """)
    source_columns = {row["name"] for row in connection.execute("PRAGMA table_info(sources)")}
    for column, declaration in (
        ("owner", "TEXT NOT NULL DEFAULT ''"), ("access_requirements", "TEXT NOT NULL DEFAULT ''"),
        ("technical_limits", "TEXT NOT NULL DEFAULT ''"), ("failure_policy", "TEXT NOT NULL DEFAULT ''"),
        ("ingestion_mode", "TEXT"), ("connector_format", "TEXT NOT NULL DEFAULT 'manual_only'"),
        ("connector_config_json", "TEXT NOT NULL DEFAULT '{}'"), ("schedule_interval_minutes", "INTEGER"),
        ("next_scheduled_at", "TEXT"), ("ingestion_config_status", "TEXT NOT NULL DEFAULT 'PENDING_REVIEW'"),
        ("ingestion_config_approved_by", "TEXT"), ("ingestion_config_approved_at", "TEXT"),
    ):
        if column not in source_columns:
            connection.execute(f"ALTER TABLE sources ADD COLUMN {column} {declaration}")
    observation_columns = {row["name"] for row in connection.execute("PRAGMA table_info(observations)")}
    if "supersedes_id" not in observation_columns:
        connection.execute("ALTER TABLE observations ADD COLUMN supersedes_id TEXT")
    for column, declaration in (("standardized_value_decimal", "TEXT"), ("standardized_currency", "TEXT"),
                                ("fx_rate_id", "TEXT"), ("conversion_convention", "TEXT"),
                                ("framework_id", "TEXT"), ("framework_version", "TEXT"),
                                ("freshness_status", "TEXT"), ("validation_json", "TEXT")):
        if column not in observation_columns:
            connection.execute(f"ALTER TABLE observations ADD COLUMN {column} {declaration}")
    for column, declaration in (("ingestion_job_id", "TEXT REFERENCES ingestion_jobs(id)"),
                                ("ingestion_mode", "TEXT"), ("idempotency_key", "TEXT")):
        if column not in observation_columns:
            connection.execute(f"ALTER TABLE observations ADD COLUMN {column} {declaration}")
    for row in connection.execute("SELECT id,value_decimal,currency,unit FROM observations WHERE standardized_value_decimal IS NULL OR standardized_currency IS NULL"):
        normalized = str(Decimal(row["value_decimal"]) * UNIT_FACTORS[row["unit"]])
        connection.execute("UPDATE observations SET standardized_value_decimal=?,standardized_currency=? WHERE id=?",
                           (normalized, row["currency"], row["id"]))
    connection.execute("UPDATE observations SET framework_id=COALESCE(framework_id,'NATIONAL'), framework_version=COALESCE(framework_version,'unspecified'), freshness_status=COALESCE(freshness_status,'UNKNOWN')")
    audit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(audit_events)")}
    if "reason" not in audit_columns:
        connection.execute("ALTER TABLE audit_events ADD COLUMN reason TEXT NOT NULL DEFAULT ''")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS observations_idempotency ON observations(idempotency_key) WHERE idempotency_key IS NOT NULL")
    connection.execute("CREATE INDEX IF NOT EXISTS ingestion_jobs_status ON ingestion_jobs(status,created_at)")
    connection.execute("INSERT OR IGNORE INTO fip_settings(setting_key,setting_value,updated_by,updated_at) VALUES('global_ingestion_mode','MANUAL','system',?)", (now_utc(),))
    _seed_registers(connection)
    connection.commit()
    return connection


def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _audit(db: sqlite3.Connection, role: str, action: str, kind: str, record_id: str,
           before: Any = None, after: Any = None, reason: str | None = None) -> None:
    rationale = reason or action.replace("_", " ").lower()
    db.execute("INSERT INTO audit_events(actor_role,action,record_type,record_id,before_json,after_json,created_at,reason) VALUES(?,?,?,?,?,?,?,?)",
               (role, action, kind, record_id, _json(before) if before is not None else None,
                _json(after) if after is not None else None, now_utc(), rationale))


def list_entities(db_path: Path) -> list[dict[str, Any]]:
    with _conn(db_path) as db:
        return [dict(row) for row in db.execute("SELECT * FROM entities ORDER BY canonical_name")]


def list_publications(db_path: Path, entity_id: str | None = None, period_id: str | None = None,
                     release_id: str | None = None) -> dict[str, Any]:
    with _conn(db_path) as db:
        if release_id:
            release = db.execute("SELECT * FROM publication_releases WHERE status='APPROVED' AND id=?", (release_id,)).fetchone()
        else:
            release = db.execute("SELECT * FROM publication_releases WHERE status='APPROVED' ORDER BY release_number DESC LIMIT 1").fetchone()
    if release is None:
        return {"release": None, "observations": [], "calculations": []}
    snapshot = json.loads(release["snapshot_json"])
    observations = snapshot["observations"]
    calculations = snapshot["calculations"]
    if entity_id:
        observations = [item for item in observations if item["entity_id"] == entity_id]
        calculations = [item for item in calculations if item["entity_id"] == entity_id]
    if period_id:
        observations = [item for item in observations if item["period_id"] == period_id]
        calculations = [item for item in calculations if item["period_id"] == period_id]
    release_record = {key: release[key] for key in ("id", "release_number", "version", "status", "created_at", "created_by", "approved_at", "approval_reason", "notes")}
    release_record["formula_catalog_version"] = snapshot.get("formula_catalog_version", FORMULA_CATALOG_VERSION)
    used_fx_ids = {item["fx_rate_id"] for item in observations if item.get("fx_rate_id")}
    fx_rates = [item for item in snapshot.get("fx_rates", []) if item["id"] in used_fx_ids]
    return {"release": release_record, "observations": observations, "calculations": calculations,
            "fx_rates": fx_rates}


def summary(db_path: Path) -> dict[str, int]:
    with _conn(db_path) as db:
        return {
            "entities": db.execute("SELECT COUNT(*) FROM entities").fetchone()[0],
            "sources": db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "observations": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "awaiting_review": db.execute("SELECT COUNT(*) FROM observations WHERE status IN ('INGESTED','VALIDATED','APPROVED')").fetchone()[0],
            "published_observations": db.execute("SELECT COUNT(*) FROM observations WHERE status='PUBLISHED'").fetchone()[0],
            "fx_rates": db.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0],
            "approved_releases": db.execute("SELECT COUNT(*) FROM publication_releases WHERE status='APPROVED'").fetchone()[0],
        }


def staff_workspace(db_path: Path) -> dict[str, Any]:
    with _conn(db_path) as db:
        entities = [dict(row) for row in db.execute("SELECT * FROM entities ORDER BY created_at DESC")]
        aliases = [dict(row) for row in db.execute("SELECT a.*,e.canonical_name FROM entity_aliases a JOIN entities e ON e.id=a.entity_id ORDER BY a.created_at DESC")]
        relationships = [dict(row) for row in db.execute("""SELECT r.*,a.canonical_name AS from_name,b.canonical_name AS to_name
                                                          FROM entity_relationships r JOIN entities a ON a.id=r.from_entity_id
                                                          JOIN entities b ON b.id=r.to_entity_id ORDER BY r.created_at DESC""")]
        metric_mappings = [dict(row) for row in db.execute("SELECT * FROM metric_mappings ORDER BY created_at DESC")]
        sources = [dict(row) for row in db.execute("SELECT * FROM sources ORDER BY created_at DESC")]
        fx_rates = [dict(row) for row in db.execute("""SELECT f.*,s.title AS source_title FROM fx_rates f
                                                       JOIN sources s ON s.id=f.source_id ORDER BY f.created_at DESC""")]
        observations = [dict(row) for row in db.execute("SELECT o.*,e.canonical_name,s.title AS source_title FROM observations o JOIN entities e ON e.id=o.entity_id JOIN sources s ON s.id=o.source_id ORDER BY o.created_at DESC")]
        calculations = []
        for row in db.execute("SELECT c.*,e.canonical_name FROM calculation_runs c JOIN entities e ON e.id=c.entity_id ORDER BY c.created_at DESC"):
            item = dict(row); item["result"] = json.loads(item.pop("result_json")); calculations.append(item)
        reconciliation_runs = []
        for row in db.execute("SELECT * FROM reconciliation_runs ORDER BY created_at DESC"):
            item = dict(row); item["result"] = json.loads(item.pop("result_json")); reconciliation_runs.append(item)
        releases = []
        for row in db.execute("SELECT id,release_number,version,status,created_at,created_by,notes,approved_at,approval_reason FROM publication_releases ORDER BY release_number DESC"):
            releases.append(dict(row))
        release_gates = [dict(row) for row in db.execute("SELECT * FROM release_gates ORDER BY gate_id")]
        nfr_register = [dict(row) for row in db.execute("SELECT * FROM nfr_register ORDER BY category")]
        audit = [dict(row) for row in db.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 80")]
        ingestion_jobs = [dict(row) for row in db.execute("SELECT * FROM ingestion_jobs ORDER BY created_at DESC LIMIT 50")]
        ingestion_settings = db.execute("SELECT setting_value,updated_by,updated_at FROM fip_settings WHERE setting_key='global_ingestion_mode'").fetchone()
    return {"entities": entities, "aliases": aliases, "relationships": relationships,
            "metric_mappings": metric_mappings, "sources": sources, "fx_rates": fx_rates,
            "observations": observations, "calculations": calculations,
            "reconciliation_runs": reconciliation_runs, "releases": releases,
        "release_gates": release_gates, "nfr_register": nfr_register, "audit_events": audit,
        "ingestion_jobs": ingestion_jobs, "global_ingestion_mode": ingestion_settings["setting_value"] if ingestion_settings else "MANUAL"}


def create_entity(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    entity_type = payload.get("entity_type")
    if not isinstance(entity_type, str) or entity_type not in ENTITY_TYPES:
        raise FIPError("entity_type is not supported.")
    name = _required_text(payload.get("canonical_name"), "canonical_name")
    jurisdiction = _required_text(payload.get("jurisdiction_code"), "jurisdiction_code", 10).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,9}", jurisdiction):
        raise FIPError("jurisdiction_code must be a short country or administrative code.")
    external_id = _optional_text(payload.get("external_id"), "external_id", 100)
    record = {"id": new_id("FIP-" + ("CORP" if entity_type in {"company", "public_enterprise"} else "GOV")),
              "entity_type": entity_type, "canonical_name": name,
              "legal_name": _optional_text(payload.get("legal_name"), "legal_name"),
              "jurisdiction_code": jurisdiction, "external_id": external_id,
              "review_status": "NEEDS_REVIEW", "created_at": now_utc()}
    with _conn(db_path) as db:
        db.execute("INSERT INTO entities VALUES(?,?,?,?,?,?,?,?)", tuple(record.values()))
        _audit(db, role, "ENTITY_CREATED", "entity", record["id"], after=record)
    return record


def _validity_dates(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    valid_from = _date(payload.get("valid_from"), "valid_from") if payload.get("valid_from") else None
    valid_to = _date(payload.get("valid_to"), "valid_to") if payload.get("valid_to") else None
    if valid_from and valid_to and valid_to < valid_from:
        raise FIPError("valid_to must be on or after valid_from.")
    return valid_from, valid_to


def create_entity_alias(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    alias_type = payload.get("alias_type")
    if not isinstance(alias_type, str) or alias_type not in ALIAS_TYPES:
        raise FIPError("alias_type is not supported.")
    valid_from, valid_to = _validity_dates(payload)
    record = {"id": new_id("FIP-ALIAS"),
              "entity_id": _required_text(payload.get("entity_id"), "entity_id", 40),
              "alias": _required_text(payload.get("alias"), "alias", 300),
              "alias_type": alias_type,
              "source_id": _required_text(payload.get("source_id"), "source_id", 40),
              "valid_from": valid_from, "valid_to": valid_to,
              "review_status": "NEEDS_REVIEW", "created_at": now_utc()}
    with _conn(db_path) as db:
        if db.execute("SELECT 1 FROM entities WHERE id=?", (record["entity_id"],)).fetchone() is None:
            raise FIPError("entity_id was not found.")
        if db.execute("SELECT 1 FROM sources WHERE id=?", (record["source_id"],)).fetchone() is None:
            raise FIPError("source_id was not found.")
        db.execute("INSERT INTO entity_aliases VALUES(?,?,?,?,?,?,?,?,?)", tuple(record.values()))
        _audit(db, role, "ENTITY_ALIAS_ADDED", "entity_alias", record["id"], after=record)
    return record


def create_entity_relationship(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    relationship_type = payload.get("relationship_type")
    if not isinstance(relationship_type, str) or relationship_type not in RELATIONSHIP_TYPES:
        raise FIPError("relationship_type is not supported.")
    valid_from, valid_to = _validity_dates(payload)
    record = {"id": new_id("FIP-RELN"),
              "from_entity_id": _required_text(payload.get("from_entity_id"), "from_entity_id", 40),
              "to_entity_id": _required_text(payload.get("to_entity_id"), "to_entity_id", 40),
              "relationship_type": relationship_type,
              "source_id": _required_text(payload.get("source_id"), "source_id", 40),
              "valid_from": valid_from, "valid_to": valid_to,
              "review_status": "NEEDS_REVIEW", "created_at": now_utc()}
    if record["from_entity_id"] == record["to_entity_id"]:
        raise FIPError("An entity cannot be related to itself.")
    with _conn(db_path) as db:
        for entity_id in (record["from_entity_id"], record["to_entity_id"]):
            if db.execute("SELECT 1 FROM entities WHERE id=?", (entity_id,)).fetchone() is None:
                raise FIPError("Both relationship entities must already exist.")
        if db.execute("SELECT 1 FROM sources WHERE id=?", (record["source_id"],)).fetchone() is None:
            raise FIPError("source_id was not found.")
        if relationship_type in {"parent_subsidiary", "government_hierarchy"}:
            cycle = db.execute("""WITH RECURSIVE descendants(entity_id) AS (
                                   SELECT to_entity_id FROM entity_relationships
                                    WHERE from_entity_id=? AND relationship_type=? AND review_status='VERIFIED'
                                   UNION SELECT r.to_entity_id FROM entity_relationships r
                                    JOIN descendants d ON r.from_entity_id=d.entity_id
                                    WHERE r.relationship_type=? AND r.review_status='VERIFIED'
                                 ) SELECT 1 FROM descendants WHERE entity_id=? LIMIT 1""",
                               (record["to_entity_id"], relationship_type, relationship_type, record["from_entity_id"])).fetchone()
            if cycle:
                raise FIPError("This hierarchy relationship would create a cycle.")
        db.execute("INSERT INTO entity_relationships VALUES(?,?,?,?,?,?,?,?,?)", tuple(record.values()))
        _audit(db, role, "ENTITY_RELATIONSHIP_ADDED", "entity_relationship", record["id"], after=record)
    return record


def create_metric_mapping(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    domain = payload.get("domain")
    metrics = CORPORATE_METRICS if domain == "corporate" else GOVERNMENT_METRICS if domain == "government" else {}
    metric_id = payload.get("metric_id")
    if not isinstance(metric_id, str) or metric_id not in metrics:
        raise FIPError("metric_id must be in the selected domain's Release 1 metric catalogue.")
    framework = payload.get("framework_id")
    if not isinstance(framework, str) or framework not in FRAMEWORKS:
        raise FIPError("framework_id is not in the framework registry.")
    jurisdiction = _required_text(payload.get("jurisdiction_code"), "jurisdiction_code", 10).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,9}", jurisdiction):
        raise FIPError("jurisdiction_code must be a short country or administrative code.")
    valid_from, valid_to = _validity_dates(payload)
    record = {"id": new_id("FIP-MAP"), "domain": domain,
              "reported_label": _required_text(payload.get("reported_label"), "reported_label", 150),
              "metric_id": metric_id, "framework_id": framework,
              "framework_version": _required_text(payload.get("framework_version"), "framework_version", 60),
              "jurisdiction_code": jurisdiction, "valid_from": valid_from, "valid_to": valid_to,
              "source_id": _required_text(payload.get("source_id"), "source_id", 40),
              "definition_notes": _required_text(payload.get("definition_notes"), "definition_notes", 1000),
              "review_status": "NEEDS_REVIEW", "created_at": now_utc()}
    with _conn(db_path) as db:
        if db.execute("SELECT 1 FROM sources WHERE id=?", (record["source_id"],)).fetchone() is None:
            raise FIPError("source_id was not found.")
        db.execute("INSERT INTO metric_mappings VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", tuple(record.values()))
        _audit(db, role, "METRIC_MAPPING_PROPOSED", "metric_mapping", record["id"], after=record)
    return record


def review_governance_record(db_path: Path, table: str, record_id: str, reason: str, role: str) -> dict[str, Any]:
    tables = {"entity_aliases": ("entity_alias", "source_id"),
              "entity_relationships": ("entity_relationship", "source_id"),
              "metric_mappings": ("metric_mapping", "source_id")}
    if table not in tables:
        raise FIPError("Unknown governance record type.")
    reason = _required_text(reason, "decision_reason", 1000)
    with _conn(db_path) as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise FIPError("Governance record was not found.")
        before = dict(row)
        if before["review_status"] != "NEEDS_REVIEW":
            raise FIPError("Only records needing review can be verified.")
        source_id = before[tables[table][1]]
        source = db.execute("SELECT approval_status FROM sources WHERE id=?", (source_id,)).fetchone()
        if source is None or source["approval_status"] != "APPROVED":
            raise FIPError("The supporting source must be approved before this mapping or relationship can be verified.")
        if table == "entity_aliases":
            entity = db.execute("SELECT review_status FROM entities WHERE id=?", (before["entity_id"],)).fetchone()
            if entity is None or entity["review_status"] != "VERIFIED":
                raise FIPError("Entity identity must be verified before its alias can be approved.")
        if table == "entity_relationships":
            entity_states = [db.execute("SELECT review_status FROM entities WHERE id=?", (before[key],)).fetchone()
                             for key in ("from_entity_id", "to_entity_id")]
            if any(state is None or state["review_status"] != "VERIFIED" for state in entity_states):
                raise FIPError("Both entity identities must be verified before their relationship can be approved.")
            if before["relationship_type"] in {"parent_subsidiary", "government_hierarchy"}:
                cycle = db.execute("""WITH RECURSIVE descendants(entity_id) AS (
                                       SELECT to_entity_id FROM entity_relationships
                                        WHERE from_entity_id=? AND relationship_type=? AND review_status='VERIFIED'
                                       UNION SELECT r.to_entity_id FROM entity_relationships r
                                        JOIN descendants d ON r.from_entity_id=d.entity_id
                                        WHERE r.relationship_type=? AND r.review_status='VERIFIED'
                                     ) SELECT 1 FROM descendants WHERE entity_id=? LIMIT 1""",
                                   (before["to_entity_id"], before["relationship_type"],
                                    before["relationship_type"], before["from_entity_id"])).fetchone()
                if cycle:
                    raise FIPError("This hierarchy relationship would create a cycle.")
        db.execute(f"UPDATE {table} SET review_status='VERIFIED' WHERE id=?", (record_id,))
        after = dict(db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone())
        _audit(db, role, f"{tables[table][0].upper()}_VERIFIED", tables[table][0], record_id,
               before=before, after=after, reason=reason)
        return after


def create_source(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    category, use = payload.get("category"), payload.get("permitted_use")
    if not isinstance(category, str) or category not in SOURCE_CATEGORIES or not isinstance(use, str) or use not in REUSE_POLICIES:
        raise FIPError("category or permitted_use is not supported.")
    url = _required_text(payload.get("url"), "url", 2000)
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise FIPError("url must be HTTPS and must not contain embedded credentials.")
    publication_date = payload.get("publication_date")
    if publication_date:
        publication_date = _date(publication_date, "publication_date")
    jurisdiction = _required_text(payload.get("jurisdiction_code"), "jurisdiction_code", 10).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9-]{1,9}", jurisdiction):
        raise FIPError("jurisdiction_code must be a short country or administrative code.")
    record = {
        "id": new_id("FIP-SRC"), "title": _required_text(payload.get("title"), "title"),
        "publisher": _required_text(payload.get("publisher"), "publisher"), "category": category,
        "jurisdiction_code": jurisdiction,
        "url": url, "publication_date": publication_date,
        "retrieval_method": _required_text(payload.get("retrieval_method"), "retrieval_method", 80),
        "expected_frequency": _required_text(payload.get("expected_frequency"), "expected_frequency", 80),
        "license_name": _required_text(payload.get("license_name"), "license_name", 200),
        "permitted_use": use, "retention_rule": _required_text(payload.get("retention_rule"), "retention_rule", 300),
        "approval_status": "PENDING_REVIEW", "created_at": now_utc(),
        "owner": _required_text(payload.get("owner"), "owner", 200),
        "access_requirements": _required_text(payload.get("access_requirements"), "access_requirements", 500),
        "technical_limits": _required_text(payload.get("technical_limits"), "technical_limits", 500),
        "failure_policy": _required_text(payload.get("failure_policy"), "failure_policy", 500),
    }
    with _conn(db_path) as db:
        columns = ",".join(record)
        placeholders = ",".join("?" for _ in record)
        db.execute(f"INSERT INTO sources({columns}) VALUES({placeholders})", tuple(record.values()))
        _audit(db, role, "SOURCE_REGISTERED", "source", record["id"], after=record)
    return record


def create_fx_rate(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    source_currency = _required_text(payload.get("source_currency"), "source_currency", 3).upper()
    target_currency = _required_text(payload.get("target_currency"), "target_currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", source_currency) or not re.fullmatch(r"[A-Z]{3}", target_currency):
        raise FIPError("FX currencies must use three-letter codes.")
    if source_currency == target_currency:
        raise FIPError("An FX rate must convert between two different currencies.")
    convention = payload.get("conversion_convention")
    if not isinstance(convention, str) or convention not in FX_CONVENTIONS:
        raise FIPError("conversion_convention must be period_average, period_end, transaction_date, or budget_rate.")
    rate = _amount(payload.get("rate_decimal"))
    if rate <= 0:
        raise FIPError("FX rate must be positive and represent target-currency units per one source-currency unit.")
    source_id = _required_text(payload.get("source_id"), "source_id", 40)
    record = {"id": new_id("FIP-FX"), "source_currency": source_currency,
              "target_currency": target_currency, "rate_decimal": str(rate),
              "period_id": _required_text(payload.get("period_id"), "period_id", 40),
              "conversion_convention": convention, "source_id": source_id,
              "status": "PENDING_REVIEW", "created_at": now_utc(), "approved_at": None}
    with _conn(db_path) as db:
        if db.execute("SELECT 1 FROM sources WHERE id=?", (source_id,)).fetchone() is None:
            raise FIPError("FX source_id was not found.")
        db.execute("INSERT INTO fx_rates VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(record.values()))
        _audit(db, role, "FX_RATE_REGISTERED", "fx_rate", record["id"], after=record)
    return record


def approve_fx_rate(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    reason = _required_text(reason, "decision_reason", 1000)
    with _conn(db_path) as db:
        row = db.execute("SELECT * FROM fx_rates WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise FIPError("FX rate was not found.")
        before = dict(row)
        if before["status"] != "PENDING_REVIEW":
            raise FIPError("Only pending FX rates can be approved.")
        source = db.execute("SELECT approval_status,permitted_use FROM sources WHERE id=?", (before["source_id"],)).fetchone()
        if source is None or source["approval_status"] != "APPROVED" or source["permitted_use"] != "public_display":
            raise FIPError("FX evidence source must be approved for public display.")
        approved_at = now_utc()
        db.execute("UPDATE fx_rates SET status='APPROVED',approved_at=? WHERE id=?", (approved_at, record_id))
        after = dict(db.execute("SELECT * FROM fx_rates WHERE id=?", (record_id,)).fetchone())
        _audit(db, role, "FX_RATE_APPROVED", "fx_rate", record_id, before=before, after=after, reason=reason)
        return after


def create_observation(db_path: Path, payload: dict[str, Any], role: str,
                       workflow: dict[str, Any] | None = None) -> dict[str, Any]:
    domain = payload.get("domain")
    if domain == "corporate":
        metrics = CORPORATE_METRICS
    elif domain == "government":
        metrics = GOVERNMENT_METRICS
    else:
        raise FIPError("domain must be corporate or government.")
    metric_id = payload.get("metric_id")
    if not isinstance(metric_id, str) or metric_id not in metrics:
        raise FIPError("metric_id is not in the selected domain's approved Release 1 catalogue.")
    currency = _required_text(payload.get("currency"), "currency", 3).upper()
    if not re.fullmatch(r"[A-Z]{3}", currency):
        raise FIPError("currency must be a three-letter ISO-style code.")
    unit = payload.get("unit")
    if not isinstance(unit, str) or unit not in UNIT_FACTORS:
        raise FIPError("unit must be a supported scale.")
    period_type = payload.get("period_type")
    if not isinstance(period_type, str) or period_type not in PERIOD_TYPES:
        raise FIPError("period_type must be annual, quarterly, monthly, or custom.")
    period_id = _required_text(payload.get("period_id"), "period_id", 40)
    start, end = _date(payload.get("period_start"), "period_start"), _date(payload.get("period_end"), "period_end")
    if start > end:
        raise FIPError("period_start must be on or before period_end.")
    fiscal_scope = _optional_text(payload.get("fiscal_scope"), "fiscal_scope", 150)
    budget_stage = _optional_text(payload.get("budget_stage"), "budget_stage", 80)
    freshness_status = payload.get("freshness_status")
    if not isinstance(freshness_status, str) or freshness_status not in FRESHNESS_STATES:
        raise FIPError("freshness_status must be CURRENT, STALE, or UNKNOWN.")
    framework_id = payload.get("framework_id")
    if not isinstance(framework_id, str) or framework_id not in FRAMEWORKS:
        raise FIPError("framework_id must be selected from the framework registry.")
    framework_version = _required_text(payload.get("framework_version"), "framework_version", 60)
    if domain == "government":
        fiscal_scope = _required_text(fiscal_scope, "fiscal_scope", 150)
        budget_stage = _required_text(budget_stage, "budget_stage", 80)
    value = str(_amount(payload.get("value")))
    original_amount = Decimal(value)
    standardized_value = original_amount * UNIT_FACTORS[unit]
    standardized_currency = currency
    fx_rate_id = _optional_text(payload.get("fx_rate_id"), "fx_rate_id", 40)
    conversion_convention = None
    if fx_rate_id:
        with _conn(db_path) as db:
            rate = db.execute("""SELECT f.*,s.approval_status,s.permitted_use FROM fx_rates f
                                 JOIN sources s ON s.id=f.source_id WHERE f.id=?""", (fx_rate_id,)).fetchone()
            if rate is None or rate["status"] != "APPROVED" or rate["approval_status"] != "APPROVED" or rate["permitted_use"] != "public_display":
                raise FIPError("fx_rate_id must refer to a rate approved from a public-display source.")
            if rate["source_currency"] != currency or rate["period_id"] != period_id:
                raise FIPError("The approved FX rate must match the observation currency and period.")
            standardized_value *= Decimal(rate["rate_decimal"])
            standardized_currency = rate["target_currency"]
            conversion_convention = rate["conversion_convention"]
    supersedes_id = _optional_text(payload.get("supersedes_id"), "supersedes_id", 40)
    version = 1
    if supersedes_id:
        with _conn(db_path) as db:
            prior = db.execute("SELECT * FROM observations WHERE id=?", (supersedes_id,)).fetchone()
            if prior is None:
                raise FIPError("supersedes_id was not found.")
            if prior["entity_id"] != payload.get("entity_id") or prior["domain"] != domain or prior["metric_id"] != metric_id:
                raise FIPError("A correction must retain the original entity, domain, and metric.")
            if prior["status"] not in {"PUBLISHED", "VALIDATION_FAILED", "REJECTED", "SUPERSEDED"}:
                raise FIPError("Only a published fact or a closed failed/replaced record can be corrected.")
            successor = db.execute("SELECT 1 FROM observations WHERE supersedes_id=? AND status NOT IN ('REJECTED','ARCHIVED') LIMIT 1", (supersedes_id,)).fetchone()
            if successor:
                raise FIPError("This record already has an active correction. Correct its latest version instead.")
            version = prior["version"] + 1
    record = {
        "id": new_id("FIP-OBS"), "entity_id": _required_text(payload.get("entity_id"), "entity_id", 40),
        "source_id": _required_text(payload.get("source_id"), "source_id", 40), "domain": domain,
        "metric_id": metric_id, "reported_label": _required_text(payload.get("reported_label") or metrics[metric_id], "reported_label", 150),
        "value_decimal": value, "standardized_value_decimal": str(standardized_value),
        "standardized_currency": standardized_currency, "fx_rate_id": fx_rate_id,
        "conversion_convention": conversion_convention, "currency": currency, "unit": unit, "period_id": period_id,
        "period_start": start, "period_end": end, "period_type": period_type,
        "reporting_basis": _required_text(payload.get("reporting_basis"), "reporting_basis", 120),
        "framework_id": framework_id, "framework_version": framework_version,
        "fiscal_scope": fiscal_scope, "budget_stage": budget_stage, "freshness_status": freshness_status,
        "evidence_reference": _required_text(payload.get("evidence_reference"), "evidence_reference", 300),
        "extraction_method": (workflow or {}).get("extraction_method", "manual_transcription"),
        "version": version, "supersedes_id": supersedes_id,
        "status": "INGESTED",
        "validation_message": None, "validation_json": None, "created_at": now_utc(),
        "ingestion_job_id": (workflow or {}).get("job_id"),
        "ingestion_mode": (workflow or {}).get("mode"),
        "idempotency_key": (workflow or {}).get("idempotency_key"),
    }
    with _conn(db_path) as db:
        entity = db.execute("SELECT * FROM entities WHERE id=?", (record["entity_id"],)).fetchone()
        if entity is None:
            raise FIPError("entity_id was not found.")
        if domain == "corporate" and entity["entity_type"] not in {"company", "public_enterprise"}:
            raise FIPError("Corporate observations require a company or public-enterprise entity.")
        if domain == "government" and entity["entity_type"] not in {"government", "department", "public_enterprise"}:
            raise FIPError("Government observations require a government, department, or public-enterprise entity.")
        if framework_id == "GOVERNMENT_FISCAL" and domain != "government":
            raise FIPError("The government fiscal framework can only be used for government observations.")
        reported_label = record["reported_label"].casefold()
        canonical_label = metrics[metric_id].casefold()
        if reported_label != canonical_label:
            mappings = db.execute("""SELECT DISTINCT metric_id FROM metric_mappings WHERE domain=? AND lower(trim(reported_label))=?
                                   AND framework_id=? AND framework_version=?
                                   AND jurisdiction_code=? AND review_status='VERIFIED'
                                   AND (valid_from IS NULL OR valid_from<=?)
                                   AND (valid_to IS NULL OR valid_to>=?)""",
                               (domain, reported_label, framework_id, framework_version,
                                entity["jurisdiction_code"], start, end)).fetchall()
            if {item["metric_id"] for item in mappings} != {metric_id}:
                raise FIPError("This source label has no verified mapping for the entity's jurisdiction and reporting framework.")
        source = db.execute("SELECT * FROM sources WHERE id=?", (record["source_id"],)).fetchone()
        if source is None:
            raise FIPError("source_id was not found.")
        if workflow:
            if workflow.get("mode") not in INGESTION_MODES:
                raise FIPError("The ingestion mode is invalid.")
            job = db.execute("SELECT source_id,mode FROM ingestion_jobs WHERE id=?", (workflow.get("job_id"),)).fetchone()
            if job is None or job["source_id"] != record["source_id"] or job["mode"] != workflow["mode"]:
                raise FIPError("The ingestion job does not match this observation's source and mode.")
        else:
            global_mode = db.execute("SELECT setting_value FROM fip_settings WHERE setting_key='global_ingestion_mode'").fetchone()
            effective_mode = source["ingestion_mode"] or (global_mode["setting_value"] if global_mode else "MANUAL")
            if effective_mode != "MANUAL":
                raise FIPError(f"This source uses {effective_mode} mode. Create an ingestion job and use its workflow, or select a MANUAL job override.")
        if not supersedes_id and db.execute("""SELECT 1 FROM observations WHERE source_id=? AND entity_id=?
                                                AND period_id=? AND metric_id=? AND evidence_reference=? LIMIT 1""",
                                            (record["source_id"], record["entity_id"], record["period_id"],
                                             record["metric_id"], record["evidence_reference"])).fetchone():
            raise FIPError("This source, entity, period, metric, and evidence reference already exists; link an intentional correction to the previous observation.")
        columns = ",".join(record.keys())
        placeholders = ",".join("?" for _ in record)
        try:
            db.execute(f"INSERT INTO observations({columns}) VALUES({placeholders})", tuple(record.values()))
        except sqlite3.IntegrityError:
            raise FIPError("The observation references a record that does not exist or conflicts with another record.") from None
        _audit(db, role, "OBSERVATION_INGESTED", "observation", record["id"], after=record,
               reason=(f"Ingested through {record['ingestion_mode']} job {record['ingestion_job_id']} ({record['extraction_method']})."
                       if workflow else "Manual evidence entry."))
    return record


def _transition(db_path: Path, table: str, record_id: str, expected: set[str], target: str,
                role: str, action: str, kind: str, reason: str, gate: str | None = None) -> dict[str, Any]:
    allowed_tables = {"entities": "entity", "sources": "source", "observations": "observation", "calculation_runs": "calculation"}
    if table not in allowed_tables:
        raise FIPError("Unknown record type.")
    with _conn(db_path) as db:
        row = db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone()
        if row is None:
            raise FIPError(f"{kind} was not found.")
        before = dict(row)
        if before["status" if table in {"observations", "calculation_runs"} else "review_status" if table == "entities" else "approval_status"] not in expected:
            raise FIPError(f"{kind} is not in a state that allows this action.")
        if gate == "observation_ready":
            joined = db.execute("""SELECT e.review_status,s.approval_status,s.permitted_use FROM observations o
                                   JOIN entities e ON e.id=o.entity_id JOIN sources s ON s.id=o.source_id WHERE o.id=?""", (record_id,)).fetchone()
            if joined["review_status"] != "VERIFIED":
                raise FIPError("The entity must be identity-verified before observation approval.")
            if joined["approval_status"] != "APPROVED" or joined["permitted_use"] != "public_display":
                raise FIPError("The source must be approved for public display before observation approval.")
        if gate == "calculation_ready":
            row_check = db.execute("SELECT result_json FROM calculation_runs WHERE id=?", (record_id,)).fetchone()
            if not json.loads(row_check["result_json"]).get("metrics"):
                raise FIPError("A calculation with no available metrics cannot be published.")
            stale_inputs = db.execute("""SELECT 1 FROM calculation_inputs i JOIN observations o ON o.id=i.observation_id
                                         JOIN sources s ON s.id=o.source_id
                                        WHERE i.calculation_id=? AND (o.status!='PUBLISHED' OR s.approval_status!='APPROVED'
                                          OR s.permitted_use!='public_display') LIMIT 1""", (record_id,)).fetchone()
            if stale_inputs:
                raise FIPError("At least one calculation input is no longer eligible for publication. Recalculate from current approved facts.")
        field = "review_status" if table == "entities" else "approval_status" if table == "sources" else "status"
        db.execute(f"UPDATE {table} SET {field}=? WHERE id=?", (target, record_id))
        after = dict(db.execute(f"SELECT * FROM {table} WHERE id=?", (record_id,)).fetchone())
        _audit(db, role, action, kind, record_id, before=before, after=after, reason=reason)
        return after


def verify_entity(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    return _transition(db_path, "entities", record_id, {"NEEDS_REVIEW"}, "VERIFIED", role, "ENTITY_VERIFIED", "entity", reason)


def approve_source(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    return _transition(db_path, "sources", record_id, {"PENDING_REVIEW"}, "APPROVED", role, "SOURCE_APPROVED", "source", reason)


def validate_observation(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    with _conn(db_path) as db:
        row = db.execute("""SELECT o.*,e.review_status,s.approval_status,s.permitted_use FROM observations o
                            JOIN entities e ON e.id=o.entity_id JOIN sources s ON s.id=o.source_id WHERE o.id=?""", (record_id,)).fetchone()
        if row is None:
            raise FIPError("observation was not found.")
        record = dict(row)
        if record["status"] != "INGESTED":
            raise FIPError("Only ingested observations can be validated.")
        checks = [
            {"dimension": "identity", "status": "PASS" if record["review_status"] == "VERIFIED" else "FAIL",
             "detail": "Entity identity verified." if record["review_status"] == "VERIFIED" else "Entity identity is not verified."},
            {"dimension": "source_authority_and_rights", "status": "PASS" if record["approval_status"] == "APPROVED" and record["permitted_use"] == "public_display" else "FAIL",
             "detail": "Source approved for public display." if record["approval_status"] == "APPROVED" and record["permitted_use"] == "public_display" else "Source approval or reuse rights do not permit publication."},
            {"dimension": "evidence_quality", "status": "PASS" if bool(record["evidence_reference"].strip()) else "FAIL",
             "detail": "Evidence location recorded." if record["evidence_reference"].strip() else "Evidence location is missing."},
            {"dimension": "completeness", "status": "PASS", "detail": "Required observation metadata and reported value are present."},
            {"dimension": "freshness", "status": "PASS" if record["freshness_status"] == "CURRENT" else "FAIL",
             "detail": "Reviewer classified this record as current." if record["freshness_status"] == "CURRENT" else f"Freshness is {record['freshness_status'].lower()}; publication requires a current source."},
        ]
        try:
            _amount(record["value_decimal"])
            number_check = {"dimension": "numeric_validation", "status": "PASS", "detail": "Finite Decimal value accepted."}
        except FIPError:
            number_check = {"dimension": "numeric_validation", "status": "FAIL", "detail": "Value is not a finite Decimal."}
        checks.append(number_check)
        errors = [check["detail"] for check in checks if check["status"] == "FAIL"]
        state = "VALIDATION_FAILED" if errors else "VALIDATED"
        message = "; ".join(errors) if errors else "Structural, identity, source, and reuse checks passed; meaning and transcription still require human review."
        before = {key: record[key] for key in ("id", "status", "validation_message", "validation_json")}
        validation_json = _json(checks)
        db.execute("UPDATE observations SET status=?,validation_message=?,validation_json=? WHERE id=?", (state, message, validation_json, record_id))
        after = {"id": record_id, "status": state, "validation_message": message, "validation_json": validation_json}
        _audit(db, role, "OBSERVATION_VALIDATED", "observation", record_id, before=before, after=after,
               reason=f"Validation result: {message} Reviewer note: {reason}")
        return {**record, **after}


def approve_observation(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    return _transition(db_path, "observations", record_id, {"VALIDATED"}, "APPROVED", role, "OBSERVATION_APPROVED", "observation", reason, gate="observation_ready")


def publish_observation(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    with _conn(db_path) as db:
        row = db.execute("""SELECT o.*,e.review_status,s.approval_status,s.permitted_use FROM observations o
                             JOIN entities e ON e.id=o.entity_id JOIN sources s ON s.id=o.source_id WHERE o.id=?""", (record_id,)).fetchone()
        if row is None:
            raise FIPError("observation was not found.")
        record = dict(row)
        if record["status"] != "APPROVED":
            raise FIPError("Only approved observations can be published.")
        if record["review_status"] != "VERIFIED" or record["approval_status"] != "APPROVED" or record["permitted_use"] != "public_display":
            raise FIPError("Publication requires a verified entity and an approved public-display source.")
        if record["supersedes_id"]:
            prior = db.execute("SELECT * FROM observations WHERE id=?", (record["supersedes_id"],)).fetchone()
            if prior is None:
                raise FIPError("The observation's correction target no longer exists.")
            if prior["status"] == "PUBLISHED":
                before_prior = dict(prior)
                db.execute("UPDATE observations SET status='SUPERSEDED' WHERE id=?", (record["supersedes_id"],))
                after_prior = dict(db.execute("SELECT * FROM observations WHERE id=?", (record["supersedes_id"],)).fetchone())
                _audit(db, role, "OBSERVATION_SUPERSEDED", "observation", record["supersedes_id"], before_prior, after_prior, reason)
                affected_runs = db.execute("""SELECT c.* FROM calculation_runs c JOIN calculation_inputs i ON i.calculation_id=c.id
                                              WHERE i.observation_id=? AND c.status='PUBLISHED'""", (record["supersedes_id"],)).fetchall()
                for run in affected_runs:
                    before_run = dict(run)
                    db.execute("UPDATE calculation_runs SET status='SUPERSEDED' WHERE id=?", (run["id"],))
                    after_run = dict(db.execute("SELECT * FROM calculation_runs WHERE id=?", (run["id"],)).fetchone())
                    _audit(db, role, "CALCULATION_SUPERSEDED", "calculation", run["id"], before_run, after_run,
                           f"Input observation {record['supersedes_id']} was superseded; recalculate and review this result.")
        before = {key: record[key] for key in ("id", "status", "version", "supersedes_id")}
        db.execute("UPDATE observations SET status='PUBLISHED' WHERE id=?", (record_id,))
        after = {"id": record_id, "status": "PUBLISHED", "version": record["version"], "supersedes_id": record["supersedes_id"]}
        _audit(db, role, "OBSERVATION_PUBLISHED", "observation", record_id, before, after, reason)
        job_id = record.get("ingestion_job_id")
        if job_id:
            job = db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
            if job is not None and job["status"] == "AWAITING_REVIEW":
                pending = db.execute("SELECT 1 FROM observations WHERE ingestion_job_id=? AND status NOT IN ('PUBLISHED','SUPERSEDED','REJECTED','ARCHIVED') LIMIT 1", (job_id,)).fetchone()
                open_exception = any(item.get("status") == "OPEN" for item in json.loads(job["exceptions_json"]))
                if pending is None and not open_exception:
                    before_job = dict(job)
                    db.execute("UPDATE ingestion_jobs SET status='COMPLETED',completed_at=? WHERE id=?", (now_utc(), job_id))
                    after_job = dict(db.execute("SELECT * FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone())
                    _audit(db, role, "INGESTION_JOB_REVIEW_COMPLETED", "ingestion_job", job_id,
                           before=before_job, after=after_job,
                           reason="All observations from the job completed reviewer publication and no exceptions remain open.")
        return {**record, **after}


def publish_calculation(db_path: Path, record_id: str, role: str, reason: str) -> dict[str, Any]:
    return _transition(db_path, "calculation_runs", record_id, {"REVIEW_REQUIRED"}, "PUBLISHED", role,
                       "CALCULATION_PUBLISHED", "calculation", reason, gate="calculation_ready")


def create_release(db_path: Path, notes: str, role: str) -> dict[str, Any]:
    notes = _required_text(notes, "release_notes", 1000)
    with _conn(db_path) as db:
        db.execute("BEGIN IMMEDIATE")
        observations = [dict(row) for row in db.execute("""SELECT o.*,e.canonical_name,e.jurisdiction_code,
                         s.title AS source_title,s.publisher,s.category AS source_category,
                         s.jurisdiction_code AS source_jurisdiction,s.url AS source_url,s.publication_date,
                         s.retrieval_method,s.expected_frequency,s.license_name,s.permitted_use,s.retention_rule
                         FROM observations o JOIN entities e ON e.id=o.entity_id JOIN sources s ON s.id=o.source_id
                         WHERE o.status='PUBLISHED' AND e.review_status='VERIFIED'
                           AND s.approval_status='APPROVED' AND s.permitted_use='public_display'
                         ORDER BY e.canonical_name,o.period_start,o.metric_id""")]
        calculations = []
        for row in db.execute("""SELECT c.*,e.canonical_name FROM calculation_runs c JOIN entities e ON e.id=c.entity_id
                                WHERE c.status='PUBLISHED' ORDER BY c.created_at"""):
            item = dict(row)
            stale = db.execute("""SELECT 1 FROM calculation_inputs i JOIN observations o ON o.id=i.observation_id
                                 JOIN sources s ON s.id=o.source_id WHERE i.calculation_id=?
                                 AND (o.status!='PUBLISHED' OR s.approval_status!='APPROVED' OR s.permitted_use!='public_display') LIMIT 1""",
                               (item["id"],)).fetchone()
            if stale:
                continue
            item["result"] = json.loads(item.pop("result_json"))
            calculations.append(item)
        used_fx_ids = {item["fx_rate_id"] for item in observations if item.get("fx_rate_id")}
        fx_rates = []
        for rate_id in sorted(used_fx_ids):
            rate = db.execute("""SELECT f.*,s.title AS source_title,s.publisher,s.category AS source_category,
                                s.jurisdiction_code AS source_jurisdiction,s.url AS source_url,s.publication_date,
                                s.retrieval_method,s.expected_frequency,s.license_name,s.permitted_use,s.retention_rule
                                  FROM fx_rates f JOIN sources s ON s.id=f.source_id
                                  WHERE f.id=? AND f.status='APPROVED' AND s.approval_status='APPROVED'
                                    AND s.permitted_use='public_display'""", (rate_id,)).fetchone()
            if rate:
                fx_rates.append(dict(rate))
        if not observations and not calculations:
            raise FIPError("There are no individually published facts or calculations to snapshot.")
        number = db.execute("SELECT COALESCE(MAX(release_number),0)+1 FROM publication_releases").fetchone()[0]
        version = f"FIP-R1-{number:04d}"
        jurisdictions = sorted({item["jurisdiction_code"] for item in observations})
        periods = sorted({item["period_id"] for item in observations} | {item["period_id"] for item in calculations})
        snapshot = {
            "version": version, "formula_catalog_version": FORMULA_CATALOG_VERSION,
            "coverage": {"fact_count": len(observations), "calculation_count": len(calculations),
                         "entity_count": len({item["entity_id"] for item in observations} | {item["entity_id"] for item in calculations}),
                         "jurisdictions": jurisdictions, "periods": periods},
            "observations": observations, "calculations": calculations, "fx_rates": fx_rates,
        }
        release = {"id": new_id("FIP-REL"), "release_number": number, "version": version,
                   "status": "DRAFT", "created_at": now_utc(), "created_by": role,
                   "notes": notes, "snapshot_json": _json(snapshot), "approved_at": None,
                   "approval_reason": None}
        db.execute("INSERT INTO publication_releases VALUES(?,?,?,?,?,?,?,?,?,?)",
                   tuple(release.values()))
        _audit(db, role, "RELEASE_SNAPSHOT_CREATED", "release", release["id"], after=snapshot,
               reason=notes)
        return {key: value for key, value in release.items() if key != "snapshot_json"} | {"coverage": snapshot["coverage"]}


def approve_release(db_path: Path, record_id: str, reason: str, role: str) -> dict[str, Any]:
    reason = _required_text(reason, "decision_reason", 1000)
    with _conn(db_path) as db:
        release = db.execute("SELECT * FROM publication_releases WHERE id=?", (record_id,)).fetchone()
        if release is None:
            raise FIPError("release was not found.")
        before = dict(release)
        if before["status"] != "DRAFT":
            raise FIPError("Only draft releases can be approved.")
        snapshot = json.loads(before["snapshot_json"])
        if not snapshot.get("observations") and not snapshot.get("calculations"):
            raise FIPError("An empty release cannot be approved.")
        approved_at = now_utc()
        db.execute("UPDATE publication_releases SET status='APPROVED',approved_at=?,approval_reason=? WHERE id=?",
                   (approved_at, reason, record_id))
        after = dict(db.execute("SELECT * FROM publication_releases WHERE id=?", (record_id,)).fetchone())
        _audit(db, role, "RELEASE_APPROVED", "release", record_id, before=before, after=after, reason=reason)
        return {key: value for key, value in after.items() if key != "snapshot_json"}


def list_releases(db_path: Path, approved_only: bool = True) -> list[dict[str, Any]]:
    query = "SELECT id,release_number,version,status,created_at,created_by,notes,approved_at,approval_reason,snapshot_json FROM publication_releases"
    if approved_only:
        query += " WHERE status='APPROVED'"
    query += " ORDER BY release_number DESC"
    with _conn(db_path) as db:
        rows = [dict(row) for row in db.execute(query)]
    for record in rows:
        snapshot = json.loads(record.pop("snapshot_json"))
        record["coverage"] = snapshot["coverage"]
    return rows


def readiness(db_path: Path) -> dict[str, Any]:
    with _conn(db_path) as db:
        gates = [dict(row) for row in db.execute("SELECT * FROM release_gates ORDER BY gate_id")]
        requirements = [dict(row) for row in db.execute("SELECT * FROM nfr_register ORDER BY category")]
    outstanding_gates = [item["gate_id"] for item in gates if item["status"] not in {"PASS", "NOT_APPLICABLE"}]
    outstanding_nfr = [item["requirement_id"] for item in requirements if item["status"] != "VERIFIED"]
    production_gate = next(item for item in gates if item["gate_id"] == "production_deployment")
    return {"architecture_specified": True, "prototype_implemented": True,
            "preproduction_gates_complete": not [item for item in gates if item["gate_id"] not in {"production_deployment", "release_approval"} and item["status"] not in {"PASS", "NOT_APPLICABLE"}],
            "production_deployment_verified": production_gate["status"] == "PASS",
            "production_ready": not outstanding_gates and not outstanding_nfr and production_gate["status"] == "PASS",
            "outstanding_release_gates": outstanding_gates,
            "outstanding_nonfunctional_requirements": outstanding_nfr,
            "release_gates": gates, "nonfunctional_requirements": requirements}


def update_release_gate(db_path: Path, gate_id: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    status = payload.get("status")
    if not isinstance(status, str) or status not in {"PENDING", "PASS", "FAIL", "NOT_APPLICABLE"}:
        raise FIPError("Gate status must be PENDING, PASS, FAIL, or NOT_APPLICABLE.")
    evidence = _optional_text(payload.get("evidence_reference"), "evidence_reference", 1000)
    if status in {"PASS", "NOT_APPLICABLE"} and not evidence:
        raise FIPError("A recorded evidence reference or rationale is required for PASS and NOT_APPLICABLE.")
    with _conn(db_path) as db:
        row = db.execute("SELECT * FROM release_gates WHERE gate_id=?", (gate_id,)).fetchone()
        if row is None:
            raise FIPError("Release gate was not found.")
        before = dict(row)
        db.execute("UPDATE release_gates SET status=?,evidence_reference=?,updated_at=?,updated_by=? WHERE gate_id=?",
                   (status, evidence, now_utc(), role, gate_id))
        after = dict(db.execute("SELECT * FROM release_gates WHERE gate_id=?", (gate_id,)).fetchone())
        _audit(db, role, "RELEASE_GATE_UPDATED", "release_gate", gate_id, before, after,
               evidence or f"Gate status set to {status}.")
        return after


def update_nfr(db_path: Path, requirement_id: str, payload: dict[str, Any], role: str) -> dict[str, Any]:
    target = _optional_text(payload.get("target"), "target", 500)
    evidence = _optional_text(payload.get("evidence_reference"), "evidence_reference", 1000)
    status = payload.get("status")
    if not isinstance(status, str) or status not in {"PENDING", "DEFINED", "VERIFIED"}:
        raise FIPError("NFR status must be PENDING, DEFINED, or VERIFIED.")
    if status in {"DEFINED", "VERIFIED"} and not target:
        raise FIPError("A measurable target is required before an NFR can be defined or verified.")
    if status == "VERIFIED" and not evidence:
        raise FIPError("Verification evidence is required before an NFR can be marked VERIFIED.")
    with _conn(db_path) as db:
        row = db.execute("SELECT * FROM nfr_register WHERE requirement_id=?", (requirement_id,)).fetchone()
        if row is None:
            raise FIPError("Non-functional requirement was not found.")
        before = dict(row)
        db.execute("UPDATE nfr_register SET target=?,status=?,evidence_reference=?,updated_at=?,updated_by=? WHERE requirement_id=?",
                   (target, status, evidence, now_utc(), role, requirement_id))
        after = dict(db.execute("SELECT * FROM nfr_register WHERE requirement_id=?", (requirement_id,)).fetchone())
        _audit(db, role, "NFR_UPDATED", "nfr", requirement_id, before, after,
               evidence or f"NFR status set to {status}.")
        return after


def _choose_input(db: sqlite3.Connection, entity_id: str, domain: str, metric_id: str,
                  period_id: str, prior_period_id: str | None = None,
                  use_prior_period: bool = False) -> dict[str, Any] | None:
    selected_period = prior_period_id if use_prior_period and prior_period_id else period_id
    rows = db.execute("""SELECT o.*,s.permitted_use,e.review_status FROM observations o
                         JOIN sources s ON s.id=o.source_id JOIN entities e ON e.id=o.entity_id
                        WHERE o.entity_id=? AND o.domain=? AND o.metric_id=? AND o.period_id=?
                          AND o.status='PUBLISHED' AND s.approval_status='APPROVED'
                          AND s.permitted_use='public_display' AND e.review_status='VERIFIED'""",
                      (entity_id, domain, metric_id, selected_period)).fetchall()
    if len(rows) > 1:
        raise FIPError(f"Conflicting published {metric_id} observations exist for {selected_period}; resolve them before calculation.")
    return dict(rows[0]) if rows else None


def calculate(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    domain = payload.get("domain")
    if not isinstance(domain, str) or domain not in {"corporate", "government"}:
        raise FIPError("domain must be corporate or government.")
    entity_id = _required_text(payload.get("entity_id"), "entity_id", 40)
    period_id = _required_text(payload.get("period_id"), "period_id", 40)
    prior_period_id = _optional_text(payload.get("prior_period_id"), "prior_period_id", 40)
    with _conn(db_path) as db:
        entity = db.execute("SELECT * FROM entities WHERE id=? AND review_status='VERIFIED'", (entity_id,)).fetchone()
        if entity is None:
            raise FIPError("A verified entity with this ID is required.")
        required = {
            "corporate": {"revenue", "gross_profit", "operating_profit", "net_income", "current_assets",
                          "current_liabilities", "inventory", "total_debt", "total_equity", "operating_cash_flow"},
            "government": {"receipts", "expenditure", "public_debt", "interest_expenditure"},
        }[domain]
        inputs: dict[str, dict[str, Any]] = {}
        for metric_id in sorted(required):
            item = _choose_input(db, entity_id, domain, metric_id, period_id)
            if item:
                inputs[metric_id] = item
        optional_metrics = ({"ebitda", "ebit", "total_assets", "closing_cash", "capital_expenditure",
                             "interest_expense", "investing_cash_flow", "financing_cash_flow"} if domain == "corporate"
                            else {"tax_revenue", "non_tax_revenue", "grants", "transfers",
                                  "capital_expenditure", "revenue_expenditure"})
        for metric_id in sorted(optional_metrics):
            item = _choose_input(db, entity_id, domain, metric_id, period_id)
            if item:
                inputs[metric_id] = item
        limitations: list[str] = []
        if prior_period_id:
            if prior_period_id == period_id:
                raise FIPError("prior_period_id must differ from the current period_id.")
            trend_metrics = {"revenue"} if domain == "corporate" else {"receipts", "expenditure", "public_debt"}
            for metric_id in sorted(trend_metrics.intersection(inputs)):
                item = _choose_input(db, entity_id, domain, metric_id, period_id, prior_period_id, use_prior_period=True)
                if item:
                    inputs[f"prior_{metric_id}"] = item
        elif domain == "corporate":
            limitations.append("Revenue growth and change are unavailable because no prior_period_id was supplied.")
        else:
            limitations.append("Receipts, expenditure, and debt trends are unavailable because no prior_period_id was supplied.")
        if not inputs:
            raise FIPError("No published, public-display observations exist for this entity and period.")
        contexts = list(inputs.values())
        currencies = {item["standardized_currency"] for item in contexts}
        bases = {item["reporting_basis"] for item in contexts}
        frameworks = {(item["framework_id"], item["framework_version"]) for item in contexts}
        if len(currencies) > 1:
            raise FIPError("Published inputs use different currencies. Convert them through an approved FX workflow before calculation.")
        if len(bases) > 1:
            raise FIPError("Published inputs use different reporting bases. Resolve comparability before calculation.")
        if len(frameworks) > 1:
            raise FIPError("Published inputs use different accounting/fiscal frameworks or framework versions.")
        prior_input_pairs = [(metric.removeprefix("prior_"), item) for metric, item in inputs.items() if metric.startswith("prior_")]
        for metric_id, prior_period in prior_input_pairs:
            current_period = inputs.get(metric_id)
            if current_period is None:
                continue
            current_days = (date.fromisoformat(current_period["period_end"]) - date.fromisoformat(current_period["period_start"])).days
            prior_days = (date.fromisoformat(prior_period["period_end"]) - date.fromisoformat(prior_period["period_start"])).days
            duration_tolerance = {"annual": 1, "semi_annual": 3, "quarterly": 3, "monthly": 3, "custom": 0}[current_period["period_type"]]
            if (current_period["period_type"] != prior_period["period_type"] or
                    date.fromisoformat(prior_period["period_end"]) >= date.fromisoformat(current_period["period_start"]) or
                    abs(current_days - prior_days) > duration_tolerance):
                raise FIPError(f"Current and prior {metric_id} periods are not compatible for a trend comparison.")
        if domain == "government":
            scopes = {item["fiscal_scope"] for item in contexts}
            stages = {item["budget_stage"] for item in contexts}
            if len(scopes) > 1 or len(stages) > 1:
                raise FIPError("Government inputs use different fiscal scopes or budget stages; they cannot be combined.")
        current_periods = {(item["period_start"], item["period_end"], item["period_type"])
                           for metric, item in inputs.items() if not metric.startswith("prior_")}
        if len(current_periods) > 1:
            raise FIPError("Inputs use different reporting-period boundaries or frequencies under this period ID.")

        def value(metric: str) -> Decimal | None:
            item = inputs.get(metric)
            if not item:
                limitations.append(f"{metric}: unavailable; no single published input was found.")
                return None
            return Decimal(item["standardized_value_decimal"])

        metrics: dict[str, Any] = {}

        def add(name: str, label: str, formula_id: str, version: str, formula: str,
                result: Decimal | None, metric_inputs: dict[str, Decimal | None], unit: str = "ratio") -> None:
            missing = [key for key, val in metric_inputs.items() if val is None]
            if result is None:
                if not missing:
                    limitations.append(f"{name}: unavailable under the formula's denominator rules.")
                return
            metrics[name] = {"label": label, "formula_id": formula_id, "formula_version": version,
                             "formula": formula, "value_decimal": str(result), "unit": unit,
                             "inputs": {key: str(val) for key, val in metric_inputs.items() if val is not None}}

        if domain == "corporate":
            revenue, gross = value("revenue"), value("gross_profit")
            operating, net = value("operating_profit"), value("net_income")
            assets, liabilities, inventory = value("current_assets"), value("current_liabilities"), value("inventory")
            debt, equity = value("total_debt"), value("total_equity")
            total_assets = value("total_assets") if "total_assets" in inputs else None
            operating_cash = value("operating_cash_flow")
            closing_cash = value("closing_cash") if "closing_cash" in inputs else None
            interest_expense = value("interest_expense") if "interest_expense" in inputs else None
            capex = value("capital_expenditure") if "capital_expenditure" in inputs else None
            investing_cash = value("investing_cash_flow") if "investing_cash_flow" in inputs else None
            financing_cash = value("financing_cash_flow") if "financing_cash_flow" in inputs else None
            if prior_period_id:
                prior = value("prior_revenue")
                change = revenue-prior if revenue is not None and prior is not None else None
                growth = change/prior*100 if change is not None and prior is not None and prior > 0 else None
                add("revenue_change", "Revenue change", "revenue_change", FORMULA_CATALOG_VERSION,
                    "current revenue - prior revenue", change,
                    {"revenue": revenue, "prior_revenue": prior}, "amount")
                add("revenue_growth_percent", "Revenue growth", "revenue_growth", FORMULA_CATALOG_VERSION,
                    "(current revenue - prior revenue) / prior revenue × 100", growth,
                    {"revenue": revenue, "prior_revenue": prior}, "%")
            for name, label, formula_id, numerator, numerator_key in (
                ("gross_margin_percent", "Gross margin", "gross_margin", gross, "gross_profit"),
                ("ebitda_margin_percent", "EBITDA margin", "ebitda_margin", value("ebitda") if "ebitda" in inputs else None, "ebitda"),
                ("ebit_margin_percent", "EBIT margin", "ebit_margin", value("ebit") if "ebit" in inputs else None, "ebit"),
                ("operating_margin_percent", "Operating margin", "operating_margin", operating, "operating_profit"),
                ("net_margin_percent", "Net margin", "net_margin", net, "net_income"),
            ):
                result = numerator/revenue*100 if numerator is not None and revenue is not None and revenue != 0 else None
                add(name, label, formula_id, FORMULA_CATALOG_VERSION, f"{numerator_key} / revenue × 100", result,
                    {numerator_key: numerator, "revenue": revenue}, "%")
            working = assets-liabilities if assets is not None and liabilities is not None else None
            add("working_capital", "Working capital", "working_capital", FORMULA_CATALOG_VERSION,
                "current assets - current liabilities", working, {"current_assets": assets, "current_liabilities": liabilities}, "amount")
            add("current_ratio", "Current ratio", "current_ratio", FORMULA_CATALOG_VERSION,
                "current assets / current liabilities", assets/liabilities if assets is not None and liabilities not in (None, Decimal(0)) else None,
                {"current_assets": assets, "current_liabilities": liabilities})
            quick = (assets-inventory)/liabilities if assets is not None and inventory is not None and liabilities not in (None, Decimal(0)) else None
            add("quick_ratio", "Quick ratio", "quick_ratio", FORMULA_CATALOG_VERSION,
                "(current assets - inventory) / current liabilities", quick,
                {"current_assets": assets, "inventory": inventory, "current_liabilities": liabilities})
            add("debt_to_equity_ratio", "Debt-to-equity", "debt_to_equity", FORMULA_CATALOG_VERSION,
                "total debt / total equity", debt/equity if debt is not None and equity not in (None, Decimal(0)) else None,
                {"total_debt": debt, "total_equity": equity})
            add("return_on_assets_percent", "Return on assets (period-end basis)", "return_on_assets_period_end", FORMULA_CATALOG_VERSION,
                "net income / period-end total assets × 100",
                net/total_assets*100 if net is not None and total_assets not in (None, Decimal(0)) else None,
                {"net_income": net, "total_assets": total_assets}, "%")
            add("return_on_equity_percent", "Return on equity (period-end basis)", "return_on_equity_period_end", FORMULA_CATALOG_VERSION,
                "net income / period-end total equity × 100",
                net/equity*100 if net is not None and equity not in (None, Decimal(0)) else None,
                {"net_income": net, "total_equity": equity}, "%")
            add("cash_ratio", "Cash ratio (period-end cash)", "cash_ratio", FORMULA_CATALOG_VERSION,
                "period-end cash and cash equivalents / current liabilities",
                closing_cash/liabilities if closing_cash is not None and liabilities not in (None, Decimal(0)) else None,
                {"closing_cash": closing_cash, "current_liabilities": liabilities})
            add("net_debt", "Net debt", "net_debt", FORMULA_CATALOG_VERSION,
                "total debt - period-end cash and cash equivalents",
                debt-closing_cash if debt is not None and closing_cash is not None else None,
                {"total_debt": debt, "closing_cash": closing_cash}, "amount")
            ebit = value("ebit") if "ebit" in inputs else None
            add("interest_coverage_ratio", "Interest coverage", "interest_coverage", FORMULA_CATALOG_VERSION,
                "EBIT / interest expense",
                ebit/interest_expense if ebit is not None and interest_expense not in (None, Decimal(0)) else None,
                {"ebit": ebit, "interest_expense": interest_expense})
            add("free_cash_flow", "Free cash flow", "free_cash_flow", FORMULA_CATALOG_VERSION,
                "operating cash flow - capital expenditure (capex treated as positive)",
                operating_cash-capex if operating_cash is not None and capex is not None else None,
                {"operating_cash_flow": operating_cash, "capital_expenditure": capex}, "amount")
            cash_quality = operating_cash/net if operating_cash is not None and net not in (None, Decimal(0)) else None
            add("operating_cash_flow_to_net_income_ratio", "Operating cash flow / net income", "ocf_to_net_income", FORMULA_CATALOG_VERSION,
                "operating cash flow / net income", cash_quality,
                {"operating_cash_flow": operating_cash, "net_income": net})
            if all(value is not None for value in (operating_cash, investing_cash, financing_cash)):
                add("cash_flow_category_subtotal", "Three-category cash-flow subtotal", "cash_flow_category_subtotal", FORMULA_CATALOG_VERSION,
                    "operating + investing + financing cash flows", operating_cash + investing_cash + financing_cash,
                    {"operating_cash_flow": operating_cash, "investing_cash_flow": investing_cash,
                     "financing_cash_flow": financing_cash}, "amount")
        else:
            receipts, expenditure = value("receipts"), value("expenditure")
            position = receipts-expenditure if receipts is not None and expenditure is not None else None
            add("net_budget_position", "Net budget position", "net_budget_position", FORMULA_CATALOG_VERSION,
                "receipts - expenditure (not represented as an official fiscal deficit)", position,
                {"receipts": receipts, "expenditure": expenditure}, "amount")
            debt, interest = value("public_debt"), value("interest_expenditure")
            tax = value("tax_revenue") if "tax_revenue" in inputs else None
            non_tax = value("non_tax_revenue") if "non_tax_revenue" in inputs else None
            grants = value("grants") if "grants" in inputs else None
            transfers = value("transfers") if "transfers" in inputs else None
            capex = value("capital_expenditure") if "capital_expenditure" in inputs else None
            revenue_expenditure = value("revenue_expenditure") if "revenue_expenditure" in inputs else None
            add("interest_to_receipts_percent", "Interest / receipts", "interest_to_receipts", FORMULA_CATALOG_VERSION,
                "interest expenditure / receipts × 100",
                interest/receipts*100 if interest is not None and receipts not in (None, Decimal(0)) else None,
                {"interest_expenditure": interest, "receipts": receipts}, "%")
            add("public_debt_to_receipts_ratio", "Public debt / receipts", "debt_to_receipts", FORMULA_CATALOG_VERSION,
                "public debt / receipts", debt/receipts if debt is not None and receipts not in (None, Decimal(0)) else None,
                {"public_debt": debt, "receipts": receipts})
            add("tax_revenue_share_percent", "Tax revenue / receipts", "tax_revenue_share", FORMULA_CATALOG_VERSION,
                "tax revenue / receipts × 100",
                tax/receipts*100 if tax is not None and receipts not in (None, Decimal(0)) else None,
                {"tax_revenue": tax, "receipts": receipts}, "%")
            for metric_id, label, formula_id, amount in (
                ("non_tax_revenue", "Non-tax revenue", "non_tax_revenue_share", non_tax),
                ("grants", "Grants", "grants_share", grants),
                ("transfers", "Transfers", "transfers_share", transfers),
            ):
                add(f"{metric_id}_share_percent", f"{label} / receipts", formula_id, FORMULA_CATALOG_VERSION,
                    f"{label.lower()} / receipts × 100",
                    amount/receipts*100 if amount is not None and receipts not in (None, Decimal(0)) else None,
                    {metric_id: amount, "receipts": receipts}, "%")
            add("capital_expenditure_share_percent", "Capital expenditure / expenditure", "capex_share", FORMULA_CATALOG_VERSION,
                "capital expenditure / expenditure × 100",
                capex/expenditure*100 if capex is not None and expenditure not in (None, Decimal(0)) else None,
                {"capital_expenditure": capex, "expenditure": expenditure}, "%")
            add("revenue_expenditure_share_percent", "Revenue expenditure / expenditure", "revenue_expenditure_share", FORMULA_CATALOG_VERSION,
                "revenue expenditure / expenditure × 100",
                revenue_expenditure/expenditure*100 if revenue_expenditure is not None and expenditure not in (None, Decimal(0)) else None,
                {"revenue_expenditure": revenue_expenditure, "expenditure": expenditure}, "%")
            if prior_period_id:
                for metric_id, label, metric_id_prefix in (
                    ("receipts", "Receipts", "receipts"),
                    ("expenditure", "Expenditure", "expenditure"),
                    ("public_debt", "Public debt", "public_debt"),
                ):
                    if metric_id not in inputs:
                        continue
                    current = value(metric_id)
                    prior = value(f"prior_{metric_id}")
                    change = current-prior if current is not None and prior is not None else None
                    growth = change/prior*100 if change is not None and prior is not None and prior > 0 else None
                    add(f"{metric_id_prefix}_change", f"{label} change", f"{metric_id_prefix}_change", FORMULA_CATALOG_VERSION,
                        f"current {label.lower()} - prior {label.lower()}", change,
                        {metric_id: current, f"prior_{metric_id}": prior}, "amount")
                    add(f"{metric_id_prefix}_growth_percent", f"{label} growth", f"{metric_id_prefix}_growth", FORMULA_CATALOG_VERSION,
                        f"(current {label.lower()} - prior {label.lower()}) / prior {label.lower()} × 100", growth,
                        {metric_id: current, f"prior_{metric_id}": prior}, "%")

        if not metrics:
            raise FIPError("No metrics could be calculated from the available published inputs.")
        result = {
            "domain": domain, "entity_id": entity_id, "entity_name": entity["canonical_name"],
            "period_id": period_id, "prior_period_id": prior_period_id, "currency": next(iter(currencies)),
            "reporting_basis": next(iter(bases)), "framework_id": next(iter(frameworks))[0],
            "framework_version": next(iter(frameworks))[1], "metrics": metrics,
            "limitations": list(dict.fromkeys(limitations)), "publication_status": "REVIEW_REQUIRED",
            "formula_catalog_version": FORMULA_CATALOG_VERSION,
            "input_observations": [{"id": item["id"], "metric_id": item["metric_id"],
                                    "version": item["version"], "period_id": item["period_id"],
                                    "source_currency": item["currency"], "source_value": item["value_decimal"],
                                    "source_unit": item["unit"], "standardized_currency": item["standardized_currency"],
                                    "framework_id": item["framework_id"], "framework_version": item["framework_version"],
                                    "standardized_value": item["standardized_value_decimal"],
                                    "fx_rate_id": item["fx_rate_id"],
                                    "conversion_convention": item["conversion_convention"]}
                                   for item in inputs.values()],
        }
        if domain == "government":
            result["fiscal_scope"] = next(iter({item["fiscal_scope"] for item in contexts}))
            result["budget_stage"] = next(iter({item["budget_stage"] for item in contexts}))
            result["limitations"].append("Net budget position is a descriptive calculation and is not necessarily the official fiscal balance or deficit.")
        run_id = new_id("FIP-CALC")
        created = now_utc()
        db.execute("INSERT INTO calculation_runs VALUES(?,?,?,?,?,?,?,?,?)",
                   (run_id, entity_id, domain, period_id, prior_period_id, "REVIEW_REQUIRED", FORMULA_CATALOG_VERSION, _json(result), created))
        for item in {record["id"]: record for record in inputs.values()}.values():
            db.execute("INSERT INTO calculation_inputs VALUES(?,?,?)", (run_id, item["id"], item["version"]))
        _audit(db, role, "CALCULATION_CREATED", "calculation", run_id, after=result)
        return {"id": run_id, **result, "created_at": created}


def reconcile(db_path: Path, payload: dict[str, Any], role: str) -> dict[str, Any]:
    entity_id = _required_text(payload.get("entity_id"), "entity_id", 40)
    domain = payload.get("domain")
    if not isinstance(domain, str) or domain not in {"corporate", "government"}:
        raise FIPError("domain must be corporate or government.")
    period_id = _required_text(payload.get("period_id"), "period_id", 40)
    with _conn(db_path) as db:
        entity = db.execute("SELECT * FROM entities WHERE id=? AND review_status='VERIFIED'", (entity_id,)).fetchone()
        if entity is None:
            raise FIPError("A verified entity with this ID is required.")
        rows = [dict(row) for row in db.execute("""SELECT o.*,s.title AS source_title,s.publication_date,s.approval_status,s.permitted_use
                                                      FROM observations o JOIN sources s ON s.id=o.source_id
                                                     WHERE o.entity_id=? AND o.domain=? AND o.period_id=?
                                                       AND o.status IN ('VALIDATED','APPROVED','PUBLISHED')
                                                       AND s.approval_status='APPROVED' AND s.permitted_use='public_display'
                                                     ORDER BY o.metric_id,o.created_at""", (entity_id, domain, period_id))]
        if not rows:
            raise FIPError("No validated observations exist for this entity and period.")
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in rows:
            grouped.setdefault(item["metric_id"], []).append(item)
        conflicts = {metric: [item["id"] for item in records] for metric, records in grouped.items() if len(records) > 1}
        selected = {metric: records[0] for metric, records in grouped.items() if len(records) == 1}
        checks: list[dict[str, Any]] = []
        if conflicts:
            checks.append({"check_id": "duplicate_conflicts", "status": "NEEDS_REVIEW",
                           "message": "Multiple eligible observations exist for a metric; select or resolve the conflicting evidence.",
                           "conflicts": conflicts})
        contexts = list(selected.values())
        dimension_conflicts = []
        for key in ("standardized_currency", "framework_id", "framework_version", "reporting_basis",
                    "period_start", "period_end", "period_type"):
            values = {item[key] for item in contexts}
            if len(values) > 1:
                dimension_conflicts.append({"dimension": key, "values": sorted(values)})
        if dimension_conflicts:
            checks.append({"check_id": "comparability", "status": "FAIL",
                           "message": "Selected observations use incompatible currency or reporting definitions.",
                           "differences": dimension_conflicts})

        def standardized(metric: str) -> Decimal | None:
            item = selected.get(metric)
            return Decimal(item["standardized_value_decimal"]) if item else None

        def reconciliation(check_id: str, label: str, required_metrics: list[str], terms: dict[str, int]) -> None:
            missing = [metric for metric in required_metrics if standardized(metric) is None]
            if missing:
                checks.append({"check_id": check_id, "status": "INCOMPLETE", "message": f"{label} check is incomplete.", "missing_metrics": missing})
                return
            values = {metric: standardized(metric) for metric in required_metrics}
            difference = sum((values[metric] * Decimal(sign) for metric, sign in terms.items()), Decimal(0))
            tolerance = Decimal("1")
            checks.append({"check_id": check_id, "status": "PASS" if abs(difference) <= tolerance else "FAIL",
                           "message": label, "difference_standardized": str(difference),
                           "tolerance_standardized": str(tolerance),
                           "input_observations": [{"id": selected[metric]["id"], "metric_id": metric,
                                                   "version": selected[metric]["version"]} for metric in required_metrics]})

        if domain == "corporate":
            reconciliation("balance_sheet", "Total assets = total liabilities + total equity",
                           ["total_assets", "total_liabilities", "total_equity"],
                           {"total_assets": 1, "total_liabilities": -1, "total_equity": -1})
            reconciliation("cash_flow", "Opening cash + operating + investing + financing + FX + other = closing cash",
                           ["opening_cash", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
                            "fx_effect_cash", "other_cash_reconciliation", "closing_cash"],
                           {"opening_cash": 1, "operating_cash_flow": 1, "investing_cash_flow": 1,
                            "financing_cash_flow": 1, "fx_effect_cash": 1, "other_cash_reconciliation": 1,
                            "closing_cash": -1})
        else:
            fiscal_conflicts = len({item["fiscal_scope"] for item in contexts}) > 1 or len({item["budget_stage"] for item in contexts}) > 1
            checks.append({"check_id": "government_basis", "status": "PASS" if not dimension_conflicts and not fiscal_conflicts else "FAIL",
                           "message": "Government observations retain their own official fiscal definitions; no universal deficit reconciliation is imposed.",
                           "fiscal_scopes": sorted({item["fiscal_scope"] for item in contexts}),
                           "budget_stages": sorted({item["budget_stage"] for item in contexts})})
        statuses = {item["status"] for item in checks}
        overall = "FAIL" if "FAIL" in statuses else "NEEDS_REVIEW" if "NEEDS_REVIEW" in statuses else "PASS" if statuses and statuses == {"PASS"} else "INCOMPLETE"
        result = {"id": new_id("FIP-REC"), "entity_id": entity_id, "entity_name": entity["canonical_name"],
                  "domain": domain, "period_id": period_id, "overall_status": overall,
                  "checks": checks, "source_observations": [{"id": item["id"], "metric_id": item["metric_id"],
                                      "version": item["version"], "source_title": item["source_title"],
                                      "evidence_reference": item["evidence_reference"]} for item in rows],
                  "freshness_policy": "Not configured; source publication dates are retained for review.",
                  "created_at": now_utc()}
        db.execute("INSERT INTO reconciliation_runs VALUES(?,?,?,?,?,?,?)",
                   (result["id"], entity_id, domain, period_id, overall, _json(result), result["created_at"]))
        _audit(db, role, "RECONCILIATION_RUN", "reconciliation", result["id"], after=result,
               reason="Explicit reconciliation run over validated observations.")
        return result


def audit_events(db_path: Path, record_type: str | None = None, record_id: str | None = None) -> list[dict[str, Any]]:
    clauses, params = [], []
    if record_type:
        clauses.append("record_type=?"); params.append(record_type)
    if record_id:
        clauses.append("record_id=?"); params.append(record_id)
    query = "SELECT * FROM audit_events" + (" WHERE " + " AND ".join(clauses) if clauses else "") + " ORDER BY id DESC LIMIT 200"
    with _conn(db_path) as db:
        records = [dict(row) for row in db.execute(query, params)]
    for record in records:
        record["before"] = json.loads(record.pop("before_json")) if record.get("before_json") else None
        record["after"] = json.loads(record.pop("after_json")) if record.get("after_json") else None
    return records


def constant_time_token_matches(candidate: str, expected: str | None) -> bool:
    return bool(expected) and bool(candidate) and secrets.compare_digest(candidate, expected)
