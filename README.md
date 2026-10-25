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

## Deploy to Render

Deploy this Flask project as a **Web Service**, not a Static Site. Flask serves the pages and API endpoints, and the model runs in the backend.

1. Push the project to a GitHub repository.
2. In Render, choose **New + → Web Service** and connect that repository.
3. Set the **Root Directory** to `Sunilkumar Portfoliio` if this project is inside a larger repository; otherwise leave it blank.
4. Choose **Runtime: Python 3**.
5. Set **Build Command** to `pip install -r requirements.txt`.
6. Set **Start Command** to `gunicorn --bind 0.0.0.0:$PORT app:app`.
7. Select a Python runtime compatible with the installed scikit-learn and NumPy packages, then create the service.
8. When deployment finishes, open the Render URL and check `/health` and `/dashboard`.

Render's filesystem is not persistent by default. The evaluation JSON is committed with the app; do not rely on files written at runtime remaining after a restart. The optional mammography model is not present/trained and should not be enabled for a public deployment without suitable validation. Do not upload real patient images or personal health information.

## Model evaluation and API

- `python train_tabular.py` evaluates Logistic Regression, Decision Tree, Support Vector Machine, and Random Forest. It uses a reproducible stratified 80/20 hold-out split and five-fold cross-validation on the training partition only, then saves the results to `data/model_evaluation.json`.
- Test-set sensitivity treats malignant as the positive class; specificity is reported for benign samples. Accuracy, sensitivity, specificity, precision, F1, ROC AUC, and the selected Random Forest confusion matrix are available in the saved report.
- `GET /health` reports local service status. `GET /api/model-evaluation` returns the saved evaluation report.
- `POST /predict` accepts JSON with all 30 scikit-learn WDBC feature names (the names contain spaces) and returns a research-only class prediction and uncalibrated model scores. All features are required; inputs must be finite and non-negative.
- `POST /predict_multimodal` explicitly returns HTTP 501: paired-data fusion is not implemented or validated.
- The dashboard includes a local JPEG/PNG/WebP/BMP/TIFF preview and optional manual notes. X-ray and ultrasound are preview/manual-note only; there is no model for either modality, and the viewer does not support DICOM.
- The supplied reconstruction archive contains an optional MobileNetV2 mammography training scaffold, not X-ray/ultrasound models or test images. It is now available as `train_image.py`; it requires licensed, de-identified mammography data in `data/mammography/{train,validation}/{benign,malignant}` and dependencies from `requirements-image.txt`. There are currently no such images or trained weights in this workspace, so automated mammography inference remains unavailable until a model is trained.
- If a mammography model is trained and present, `POST /predict-image` accepts supported image uploads in memory, requires `modality=Mammography`, and does not save the upload. Its scores are research outputs only; validation and clinical suitability are not established.
- The existing `POST /api/predict` remains as a compatibility endpoint for the original three-input portfolio demo; its other 27 fields are filled with dataset medians. It is not a complete assessment and is labelled as such.
- No patient history, identifiers, or submitted assessment data are persisted. There is no prediction history feature.

## Testing

Run `python -m pytest -q` from the project directory. The tests cover dataset dimensions/class counts, existing homepage compatibility, dashboard rendering, API validation, health status, legacy behavior, and explicit multimodal unavailability.

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

The demo trains a reproducible Random Forest from scikit-learn's bundled Wisconsin breast-cancer dataset when the app starts. It is intentionally labelled as an educational portfolio demonstration, not a diagnostic tool.
