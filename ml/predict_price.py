"""Load the trained model and estimate an individual house listing price."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import joblib
import numpy as np

try:
    from .feature_engineering import build_feature_dict
except ImportError:  # supports running this file directly
    from feature_engineering import build_feature_dict

ROOT = Path(__file__).resolve().parents[1]
MODEL_PATH = ROOT / "models" / "linear_regression_model.joblib"


def predict(record: dict[str, Any], model_path: Path = MODEL_PATH) -> dict[str, Any]:
    if not model_path.exists():
        raise FileNotFoundError(f"Trained model not found: {model_path}. Run ml/train_linear_regression.py first.")
    bundle = joblib.load(model_path)
    features = build_feature_dict(record, bundle.get("rare_categories"))
    vector = bundle["vectorizer"].transform([features])
    prediction = float(bundle["regressor"].predict(vector)[0])
    prediction = max(0.0, prediction)

    warnings = []
    ranges = bundle.get("numeric_ranges", {})
    for key, label in (("area_sqft", "Area"), ("bedrooms", "Bedrooms"), ("bathrooms", "Bathrooms")):
        if key in features and key in ranges:
            value = float(features[key])
            if value < ranges[key]["min"] or value > ranges[key]["max"]:
                warnings.append(f"{label} is outside the training-data range; estimate may be unreliable.")
    if not record.get("city") and not record.get("address") and not record.get("locality"):
        warnings.append("No city/locality was supplied. The model was trained on a geographically concentrated dataset.")

    area = features.get("area_sqft")
    return {
        "predicted_price_inr": round(prediction, 2),
        "predicted_price_crore": round(prediction / 10_000_000, 4),
        "predicted_price_per_sqft_inr": round(prediction / float(area), 2) if area and float(area) > 0 else None,
        "training_market": "Gurgaon-dominant house listing dataset",
        "model_type": bundle["model_type"],
        "target_unit": "INR",
        "warnings": warnings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate the price of a house from its details.")
    parser.add_argument("--area", type=float, required=True, help="Area in square feet")
    parser.add_argument("--bedrooms", type=float, required=True)
    parser.add_argument("--bathrooms", type=float, required=True)
    parser.add_argument("--society", default="unknown")
    parser.add_argument("--address", default="")
    parser.add_argument("--facing", default="unknown")
    parser.add_argument("--age", default="unknown", dest="age_possession")
    parser.add_argument("--floor", type=float, default=None, dest="floor_number")
    args = parser.parse_args()
    print(json.dumps(predict(vars(args)), indent=2))


if __name__ == "__main__":
    main()
