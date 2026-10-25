"""Dataset loading, reproducible model construction, and input validation."""
from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.datasets import load_breast_cancer
from sklearn.ensemble import RandomForestClassifier

MODEL_NAME = "Random Forest"
MODEL_VERSION = "rf-1.0.0"
MAX_FEATURE_VALUE = 1_000_000.0


def load_dataset() -> tuple[np.ndarray, np.ndarray, list[str], dict[str, Any]]:
    """Load sklearn's bundled WDBC data; convert malignant to positive class 1."""
    dataset = load_breast_cancer()
    malignant = (dataset.target == 0).astype(np.int8)
    metadata = {
        "name": "Wisconsin Diagnostic Breast Cancer (scikit-learn bundled copy)",
        "samples": int(dataset.data.shape[0]),
        "features": int(dataset.data.shape[1]),
        "classes": {"malignant": int(malignant.sum()), "benign": int((malignant == 0).sum())},
        "feature_minima": dataset.data.min(axis=0).tolist(),
        "feature_maxima": dataset.data.max(axis=0).tolist(),
    }
    return dataset.data.astype(float), malignant, dataset.feature_names.tolist(), metadata


def build_random_forest() -> RandomForestClassifier:
    """Return the fixed, reproducible estimator used for live demonstration."""
    return RandomForestClassifier(
        n_estimators=250,
        random_state=42,
        min_samples_leaf=2,
        n_jobs=-1,
    )


def train_prediction_model() -> tuple[RandomForestClassifier, np.ndarray, list[str]]:
    """Train the live demo estimator on all bundled records (not for clinical use)."""
    features, labels, feature_names, _ = load_dataset()
    estimator = build_random_forest()
    estimator.fit(features, labels)
    return estimator, features, feature_names


def validate_feature_payload(
    payload: Any, feature_names: list[str]
) -> tuple[np.ndarray | None, list[str]]:
    """Require a finite, non-negative value for every model feature in model order."""
    if not isinstance(payload, dict):
        return None, ["Request body must be a JSON object containing all 30 features."]

    missing = [name for name in feature_names if name not in payload]
    if missing:
        return None, [f"Missing required feature: {name}." for name in missing]

    values: list[float] = []
    errors: list[str] = []
    for name in feature_names:
        value = payload[name]
        if isinstance(value, bool):
            errors.append(f"{name} must be a finite, non-negative number.")
            continue
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            errors.append(f"{name} must be a finite, non-negative number.")
            continue
        if not np.isfinite(number) or number < 0 or number > MAX_FEATURE_VALUE:
            errors.append(f"{name} must be between 0 and {MAX_FEATURE_VALUE:g}.")
            continue
        values.append(number)

    if errors:
        return None, errors
    return np.asarray(values, dtype=float).reshape(1, -1), []