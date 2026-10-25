"""Evaluate candidate classifiers with a fixed hold-out set and 5-fold CV."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.base import clone
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC
from sklearn.tree import DecisionTreeClassifier
from sklearn.linear_model import LogisticRegression

from ml.model_service import MODEL_NAME, MODEL_VERSION, build_random_forest, load_dataset

BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_PATH = BASE_DIR / "data" / "model_evaluation.json"


def candidate_models() -> dict[str, Pipeline]:
    """Build candidate pipelines; scale only algorithms sensitive to feature scale."""
    return {
        "Logistic Regression": Pipeline([
            ("scale", StandardScaler()),
            ("classifier", LogisticRegression(max_iter=3000, random_state=42)),
        ]),
        "Decision Tree": Pipeline([
            ("classifier", DecisionTreeClassifier(random_state=42)),
        ]),
        "Support Vector Machine": Pipeline([
            ("scale", StandardScaler()),
            ("classifier", SVC(kernel="rbf", probability=True, random_state=42)),
        ]),
        MODEL_NAME: Pipeline([
            ("classifier", build_random_forest()),
        ]),
    }


def _scores(model: Any, x_test: np.ndarray, y_test: np.ndarray) -> dict[str, Any]:
    predictions = model.predict(x_test)
    probabilities = model.predict_proba(x_test)[:, 1]
    # Label order [benign=0, malignant=1]: [[TN, FP], [FN, TP]].
    tn, fp, fn, tp = confusion_matrix(y_test, predictions, labels=[0, 1]).ravel()
    return {
        "accuracy": float(accuracy_score(y_test, predictions)),
        "sensitivity_malignant": float(recall_score(y_test, predictions, pos_label=1, zero_division=0)),
        "specificity_benign": float(recall_score(y_test, predictions, pos_label=0, zero_division=0)),
        "precision_malignant": float(precision_score(y_test, predictions, pos_label=1, zero_division=0)),
        "f1_malignant": float(f1_score(y_test, predictions, pos_label=1, zero_division=0)),
        "roc_auc": float(roc_auc_score(y_test, probabilities)),
        "confusion_matrix": {"true_benign": int(tn), "false_malignant": int(fp), "missed_malignant": int(fn), "true_malignant": int(tp)},
    }


def evaluate() -> dict[str, Any]:
    features, labels, feature_names, dataset_metadata = load_dataset()
    x_train, x_test, y_train, y_test = train_test_split(
        features, labels, test_size=0.2, random_state=42, stratify=labels
    )

    comparisons: list[dict[str, Any]] = []
    fitted_models: dict[str, Any] = {}
    for name, candidate in candidate_models().items():
        cv_accuracy = cross_val_score(
            clone(candidate), x_train, y_train, cv=5, scoring="accuracy", n_jobs=-1
        )
        fitted = clone(candidate).fit(x_train, y_train)
        fitted_models[name] = fitted
        comparisons.append({
            "name": name,
            "test": _scores(fitted, x_test, y_test),
            "cross_validation": {
                "folds": 5,
                "accuracy_mean": float(cv_accuracy.mean()),
                "accuracy_std": float(cv_accuracy.std(ddof=1)),
            },
        })

    selected = fitted_models[MODEL_NAME]
    fitted_tree = selected.named_steps["classifier"]
    importance = sorted(
        zip(feature_names, fitted_tree.feature_importances_),
        key=lambda pair: pair[1],
        reverse=True,
    )[:10]

    return {
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "selected_model": "Random Forest",
        "dataset": dataset_metadata,
        "evaluation_protocol": {
            "holdout": "Stratified 80/20 train/test split; random_state=42",
            "cross_validation": "5-fold stratified cross-validation on training partition only",
            "positive_class": "malignant",
            "negative_class": "benign",
            "confusion_matrix_layout": "[[true benign, false malignant], [missed malignant, true malignant]]",
            "metrics_source": "held-out test split; cross-validation accuracy reported separately",
            "clinical_validation": False,
        },
        "split": {"train_samples": int(len(y_train)), "test_samples": int(len(y_test))},
        "models": comparisons,
        "feature_importance": [{"name": name, "importance": float(value)} for name, value in importance],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "warning": "Research/education demonstration only. This model is not a diagnostic or clinical decision-support device.",
    }


def main() -> None:
    results = evaluate()
    OUTPUT_PATH.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(f"Saved held-out evaluation results to {OUTPUT_PATH}")
    for row in results["models"]:
        metrics = row["test"]
        print(
            f"{row['name']}: accuracy={metrics['accuracy']:.3f}, "
            f"sensitivity={metrics['sensitivity_malignant']:.3f}, "
            f"specificity={metrics['specificity_benign']:.3f}, "
            f"ROC AUC={metrics['roc_auc']:.3f}"
        )


if __name__ == "__main__":
    main()