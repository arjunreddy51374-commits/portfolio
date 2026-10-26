# Sunilkumar Vajjiram Portfolio & Breast Cancer ML Research Dashboard

A Flask portfolio for healthcare, programme-management, analytics, and machine-learning work, with a research-only breast-cancer classification dashboard. Portfolio content lives in `data/*.json`; the UI is in `templates/` and `static/`.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python train_tabular.py
python app.py
```

Open `http://127.0.0.1:5050` for the portfolio or `http://127.0.0.1:5050/dashboard` for the analytics dashboard. Do not open a template directly: templates require Flask.

The FIP workspace is available at `/fip`, its user guide at `/fip/guide`, and the authorized-token staff workspace at `/fip/staff`.

## Model evaluation and API

- `python train_tabular.py` evaluates Logistic Regression, Decision Tree, Support Vector Machine, and Random Forest. It uses a reproducible stratified 80/20 hold-out split and five-fold cross-validation on the training partition only, then saves the results to `data/model_evaluation.json`.
- Test-set sensitivity treats malignant as the positive class; specificity is reported for benign samples. Accuracy, sensitivity, specificity, precision, F1, ROC AUC, and the selected Random Forest confusion matrix are available in the saved report.
- `GET /health` reports local service status. `GET /api/model-evaluation` returns the saved evaluation report.
- `POST /predict` accepts JSON with all 30 scikit-learn WDBC feature names (the names contain spaces) and returns a research-only class prediction and uncalibrated model scores. All features are required; inputs must be finite and non-negative.
- `POST /predict_multimodal` explicitly returns HTTP 501: paired-data fusion is not implemented or validated.
- The dashboard includes a local JPEG/PNG/WebP/BMP/TIFF preview and optional manual notes. X-ray and ultrasound are preview/manual-note only; there is no model for either modality, and the viewer does not support DICOM.
- The supplied reconstruction archive contains an optional MobileNetV2 mammography training scaffold, not X-ray/ultrasound models or test images. It is now available as `train_image.py`; it requires licensed, de-identified mammography data in `data/mammography/{train,validation}/{benign,malignant}` and dependencies from `requirements-image.txt`. There are currently no such images or trained weights in this workspace, so automated mammography inference remains unavailable until a model is trained.
- If a mammography model is trained and present, `POST /predict-image` accepts supported image uploads in memory, requires `modality=Mammography`, and does not save the upload. Its scores are research outputs only; validation and clinical suitability are not established.
- The former `POST /api/predict` three-input shortcut now returns HTTP 410. It previously filled 27 of 30 features with medians and was not a valid full-feature assessment. Use `POST /predict` with all 30 named features instead.
- No patient history, identifiers, or submitted assessment data are persisted. There is no prediction history feature.

## FIP Release 1 prototype

- `GET /fip` opens the public financial-intelligence view; staff controls are collapsed there and available at `/fip/staff`. Its local SQLite database is created at `instance/fip.sqlite3` on the first data request and is excluded from version control.
- The ingestion default is persisted as `MANUAL`; global, per-source, and per-job choices accept exactly `MANUAL`, `AUTOMATIC`, or `HYBRID`. Saving a mode does not start a job. Per-source connector configuration must be approved by a reviewer after the source itself is approved for public display.
- Automatic and Hybrid jobs fetch only configured HTTPS CSV or JSON connectors, with a 5 MiB / 10,000-row limit, public-host checks, and a stable idempotency key. Automatic metric resolution accepts canonical catalogue labels or verified source-scoped mappings. Extracted records receive structural validation and stay unpublished; reviewer validation/approval and release approval still govern public output. Hybrid jobs additionally allow corrections for row exceptions.
- Per-source schedules are opt-in. Run `python -m fip.scheduler` as a worker on the same host and shared persistent volume as the web service; configure `FIP_SCHEDULER_POLL_SECONDS` to adjust polling (minimum five seconds). A remote worker with its own local SQLite file cannot share the website's jobs. A source must have an approved connector, an Automatic/Hybrid effective mode, and an interval before the worker queues it.
- Jobs expose queued, running, awaiting-review, completed, failed, and cancelled states. Failed jobs can be retried with the current reviewer-approved connector version; queued jobs can be cancelled with an audited reason.
- The audit log records the actor role, mode, job, connector version, attempt, extraction method, and observation lineage. FIP uses shared admin/reviewer tokens, so the current authentication model identifies the role but not an individual staff account.
- Configure independent admin and reviewer tokens before staff operations:

  ```bash
  export FIP_ADMIN_TOKEN='use-a-long-random-secret'
  export FIP_REVIEWER_TOKEN='use-a-different-long-random-secret'
  python app.py
  ```

- Admin workflows register entities, sources, aliases, relationships, metric mappings, FX rates and reported observations. Reviewer workflows verify identity/mappings, review source rights, validate evidence, publish facts/calculations and approve release snapshots. Reviewer decisions require an audit reason. Tokens are sent in request headers and held only in the current browser tab.
- Observations retain original value, currency and unit, standardized amount and currency, reporting period, accounting/fiscal framework, source locator, freshness assessment, validation dimensions and version lineage. Currency conversion requires an approved, period-matched FX record; no rate is silently inferred.
- Corporate calculations include growth, margins, period-end returns, liquidity, leverage, net debt, free cash flow and cash-flow measures. Return on assets/equity use period-end balances; free cash flow treats capital expenditure as a positive amount and should only be used with an approved mapping that preserves that sign convention. Government calculations include receipts and expenditure composition and period-over-period changes where compatible. Government results require compatible fiscal scope, budget stage, currency and framework. Receipts less expenditure is labeled `net_budget_position`, not an official deficit. Statement-level balance-sheet and cash-flow reconciliations report missing inputs and differences explicitly.
- The Release 1 formula catalogue remains bounded: CAGR, ROIC, working-capital days, debt/GDP, budget execution and budget-stage variance need additional approved inputs and definitions before they can be calculated safely.
- Public Web data comes from the latest separately approved immutable release snapshot. `GET /api/fip/delivery/excel.xlsx` creates a native Excel report with Dashboard, Controls, Corporate, Government, Statements, Engine, Raw Data, Standardized Data, Source Register, FX Rates and release-audit tabs. Exact decimal values stay as text so Excel cannot silently round them. The workbook exposes the canonical calculation results and lineage; it does not independently recalculate them. `GET /api/fip/delivery/excel.csv` provides a flat export, and `GET /api/fip/delivery/powerbi` exposes release-versioned JSON. All delivery routes support `?release_id=...` for an earlier approved snapshot.
- `GET /api/fip/readiness` reports architecture, prototype, release-gate and NFR evidence separately. Gates begin pending, so the app does not claim production readiness. Reviewer evidence can be added in the readiness register.
- This local build does not extract financial figures from PDFs, resolve external entity identifiers, provide a native `.xlsx` model or `.pbix` package, use hosted PostgreSQL/object storage, or demonstrate production security, backup/recovery, performance and deployment. Forecasting, valuation, AI research and commercial features remain deferred releases.
- `GET /finance` remains a separate stateless calculator for unsaved analysis.

## Financial calculation workspace

- `GET /finance` provides a source-attributed manual input tool; no issuer data is preloaded.
- `POST /api/finance/analyze` accepts a JSON object containing currency, unit, period, HTTPS source URL, and an `amounts` object. Submit amounts as decimal strings to avoid binary floating-point approximation. If `prior_revenue` is supplied, also provide an earlier `prior_period` of the same annual/quarterly frequency.
- Supported calculations include revenue growth, margins, EBITDA, working capital, current and quick ratios, debt/equity, net debt, interest coverage, average-balance returns/turnover, working-capital and cash-conversion days, free cash flow, and a clearly labeled three-category cash-flow subtotal. Formula versions and input references accompany results; missing, not-applicable, and invalid metrics remain distinguishable.
- Source values and reporting metadata accompany outputs; input units are normalized to base currency units. Exact Decimal-context results and display-rounded results are separate. The endpoint is stateless and does not verify a URL or retain submissions. All entered data remains unreviewed and must be checked against source filings before publication.
- `POST /api/finance/history` compares 2–20 same-currency annual or quarterly statements. It sorts periods, rejects duplicates and incompatible frequencies, flags missing periods, normalizes units, and computes revenue changes and annual CAGR.
- The page accepts in-memory CSV/XLSX history files through `POST /api/finance/import`. It rejects formula cells, enforces file/row limits, preserves per-row source URLs, and withholds peer comparisons across currencies. It does not extract figures from PDFs or retain uploads.
- The model studio derives editable starting assumptions and produces deterministic downside/base/upside 1–10 year three-statement scenarios. It checks the opening balance sheet, exposes formula/input lineage, reconciles forecast balance sheets, and warns about negative cash. These are scenario illustrations, not predictions.
- Formula definitions, scenario assumptions, a fully worked synthetic example, and current limits are documented in [`finance/PHASE2.md`](finance/PHASE2.md). The complete Finance tests are in `tests/test_finance.py` and `tests/test_finance_phase2.py`. No Fortis IPO values or conclusions are included because original source documents and reconciled analysis were not available in the repository.

## Healthcare KPI workspace

- `GET /health-analytics` provides generic KPI definitions and an aggregate-input calculator; it does not contain district statistics, official targets, or preloaded hospital data.
- `POST /api/healthcare/kpis` calculates ALOS, bed occupancy, consultation wait, discharge turnaround, and reporting completeness when their source-attributed aggregate numerator/denominator pairs are supplied.
- Definitions are prototypes that require local approval of cohorts, event timestamps, exclusions, and reporting periods before operational comparisons. Requests are stateless and the API rejects unknown fields such as patient identifiers.
- The interface displays no metrics until users submit figures; unreviewed values require human source review before publication.

## Testing

Run `python -m pytest -q` from the project directory. The tests cover dataset dimensions/class counts, model training split, homepage compatibility, dashboard rendering, finance/healthcare calculations and validation, FIP identity/evidence/calculation/reconciliation/release workflows, Manual/Automatic/Hybrid ingestion, public/staff page separation, access control, connector approval/versioning, idempotency, hybrid corrections, schedules, retries, cancellation, CSV/Power BI/Excel delivery, API behavior, and explicit multimodal unavailability.

## Optional mammography training

The supplied archive contained an image-model training scaffold, but no medical images or trained image weights. The scaffold is included only for the mammography modality:

1. Install `requirements-image.txt` in a Python/TensorFlow combination supported by the installed TensorFlow release. On macOS, check TensorFlow's platform support before installation.
2. Place licensed, de-identified mammography images under `data/mammography/train/benign`, `data/mammography/train/malignant`, `data/mammography/validation/benign`, and `data/mammography/validation/malignant`.
3. Run `python train_image.py`, then restart the app. The app will only enable mammography inference when compatible weights and class mapping exist.

This starter uses ImageNet-pretrained MobileNetV2 and an exploratory validation split. Its training/validation scores do not establish external or clinical validity. Do not feed it X-rays or ultrasound scans. Multimodal fusion remains disabled.

## Research and safety limits

The model uses the bundled scikit-learn copy of the Wisconsin Diagnostic Breast Cancer dataset: 569 records, 30 numerical features, 212 malignant and 357 benign samples. Evaluation metrics describe this dataset and split only; they are not evidence of clinical performance. The dashboard shows classifier scores, not calibrated patient-level probabilities. Random Forest impurity feature importance is a global model summary, not a per-case explanation. This is not a medical device, diagnosis, or clinical decision-support tool; never use it for patient care.

## Add personal files

- Put a profile image at `static/images/profile.jpg`.
- Put a résumé at `static/documents/resume.pdf`.
- Update the JSON files in `data/` to add experience, projects, credentials, and education.

The demo fits a reproducible Random Forest on the stratified training partition when the app starts; the 20% held-out partition remains excluded from serving-model fitting. It is intentionally labelled as an educational portfolio demonstration, not a diagnostic tool.
