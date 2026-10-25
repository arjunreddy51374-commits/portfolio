"""Integration checks for the research dashboard and JSON API."""
from __future__ import annotations

import math
from io import BytesIO

import pytest

from app import app, feature_names, model
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


def test_serving_model_is_not_fit_on_the_holdout_partition():
    assert model.estimators_[0].tree_.n_node_samples[0] <= 455


def test_existing_portfolio_homepage_still_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Sunilkumar" in response.data
    assert b"Turning Data Into<br><span>Better Decisions." in response.data
    assert b"Resume" not in response.data
    assert b"View MBA project report" in response.data


def test_dashboard_renders_all_thirty_features(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert response.data.count(b'type="number"') == 30
    assert b"must not replace assessment by a qualified healthcare professional" in response.data.lower()


def test_dashboard_exposes_local_manual_image_review_without_claiming_ai_analysis(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert b"X-ray, ultrasound & mammography review" in response.data
    assert b"only uploaded if you run the mammography model" in response.data
    assert b"X-ray and ultrasound have no automated image model" in response.data
    assert b'data-image-model-ready="false"' in response.data
    assert b"must not replace assessment by a qualified healthcare professional" in response.data


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json["status"] == "ok"
    assert response.json["model_loaded"] is True
    assert response.json["model_version"] == "rf-1.1.0"
    assert response.json["image_model_ready"] is False


def test_full_feature_prediction_returns_disclaimer_and_scores(client, sample_payload):
    response = client.post("/predict", json=sample_payload)
    assert response.status_code == 200
    assert response.json["prediction"] in {"Benign", "Malignant"}
    assert response.json["model_output"]["interpretation"].startswith("Model scores")
    assert "must not replace assessment by a qualified healthcare professional" in response.json["disclaimer"].lower()


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


def test_legacy_three_feature_route_is_retired(client):
    response = client.post(
        "/api/predict",
        json={"mean_radius": 14.2, "mean_texture": 20.1, "mean_perimeter": 92.4},
    )
    assert response.status_code == 410
    assert "requires all 30" in response.json["error"]


def test_homepage_three_feature_demo_form_is_removed(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"id=\"predictionForm\"" not in response.data
    assert b"three-value median-filled shortcut has been removed" in response.data


def test_finance_dashboard_and_analysis_api(client):
    page = client.get("/finance")
    assert page.status_code == 200
    assert b"Numbers, with their" in page.data
    response = client.post("/api/finance/analyze", json={
        "currency": "INR", "unit": "millions", "period": "FY2025",
        "prior_period": "FY2024",
        "source_url": "https://example.com/annual-report.pdf",
        "amounts": {"revenue": "200", "prior_revenue": "160"},
    })
    assert response.status_code == 200
    assert response.json["metadata"]["review_status"] == "unreviewed"
    assert response.json["metrics"]["revenue_growth_percent"]["value_decimal"] == "25.00"


def test_finance_api_rejects_invalid_source_and_content_type(client):
    assert client.post("/api/finance/analyze", data="{}", content_type="text/plain").status_code == 415
    response = client.post("/api/finance/analyze", json={
        "currency": "INR", "unit": "millions", "period": "FY2025",
        "source_url": "http://example.com/report", "amounts": {"revenue": "10"},
    })
    assert response.status_code == 400
    assert "HTTPS" in response.json["error"]


def test_healthcare_dashboard_and_aggregate_kpi_api(client):
    page = client.get("/health-analytics")
    assert page.status_code == 200
    assert b"NO DATA LOADED" in page.data
    assert b"No district, hospital, or programme figures are preloaded" in page.data
    response = client.post("/api/healthcare/kpis", json={
        "source_url": "https://example.com/monthly-report.pdf",
        "period": "April 2025",
        "values": {"inpatient_days": "300", "discharges": "30"},
    })
    assert response.status_code == 200
    assert response.json["metrics"]["average_length_of_stay_days"]["value_decimal"] == "10"
    assert response.json["metadata"]["review_status"] == "unreviewed"
    assert response.json["metadata"]["human_review_required_before_publication"] is True


def test_healthcare_kpi_api_requires_source_url(client):
    response = client.post("/api/healthcare/kpis", json={"period": "April", "values": {}})
    assert response.status_code == 400


def test_evaluation_endpoint_reports_metrics_or_explicit_unavailability(client):
    response = client.get("/api/model-evaluation")
    assert response.status_code in {200, 503}
    if response.status_code == 200:
        assert len(response.json["models"]) == 4
        assert "sensitivity_malignant" in response.json["models"][0]["test"]