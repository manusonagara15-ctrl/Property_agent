
import re
import pandas as pd


def parse_property_query(query):
    """Extract common property requirements from a search sentence."""
    text = query.lower().replace(",", "")
    filters = {}

    # Listing type: rent or sale
    if re.search(r"\b(rent|rental|to rent|for rent)\b", text):
        filters["listing_type"] = "Rent"
    elif re.search(r"\b(sale|buy|purchase|for sale|to buy)\b", text):
        filters["listing_type"] = "Sale"

    # Bedrooms: 1 BHK, 2 bedroom, etc.
    bedroom_match = re.search(
        r"\b([1-9])\s*(?:bhk|bedrooms?|beds?)\b", text
    )
    if bedroom_match:
        filters["bedrooms"] = int(bedroom_match.group(1))

    # Budget: under 20000, below 50 lakh, up to 2 crore
    budget_match = re.search(
        r"\b(?:under|below|less than|up to|max(?:imum)?|within)\s*"
        r"(?:rs\.?|inr|₹)?\s*"
        r"(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lakh|l|thousand|k)?\b",
        text,
    )

    if budget_match:
        amount = float(budget_match.group(1))
        unit = budget_match.group(2) or ""

        if unit.startswith("crore") or unit == "cr":
            amount *= 10_000_000
        elif unit.startswith("lakh") or unit == "l":
            amount *= 100_000
        elif unit in ("thousand", "k"):
            amount *= 1_000

        filters["max_price"] = amount

    # Property type
    if re.search(r"\b(apartment|flat)\b", text):
        filters["property_type"] = "Apartment"
    elif re.search(r"\b(villa)\b", text):
        filters["property_type"] = "Villa"
    elif re.search(r"\b(house|bungalow)\b", text):
        filters["property_type"] = "House"
    elif re.search(r"\b(plot|land)\b", text):
        filters["property_type"] = "Plot"
    elif re.search(r"\b(commercial|office|shop)\b", text):
        filters["property_type"] = "Commercial"

    return filters


def search_properties(properties, query):
    """Filter property records using extracted search requirements."""
    filters = parse_property_query(query)
    results = properties.copy()

    if not query.strip():
        return results, filters

    # Match city names even when the user types different capitalization.
    cities = properties["city"].dropna().unique()
    normalized_query = query.lower()

    matched_city = next(
        (city for city in cities if str(city).lower() in normalized_query),
        None,
    )

    if matched_city:
        results = results[
            results["city"].str.lower() == str(matched_city).lower()
        ]

    if "listing_type" in filters:
        results = results[
            results["listing_type"] == filters["listing_type"]
        ]

    if "property_type" in filters:
        results = results[
            results["property_type"].str.lower()
            == filters["property_type"].lower()
        ]

    if "bedrooms" in filters:
        results = results[
            results["bedrooms"] == filters["bedrooms"]
        ]

    if "max_price" in filters:
        results = results[results["price"] <= filters["max_price"]]

    return results, filters
