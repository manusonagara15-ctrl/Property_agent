
import json
import urllib.error
import urllib.request


OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.2:3b"


def extract_property_filters(user_message: str) -> dict:
    """
    Ask the local Ollama model to extract property search filters.
    Returns structured data, not invented property listings.
    """
    prompt = f"""
You extract property search filters for an Indian real-estate application.

Read the user's message and return ONLY a valid JSON object with these keys:
{{
  "city": null,
  "listing_type": null,
  "property_type": null,
  "bedrooms": null,
  "max_price": null
}}

Rules:
- city: city explicitly mentioned, otherwise null.
- listing_type: "Rent" or "Sale", otherwise null.
- property_type: "Apartment", "Villa", "House", "Plot", or "Commercial", otherwise null.
- bedrooms: integer, otherwise null.
- max_price: numeric maximum budget in INR, otherwise null.
- Do not guess missing values.
- "under 20000", "below 20k", and "budget 20000" mean max_price 20000.
- "2 BHK" means bedrooms 2.
- Return JSON only.

User message: {json.dumps(user_message)}
"""

    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {
            "temperature": 0,
            "num_predict": 160
        }
    }

    request = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))

        extracted = json.loads(result.get("response", "{}"))

        allowed_listing_types = {"Rent", "Sale"}
        allowed_property_types = {
            "Apartment", "Villa", "House", "Plot", "Commercial"
        }

        listing_type = extracted.get("listing_type")
        if listing_type not in allowed_listing_types:
            listing_type = None

        property_type = extracted.get("property_type")
        if property_type not in allowed_property_types:
            property_type = None

        bedrooms = extracted.get("bedrooms")
        try:
            bedrooms = int(bedrooms) if bedrooms is not None else None
            if bedrooms is not None and not 0 < bedrooms < 100:
                bedrooms = None
        except (TypeError, ValueError):
            bedrooms = None

        max_price = extracted.get("max_price")
        try:
            max_price = float(max_price) if max_price is not None else None
            if max_price is not None and max_price <= 0:
                max_price = None
        except (TypeError, ValueError):
            max_price = None

        city = extracted.get("city")
        if not isinstance(city, str) or not city.strip():
            city = None
        else:
            city = city.strip()

        return {
            "city": city,
            "listing_type": listing_type,
            "property_type": property_type,
            "bedrooms": bedrooms,
            "max_price": max_price
        }

    except urllib.error.URLError as exc:
        raise RuntimeError(
            "Cannot connect to Ollama. Check that Ollama is running."
        ) from exc
    except (TimeoutError, json.JSONDecodeError, KeyError) as exc:
        raise RuntimeError(
            "The local AI did not return valid filters. Please try again."
        ) from exc


def build_search_query(filters: dict, original_message: str) -> str:
    """
    Convert extracted filters into a search phrase understood by
    the existing property_search.py service.
    """
    parts = []

    if filters.get("bedrooms"):
        parts.append(f'{filters["bedrooms"]} BHK')

    if filters.get("property_type"):
        parts.append(filters["property_type"])

    if filters.get("listing_type"):
        parts.append(f'for {filters["listing_type"].lower()}')

    if filters.get("city"):
        parts.append(f'in {filters["city"]}')

    if filters.get("max_price") is not None:
        parts.append(f'under {filters["max_price"]:g}')

    # Preserve the user's original wording when the model cannot
    # confidently identify any search filters.
    if not parts:
        return original_message

    return " ".join(parts)
