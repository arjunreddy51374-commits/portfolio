"""Integration checks for the research dashboard and JSON API."""
from __future__ import annotations

import math
from io import BytesIO

import pytest

from app import app, feature_names
from ml.model_service import load_dataset


@pytest.fixture()
def client():
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def sample_payload():
    features, _, names, _ = load_dataset()
    return dict(zip(names, features[0].tolist()))


def test_dataset_has_expected_wdbc_dimensions_and_classes():
    features, labels, names, details = load_dataset()
    assert features.shape == (569, 30)
    assert len(names) == 30
    assert details["classes"] == {"malignant": 212, "benign": 357}
    assert set(labels) == {0, 1}


def test_existing_portfolio_homepage_still_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Sunilkumar" in response.data


def test_dashboard_renders_all_thirty_features(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.data.count(b'type="number"') == 30
    assert b"not a diagnosis" in response.data.lower()


def test_dashboard_exposes_local_manual_image_review_without_claiming_ai_analysis(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"X-ray, ultrasound & mammography review" in response.data
    assert b"only uploaded if you run the mammography model" in response.data
    assert b"X-ray and ultrasound have no automated image model" in response.data
    assert b'data-image-model-ready="false"' in response.data


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"
    assert response.json["model_loaded"] is True
    assert response.json["image_model_ready"] is False


def test_full_feature_prediction_returns_disclaimer_and_scores(client, sample_payload):
    response = client.post("/predict", json=sample_payload)
    assert response.status_code == 200
    assert response.json["prediction"] in {"Benign", "Malignant"}
    assert response.json["model_output"]["interpretation"].startswith("Model scores")
    assert "not a diagnosis" in response.json["disclaimer"].lower()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.pop(next(iter(payload))),
        lambda payload: payload.__setitem__(next(iter(payload)), -1),
        lambda payload: payload.__setitem__(next(iter(payload)), math.nan),
        lambda payload: payload.__setitem__(next(iter(payload)), "not numeric"),
        lambda payload: payload.__setitem__(next(iter(payload)), True),
    ],
)
def test_prediction_rejects_missing_and_invalid_features(client, sample_payload, mutation):
    mutation(sample_payload)
    response = client.post("/predict", json=sample_payload)
    assert response.status_code == 400
    assert response.json["error"] == "Input validation failed."


def test_prediction_requires_json_content_type(client):
    response = client.post("/predict", data="{}", content_type="text/plain")
    assert response.status_code == 415


def test_multimodal_route_says_unavailable_without_fabricating_result(client):
    response = client.post("/predict_multimodal", json={})
    assert response.status_code == 501
    assert response.json["image_model_available"] is False
    assert "no validated fusion model" in response.json["error"]


def test_xray_image_is_rejected_from_mammography_model_endpoint(client):
    response = client.post(
        "/predict-image",
        data={"modality": "X-ray", "image": (BytesIO(b"not-an-image"), "sample.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
    assert "mammography only" in response.json["error"]


def test_mammography_endpoint_reports_missing_model_without_predicting(client):
    response = client.post(
        "/predict-image",
        data={"modality": "Mammography", "image": (BytesIO(b"not-an-image"), "sample.jpg")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 503
    assert response.json["image_model_available"] is False


def test_image_training_fails_clearly_without_dataset():
    from train_image import _check_dataset

    with pytest.raises(SystemExit, match="No complete mammography dataset"):
        _check_dataset()


def test_legacy_three_feature_route_remains_available(client):
    response = client.post(
        "/api/predict",
        json={"mean_radius": 14.2, "mean_texture": 20.1, "mean_perimeter": 92.4},
    )
    assert response.status_code == 200
    assert "classification" in response.json
    assert "remaining inputs are filled with dataset medians" in response.json["disclaimer"]


def test_evaluation_endpoint_reports_metrics_or_explicit_unavailability(client):
    response = client.get("/api/model-evaluation")
    assert response.status_code in {200, 503}
    if response.status_code == 200:
        assert len(response.json["models"]) == 4
        assert "sensitivity_malignant" in response.json["models"][0]["test"]