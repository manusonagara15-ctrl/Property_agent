"""Train and evaluate a Linear Regression house-listing price model.

Usage from project root:
    python ml/train_linear_regression.py --input data/processed/house_prices_training.csv

The input target `price` is assumed to be INR crore, as documented in the
cleaning workbook. The script converts it to INR for reporting and storage.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, train_test_split

try:
    from .feature_engineering import build_feature_dict
except ImportError:  # supports running this file directly from the project root
    from feature_engineering import build_feature_dict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_XLSX = ROOT / "data" / "raw" / "house_cleaned_analysis.xlsx"
DEFAULT_CSV = ROOT / "data" / "processed" / "house_prices_training.csv"
MODEL_PATH = ROOT / "models" / "linear_regression_model.joblib"
METRICS_PATH = ROOT / "reports" / "linear_regression_metrics.json"
PREDICTIONS_PATH = ROOT / "reports" / "linear_regression_test_predictions.csv"
COEFFICIENTS_PATH = ROOT / "reports" / "linear_regression_coefficients.csv"


def load_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(str(value).replace(",", "").strip())
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def get_target_crore(row: dict[str, Any]) -> float | None:
    # Workbook source field is `price`; training CSVs may use `price_crore`.
    for key in ("price", "price_crore", "price_in_crore"):
        val = numeric(row.get(key))
        if val is not None:
            return val
    # Optional explicit INR target in externally prepared CSVs.
    for key in ("price_inr", "estimated_value_inr"):
        val = numeric(row.get(key))
        if val is not None:
            return val / 10_000_000
    return None


def clean_records(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[float], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    targets: list[float] = []
    dropped_nonpositive = 0
    for row in rows:
        target = get_target_crore(row)
        area = numeric(row.get("area", row.get("area_sqft", row.get("living_area_sqft"))))
        if target is None or target <= 0 or area is None or area <= 0:
            dropped_nonpositive += 1
            continue
        records.append(row)
        targets.append(target * 10_000_000.0)
    if len(records) < 30:
        raise ValueError(f"Only {len(records)} valid records remain; at least 30 are needed for a basic experiment.")
    audit = {
        "rows_read": len(rows),
        "rows_used": len(records),
        "rows_excluded_missing_or_nonpositive_price_or_area": dropped_nonpositive,
        "target_assumption": "price column is INR crore; converted to INR by multiplying by 10,000,000",
        "leakage_fields_excluded": ["price_per_sqft", "property_name", "description", "rating", "nearbyLocations"],
    }
    return records, targets, audit


def make_rare_categories(records: list[dict[str, Any]], min_count: int = 3) -> dict[str, set[str]]:
    raw_features = [build_feature_dict(row) for row in records]
    category_keys = ("society", "locality", "facing", "possession")
    rare: dict[str, set[str]] = {}
    for key in category_keys:
        counts = Counter(str(f.get(key, "unknown")) for f in raw_features)
        rare[key] = {value for value, count in counts.items() if count < min_count}
    return rare


def calculate_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    nonzero = np.abs(y_true) > 1e-9
    mape = float(np.mean(np.abs((y_true[nonzero] - y_pred[nonzero]) / y_true[nonzero])) * 100) if np.any(nonzero) else None
    return {
        "mae_inr": float(mean_absolute_error(y_true, y_pred)),
        "rmse_inr": float(math.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)),
        "mape_percent": mape,
        "median_absolute_error_inr": float(np.median(np.abs(y_true - y_pred))),
    }


def cross_validate(records: list[dict[str, Any]], targets_inr: list[float], folds: int = 5) -> dict[str, Any]:
    """Five-fold shuffled CV; category bucketing/vectorization is fitted per fold."""
    y = np.asarray(targets_inr, dtype=float)
    splitter = KFold(n_splits=folds, shuffle=True, random_state=42)
    fold_results = []
    for fold_no, (train_idx, valid_idx) in enumerate(splitter.split(records), start=1):
        train_rows = [records[i] for i in train_idx]
        valid_rows = [records[i] for i in valid_idx]
        rare = make_rare_categories(train_rows)
        train_features = [build_feature_dict(row, rare) for row in train_rows]
        valid_features = [build_feature_dict(row, rare) for row in valid_rows]
        vectorizer = DictVectorizer(sparse=True, sort=True)
        X_train = vectorizer.fit_transform(train_features)
        X_valid = vectorizer.transform(valid_features)
        reg = LinearRegression().fit(X_train, y[train_idx])
        pred = np.maximum(0.0, reg.predict(X_valid))
        fold_metrics = calculate_metrics(y[valid_idx], pred)
        fold_metrics["fold"] = fold_no
        fold_results.append(fold_metrics)
    return {
        "folds": fold_results,
        "mean_mae_inr": float(np.mean([x["mae_inr"] for x in fold_results])),
        "mean_rmse_inr": float(np.mean([x["rmse_inr"] for x in fold_results])),
        "mean_r2": float(np.mean([x["r2"] for x in fold_results])),
        "mean_mape_percent": float(np.mean([x["mape_percent"] for x in fold_results if x["mape_percent"] is not None])),
    }


def save_training_csv(records: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not records:
        return
    headers = list(records[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def train(rows: list[dict[str, Any]], input_description: str) -> dict[str, Any]:
    records, targets_inr, audit = clean_records(rows)
    y = np.asarray(targets_inr, dtype=float)
    rare_categories = make_rare_categories(records)
    feature_rows = [build_feature_dict(row, rare_categories) for row in records]

    indices = np.arange(len(records))
    train_idx, test_idx = train_test_split(indices, test_size=0.20, random_state=42)
    X_train_rows = [feature_rows[i] for i in train_idx]
    X_test_rows = [feature_rows[i] for i in test_idx]
    y_train = y[train_idx]
    y_test = y[test_idx]

    vectorizer = DictVectorizer(sparse=True, sort=True)
    X_train = vectorizer.fit_transform(X_train_rows)
    X_test = vectorizer.transform(X_test_rows)

    # Ordinary least-squares Linear Regression on INR target. A log-area feature
    # is included alongside area to represent nonlinear size effects.
    regressor = LinearRegression()
    regressor.fit(X_train, y_train)
    y_pred = np.maximum(0.0, regressor.predict(X_test))

    metrics = calculate_metrics(y_test, y_pred)
    baseline_value = float(np.median(y_train))
    baseline_pred = np.full_like(y_test, baseline_value)
    baseline_metrics = calculate_metrics(y_test, baseline_pred)
    cross_validation_metrics = cross_validate(records, targets_inr, folds=5)

    # Feature range metadata supports safe input warnings at prediction time.
    numeric_ranges: dict[str, dict[str, float]] = {}
    for feature in ("area_sqft", "bedrooms", "bathrooms", "floor_number", "balcony_count"):
        vals = [float(r[feature]) for r in feature_rows if feature in r]
        if vals:
            numeric_ranges[feature] = {"min": min(vals), "max": max(vals), "median": float(np.median(vals))}

    bundle = {
        "model_type": "sklearn.linear_model.LinearRegression",
        "target_transform": "none (price_inr)",
        "vectorizer": vectorizer,
        "regressor": regressor,
        "rare_categories": rare_categories,
        "numeric_ranges": numeric_ranges,
        "target_unit": "INR",
        "training_rows": len(train_idx),
        "test_rows": len(test_idx),
        "feature_names": vectorizer.get_feature_names_out().tolist(),
        "training_source": input_description,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREDICTIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    COEFFICIENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)

    with COEFFICIENTS_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["encoded_feature", "coefficient_inr_per_unit"])
        coefficients = sorted(
            zip(vectorizer.get_feature_names_out(), regressor.coef_),
            key=lambda item: abs(float(item[1])),
            reverse=True,
        )
        for name, coefficient in coefficients:
            writer.writerow([name, float(coefficient)])

    metrics_doc = {
        "model": "Linear Regression (ordinary least squares) with INR target",
        "training_source": input_description,
        "dataset_audit": audit,
        "split": {"method": "random holdout", "test_size": 0.20, "random_state": 42},
        "training_rows": len(train_idx),
        "test_rows": len(test_idx),
        "features_count_after_encoding": len(bundle["feature_names"]),
        "features_used": ["area", "log(area)", "bedRoom", "bathroom", "floorNum", "balcony", "society", "locality from address", "facing", "agePossession", "additionalRoom flags", "amenity flags", "furnishing flags"],
        "features_excluded_to_prevent_leakage": audit["leakage_fields_excluded"],
        "model_metrics": metrics,
        "median_baseline_metrics": baseline_metrics,
        "five_fold_cross_validation": cross_validation_metrics,
        "interpretation": "Metrics measure prediction of source listing valuations in this dataset, not independently verified transaction market value. R2 and percentage error must be interpreted cautiously on this small, single-market dataset.",
    }
    METRICS_PATH.write_text(json.dumps(metrics_doc, indent=2), encoding="utf-8")

    with PREDICTIONS_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["source_row_index", "actual_price_inr", "predicted_price_inr", "absolute_error_inr", "absolute_percentage_error"])
        for idx, actual, pred in zip(test_idx, y_test, y_pred):
            ape = abs(actual - pred) / abs(actual) * 100 if actual else ""
            writer.writerow([int(idx), round(float(actual), 2), round(float(pred), 2), round(float(abs(actual-pred)), 2), round(float(ape), 2) if ape != "" else ""])

    return {"bundle": bundle, "metrics": metrics_doc, "y_test": y_test, "y_pred": y_pred}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=None, help="Training CSV. If omitted, use data/processed/house_prices_training.csv.")
    args = parser.parse_args()

    if args.input:
        rows = load_csv(args.input)
        input_description = str(args.input.resolve())
        save_training_csv(rows, DEFAULT_CSV)
    elif DEFAULT_CSV.exists():
        rows = load_csv(DEFAULT_CSV)
        input_description = str(DEFAULT_CSV.resolve())
    else:
        raise FileNotFoundError("Place the exported Cleaned Data CSV at data/processed/house_prices_training.csv or pass --input path/to/training.csv")

    result = train(rows, input_description)
    print("\nLINEAR REGRESSION TRAINING COMPLETE")
    print(f"Model: {MODEL_PATH}")
    print(f"Metrics: {METRICS_PATH}")
    print(f"Test predictions: {PREDICTIONS_PATH}")
    print(f"Feature coefficients: {COEFFICIENTS_PATH}")
    print(json.dumps(result["metrics"]["model_metrics"], indent=2))
    print("\nMedian baseline for comparison:")
    print(json.dumps(result["metrics"]["median_baseline_metrics"], indent=2))
    print("\nFive-fold cross-validation:")
    print(json.dumps(result["metrics"]["five_fold_cross_validation"], indent=2))


if __name__ == "__main__":
    main()
