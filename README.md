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

The isolated upgrade work is on branch `upgrade/premium-portfolio-phase1`; it is not deployed. The financial workspace is available at `/finance` in that branch.

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

## Financial calculation workspace

- `GET /finance` provides a source-attributed manual input tool; no issuer data is preloaded.
- `POST /api/finance/analyze` accepts a JSON object containing currency, unit, period, HTTPS source URL, and an `amounts` object. Submit amounts as decimal strings to avoid binary floating-point approximation. If `prior_revenue` is supplied, also provide an earlier `prior_period` of the same annual/quarterly frequency.
- Supported calculations include revenue growth, gross/operating/net margins, working capital, current and quick ratios, debt/equity, operating cash flow/net income, and a clearly labeled three-category cash-flow subtotal. Missing inputs or zero denominators are reported rather than guessed.
- Source values and reporting metadata accompany outputs; input units are normalized to base currency units. Exact Decimal-context results and display-rounded results are separate. The endpoint is stateless and does not verify a URL or retain submissions. All entered data remains unreviewed and must be checked against source filings before publication.
- Financial calculation tests are in `tests/test_finance.py`. No Fortis IPO values or conclusions are included because original source documents and reconciled analysis were not available in the repository.

## Healthcare KPI workspace

- `GET /health-analytics` provides generic KPI definitions and an aggregate-input calculator; it does not contain district statistics, official targets, or preloaded hospital data.
- `POST /api/healthcare/kpis` calculates ALOS, bed occupancy, consultation wait, discharge turnaround, and reporting completeness when their source-attributed aggregate numerator/denominator pairs are supplied.
- Definitions are prototypes that require local approval of cohorts, event timestamps, exclusions, and reporting periods before operational comparisons. Requests are stateless and the API rejects unknown fields such as patient identifiers.
- The interface displays no metrics until users submit figures; unreviewed values require human source review before publication.

## Testing

Run `python -m pytest -q` from the project directory. The tests cover dataset dimensions/class counts, model training split, homepage compatibility, dashboard rendering, finance/healthcare calculations and validation, API behavior, and explicit multimodal unavailability.

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
