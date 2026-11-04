"""Feature preparation for the Gurgaon house listing valuation model.

Only property attributes are used. Target-derived fields such as price_per_sqft,
property_name and description are deliberately excluded to prevent leakage.
"""
from __future__ import annotations

import math
import re
from typing import Any, Mapping


def _first(record: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = record.get(name)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _number(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip().replace(",", "")
    if text in {"", "-", "unknown", "none", "nan"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        result = float(match.group(0))
    except ValueError:
        return None
    return result if math.isfinite(result) else None


def _balcony_count(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"3+", "3 +", "more than 3"}:
        return 4.0  # conservative numeric representation for the 3+ category
    return _number(value)


def _locality(address: Any) -> str:
    """Extract a broad locality label, not a unique street address."""
    text = str(address or "").strip().lower()
    if not text:
        return "unknown"
    patterns = [
        r"sector\s*[- ]?\d{1,3}\s*[a-z]?",
        r"sushant\s+lok(?:\s+phase\s*\d+)?",
        r"dlf\s+phase\s*\d+",
        r"south\s+city(?:\s*[- ]?\s*\d+)?",
        r"nirvana\s+country",
        r"palam\s+vihar",
        r"civil\s+lines",
        r"golf\s+course\s+road",
        r"golf\s+course\s+extension\s+road",
        r"faridabad",
        r"gurgaon|gurugram",
    ]
    for pattern in patterns:
        found = re.search(pattern, text, flags=re.I)
        if found:
            return re.sub(r"\s+", " ", found.group(0)).strip()
    # Fall back to a shortened first address segment, excluding house numbers.
    first = text.split(",")[0].strip()
    first = re.sub(r"^\d+[a-z]?\s*[,/-]?\s*", "", first)
    return first[:60] or "unknown"


def _rare(value: str, category: str, rare_categories: Mapping[str, set[str]] | None) -> str:
    if rare_categories and value in rare_categories.get(category, set()):
        return "__RARE__"
    return value


def build_feature_dict(
    record: Mapping[str, Any],
    rare_categories: Mapping[str, set[str]] | None = None,
) -> dict[str, float | str]:
    """Convert a source row or buyer form into model features."""
    features: dict[str, float | str] = {}

    numeric_fields = {
        "area_sqft": _number(_first(record, "area", "living_area_sqft", "area_sqft")),
        "bedrooms": _number(_first(record, "bedRoom", "bedrooms", "bedroom")),
        "bathrooms": _number(_first(record, "bathroom", "bathrooms")),
        "floor_number": _number(_first(record, "floorNum", "floor_number", "floor")),
        "balcony_count": _balcony_count(_first(record, "balcony", "balconies", "balcony_count")),
    }
    for key, value in numeric_fields.items():
        if value is not None:
            features[key] = value
    if numeric_fields["area_sqft"] is not None and numeric_fields["area_sqft"] > 0:
        features["log_area_sqft"] = math.log1p(numeric_fields["area_sqft"])

    category_values = {
        "society": str(_first(record, "society", "project", "society_name") or "unknown").strip().lower(),
        "locality": _locality(_first(record, "address", "locality", "sector")),
        "facing": str(_first(record, "facing", "direction") or "unknown").strip().lower(),
        "possession": str(_first(record, "agePossession", "age_possession", "property_age") or "unknown").strip().lower(),
    }
    for key, value in category_values.items():
        features[key] = _rare(value, key, rare_categories)

    rooms = str(_first(record, "additionalRoom", "additional_rooms") or "").lower()
    feature_text = str(_first(record, "features", "amenities") or "").lower()
    furnishing = str(_first(record, "furnishDetails", "furnishing") or "").lower()
    for token in ("servant room", "store room", "pooja room", "study room"):
        features[f"extra_{token.replace(' ', '_')}"] = 1.0 if token in rooms else 0.0
    for token in ("parking", "private garden", "swimming pool", "fitness centre", "security"):
        features[f"amenity_{token.replace(' ', '_')}"] = 1.0 if token in feature_text else 0.0
    for token in ("modular kitchen", "wardrobe", "ac", "geyser"):
        features[f"furnishing_{token.replace(' ', '_')}"] = 1.0 if token in furnishing else 0.0

    return features
