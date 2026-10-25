"""Flask portfolio and research-only breast-cancer classification dashboard."""
from __future__ import annotations

import json
import os
import warnings
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, render_template, request
from PIL import Image, UnidentifiedImageError
from werkzeug.exceptions import RequestEntityTooLarge

from finance.calculations import FinancialInputError, analyze_financials
from healthcare.metrics import HealthMetricInputError, analyze_health_kpis
from fip.routes import create_blueprint as create_fip_blueprint
from ml.model_service import (
    MODEL_NAME,
    MODEL_VERSION,
    load_dataset,
    train_prediction_model,
    validate_feature_payload,
)

BASE_DIR = Path(__file__).resolve().parent
MODEL_DIR = BASE_DIR / "models"
IMAGE_MODEL_PATH = MODEL_DIR / "image_model.keras"
IMAGE_CLASS_NAMES_PATH = MODEL_DIR / "image_class_names.json"
MAX_JSON_CONTENT_LENGTH = 32 * 1024
MAX_IMAGE_CONTENT_LENGTH = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "bmp", "tif", "tiff", "webp"}
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = MAX_IMAGE_CONTENT_LENGTH
app.config["FIP_ADMIN_TOKEN"] = os.environ.get("FIP_ADMIN_TOKEN")
app.config["FIP_REVIEWER_TOKEN"] = os.environ.get("FIP_REVIEWER_TOKEN")
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
FIP_DB_PATH = Path(os.environ.get("FIP_DATABASE", str(BASE_DIR / "instance" / "fip.sqlite3"))).expanduser()
app.register_blueprint(create_fip_blueprint(FIP_DB_PATH))


def read_data(name: str):
    with (BASE_DIR / "data" / name).open(encoding="utf-8") as file:
        return json.load(file)


profile = read_data("profile.json")
experience = read_data("experience.json")
projects = read_data("projects.json")
certifications = read_data("certifications.json")
education = read_data("education.json")
skills = read_data("skills.json")
model, training_features, feature_names = train_prediction_model()
feature_groups = [
    ("Mean values", [name for name in feature_names if name.startswith("mean ")]),
    ("Standard error values", [name for name in feature_names if name.endswith("error")]),
    ("Worst values", [name for name in feature_names if name.startswith("worst ")]),
]
evaluation_path = BASE_DIR / "data" / "model_evaluation.json"


def load_image_artifacts():
    """Load optional archive-trained mammography model and its class mapping."""
    if not IMAGE_MODEL_PATH.is_file() or not IMAGE_CLASS_NAMES_PATH.is_file():
        return None, []
    try:
        class_names = json.loads(IMAGE_CLASS_NAMES_PATH.read_text(encoding="utf-8"))
        if class_names != ["benign", "malignant"]:
            return None, []
        import tensorflow as tf

        return tf.keras.models.load_model(IMAGE_MODEL_PATH, compile=False), class_names
    except Exception:
        return None, []


image_model, image_class_names = load_image_artifacts()


def load_evaluation():
    """Read generated test metrics; never substitute hard-coded measurements."""
    try:
        with evaluation_path.open(encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return None


def json_payload():
    if request.content_length and request.content_length > MAX_JSON_CONTENT_LENGTH:
        return None, (jsonify(error="JSON request body is too large."), 413)
    if not request.is_json:
        return None, (jsonify(error="Content-Type must be application/json."), 415)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return None, (jsonify(error="Request body must be a JSON object."), 400)
    return payload, None


def prediction_response(features: np.ndarray):
    probabilities = model.predict_proba(features)[0]
    malignant_probability = float(probabilities[1])
    benign_probability = float(probabilities[0])
    malignant = int(model.predict(features)[0]) == 1
    return {
        "prediction": "Malignant" if malignant else "Benign",
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "model_output": {
            "malignant_score": round(malignant_probability, 6),
            "benign_score": round(benign_probability, 6),
            "interpretation": "Model scores are not calibrated patient-level probabilities or a diagnosis.",
        },
        "explanation": {
            "type": "global_feature_importance",
            "message": "Global feature importance describes the model overall; it does not explain this individual result.",
        },
        "disclaimer": "This demonstration is for educational and research purposes only. It is not a medical diagnostic service and must not replace assessment by a qualified healthcare professional.",
    }


@app.get("/")
def index():
    return render_template(
        "index.html",
        profile=profile,
        experience=experience,
        projects=projects,
        certifications=certifications,
        education=education,
        skills=skills,
        resume_available=(BASE_DIR / "static" / "documents" / "resume.pdf").is_file(),
        available_static_files={
            project["report"]
            for project in projects
            if project.get("report") and (BASE_DIR / "static" / project["report"]).is_file()
        },
    )


@app.get("/dashboard")
def dashboard():
    evaluation = load_evaluation()
    return render_template(
        "dashboard.html",
        evaluation=evaluation,
        feature_names=feature_names,
        feature_groups=feature_groups,
        model_name=MODEL_NAME,
        model_version=MODEL_VERSION,
        image_model_ready=image_model is not None,
    )


@app.get("/finance")
def finance_dashboard():
    return render_template("finance.html")


@app.post("/api/finance/analyze")
def finance_analyze():
    if not request.is_json:
        return jsonify(error="Content-Type must be application/json."), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Request body must be a JSON object."), 400
    try:
        result = analyze_financials(payload)
    except FinancialInputError as error:
        return jsonify(error=str(error)), 400
    return jsonify(result)


@app.get("/health-analytics")
def healthcare_dashboard():
    return render_template("healthcare.html")


@app.post("/api/healthcare/kpis")
def healthcare_kpis():
    if not request.is_json:
        return jsonify(error="Content-Type must be application/json."), 415
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Request body must be a JSON object."), 400
    try:
        return jsonify(analyze_health_kpis(payload))
    except HealthMetricInputError as error:
        return jsonify(error=str(error)), 400


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        model_loaded=model is not None,
        evaluation_available=load_evaluation() is not None,
        model_version=MODEL_VERSION,
        image_model_ready=image_model is not None,
    )


@app.get("/api/model-evaluation")
def model_evaluation():
    evaluation = load_evaluation()
    if evaluation is None:
        return jsonify(error="Evaluation is unavailable. Run python -m ml.evaluate first."), 503
    return jsonify(evaluation)


@app.get("/api/portfolio")
def portfolio_api():
    return jsonify(
        profile=profile,
        experience=experience,
        projects=projects,
        certifications=certifications,
        education=education,
        skills=skills,
    )


@app.post("/predict")
def predict():
    payload, error = json_payload()
    if error:
        return error
    features, errors = validate_feature_payload(payload, feature_names)
    if errors:
        return jsonify(error="Input validation failed.", details=errors), 400
    return jsonify(prediction_response(features))


@app.post("/predict_multimodal")
def predict_multimodal():
    return jsonify(
        error="Multimodal fusion is unavailable: no validated fusion model or paired multimodal evaluation is configured.",
        image_model_available=image_model is not None,
    ), 501


@app.post("/predict-image")
def predict_image():
    """Run the optional mammography-only model in memory; never save uploads."""
    if request.form.get("modality") != "Mammography":
        return jsonify(
            error="The optional image model supports mammography only; it must not be used on X-ray or ultrasound images.",
        ), 400
    uploaded = request.files.get("image")
    if uploaded is None or not uploaded.filename:
        return jsonify(error="Attach an image using the 'image' field."), 400
    extension = uploaded.filename.rsplit(".", 1)[-1].lower() if "." in uploaded.filename else ""
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        return jsonify(error="Unsupported image file. Use PNG, JPG, BMP, or TIFF."), 400
    if image_model is None:
        return jsonify(
            error="Mammography image model is not trained or available.",
            next_step="Add licensed, de-identified mammography training data and run python train_image.py with the optional image dependencies installed.",
            image_model_available=False,
        ), 503

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(uploaded.stream) as source:
                if source.format not in {"PNG", "JPEG", "BMP", "TIFF", "WEBP"}:
                    return jsonify(error="The file content is not a supported raster image."), 400
                source.load()
                image = source.convert("RGB").resize((224, 224))
        batch = np.expand_dims(np.asarray(image, dtype=np.float32), axis=0)
        scores = np.asarray(image_model.predict(batch, verbose=0)).reshape(-1)
        if len(scores) != len(image_class_names) or not np.isfinite(scores).all():
            return jsonify(error="The image model returned an invalid result."), 500
        probabilities = {
            name: round(float(score), 6)
            for name, score in zip(image_class_names, scores)
        }
        prediction = image_class_names[int(np.argmax(scores))]
        return jsonify(
            modality="Mammography",
            model_name="MobileNetV2 mammography research model",
            prediction=prediction,
            model_scores=probabilities,
            disclaimer="Research prototype output only. Scores are not calibrated patient-level probabilities or a diagnosis. Not clinically validated; do not use for patient care.",
        )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        return jsonify(error="The uploaded file is not a valid, safely decodable image."), 400


@app.post("/api/predict")
def legacy_demo_predict():
    """Retire the misleading legacy endpoint that substituted median features."""
    return jsonify(
        error="This three-feature shortcut has been retired. The model requires all 30 WDBC measurements.",
        replacement="Use POST /predict with the complete 30-feature payload, or open /dashboard.",
    ), 410


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify(error="Request body is too large."), 413


@app.errorhandler(RequestEntityTooLarge)
def request_entity_too_large(_error):
    return jsonify(error="Image upload exceeds the 12 MB limit."), 413


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5050")), debug=False)
