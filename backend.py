"""Backend logic for Property Agent Bot.

Contains configuration, local AI/search parsing, SQLite operations, lead/CRM
functions, image-path resolution, and property recommendations. This module
contains no Streamlit page layout. Keep it beside frontend.py in the project root.
"""
import html
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import textwrap
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd


# =========================================================
# CONFIGURATION
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "property_agent.db"
ASSETS_DIR = BASE_DIR / "assets"
PROPERTY_IMAGES_DIR = ASSETS_DIR / "properties"
USER_UPLOADS_DIR = PROPERTY_IMAGES_DIR / "user_uploads"

PROPERTY_IMAGE_MAP = {
    1: PROPERTY_IMAGES_DIR / "apartment.jpg",
    2: PROPERTY_IMAGES_DIR / "villa.jpg",
    3: PROPERTY_IMAGES_DIR / "flat.jpg",
}


# =========================================================
# INLINE LOCAL AI + PROPERTY SEARCH SERVICES
# The app is self-contained; the services/ folder is not needed at runtime.
# =========================================================
OLLAMA_URL = os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate")
MODEL_NAME = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")

def extract_property_filters(user_message: str) -> dict:
    """Extract validated search filters with local Ollama; no listings are invented."""
    prompt = f"""
You extract property search filters for an Indian real-estate application.
Read the user's message and return ONLY a valid JSON object with keys city, listing_type, property_type, bedrooms, max_price.
Rules: city must be explicitly mentioned; listing_type is Rent or Sale; property_type is Apartment, Villa, House, Plot, or Commercial; bedrooms is an integer; max_price is numeric INR; do not guess missing values.
'under 20000', 'below 20k', and 'budget 20000' mean max_price 20000. '2 BHK' means bedrooms 2. Return JSON only.
User message: {json.dumps(user_message)}
"""
    payload = {
        "model": MODEL_NAME, "prompt": prompt, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_predict": 160},
    }
    request = urllib.request.Request(
        OLLAMA_URL, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            result = json.loads(response.read().decode("utf-8"))
        extracted = json.loads(result.get("response", "{}"))
        if not isinstance(extracted, dict):
            raise ValueError("The local AI response was not a JSON object.")
        listing_raw = str(extracted.get("listing_type") or "").strip().casefold()
        listing = {"rent": "Rent", "sale": "Sale"}.get(listing_raw)
        type_raw = str(extracted.get("property_type") or "").strip().casefold()
        type_map = {"apartment": "Apartment", "flat": "Apartment", "villa": "Villa",
                    "house": "House", "plot": "Plot", "commercial": "Commercial"}
        property_type = type_map.get(type_raw)
        try:
            bedrooms = int(extracted["bedrooms"]) if extracted.get("bedrooms") is not None else None
            if bedrooms is not None and not 0 < bedrooms < 100: bedrooms = None
        except (TypeError, ValueError, OverflowError):
            bedrooms = None
        try:
            max_price = float(extracted["max_price"]) if extracted.get("max_price") is not None else None
            if max_price is not None and (not pd.notna(max_price) or max_price <= 0): max_price = None
        except (TypeError, ValueError, OverflowError):
            max_price = None
        city = extracted.get("city")
        city = city.strip() if isinstance(city, str) and city.strip() else None
        return {"city": city, "listing_type": listing, "property_type": property_type,
                "bedrooms": bedrooms, "max_price": max_price}
    except urllib.error.URLError as exc:
        raise RuntimeError("Cannot connect to Ollama. Check that Ollama is running.") from exc
    except (TimeoutError, json.JSONDecodeError, KeyError, ValueError) as exc:
        raise RuntimeError("The local AI did not return valid filters. Please try again.") from exc

def build_search_query(filters: dict, original_message: str) -> str:
    """Turn structured filters into a search phrase for the fallback parser."""
    parts = []
    if filters.get("bedrooms"): parts.append(f'{filters["bedrooms"]} BHK')
    if filters.get("property_type"): parts.append(filters["property_type"])
    if filters.get("listing_type"): parts.append(f'for {filters["listing_type"].lower()}')
    if filters.get("city"): parts.append(f'in {filters["city"]}')
    if filters.get("max_price") is not None: parts.append(f'under {filters["max_price"]:g}')
    return " ".join(parts) if parts else original_message

def parse_property_query(query):
    """Parse common real-estate requirements, including 'budget 20k'."""
    text = str(query or "").lower().replace(",", "").replace("₹", " rs ")
    filters = {}
    if re.search(r"\b(rent|rental|to rent|for rent)\b", text):
        filters["listing_type"] = "Rent"
    elif re.search(r"\b(sale|buy|purchase|for sale|to buy)\b", text):
        filters["listing_type"] = "Sale"
    bedroom_match = re.search(r"\b([1-9])\s*(?:bhk|bedrooms?|beds?)\b", text)
    if bedroom_match: filters["bedrooms"] = int(bedroom_match.group(1))
    budget_match = re.search(
        r"\b(?:under|below|less than|up to|max(?:imum)?|within|budget(?: of)?)\s*"
        r"(?:rs\.?|inr)?\s*(\d+(?:\.\d+)?)\s*(crores?|cr|lakhs?|lakh|thousand|k)?\b", text
    )
    if budget_match:
        amount = float(budget_match.group(1)); unit = (budget_match.group(2) or "").casefold()
        if unit.startswith("crore") or unit == "cr": amount *= 10_000_000
        elif unit.startswith("lakh") or unit == "lakh": amount *= 100_000
        elif unit in ("thousand", "k"): amount *= 1_000
        filters["max_price"] = amount
    type_patterns = [(r"\b(apartment|flat)\b", "Apartment"), (r"\bvilla\b", "Villa"),
                     (r"\b(house|bungalow)\b", "House"), (r"\b(plot|land)\b", "Plot"),
                     (r"\b(commercial|office|shop)\b", "Commercial")]
    for pattern, canonical in type_patterns:
        if re.search(pattern, text): filters["property_type"] = canonical; break
    return filters

def search_properties(properties, query):
    """Filter a DataFrame by case-insensitive text-derived property requirements."""
    filters = parse_property_query(query)
    results = properties.copy()
    if not str(query or "").strip(): return results, filters
    if "city" in results.columns:
        normalized_query = str(query).casefold()
        cities = results["city"].dropna().astype(str).unique().tolist()
        matched_city = next((city for city in sorted(cities, key=len, reverse=True)
                             if city.strip() and city.casefold() in normalized_query), None)
        if matched_city:
            results = results[results["city"].astype(str).str.casefold() == matched_city.casefold()]
    comparisons = {"listing_type": "listing_type", "property_type": "property_type"}
    for key, column in comparisons.items():
        if key in filters and column in results.columns:
            results = results[results[column].astype(str).str.strip().str.casefold() == filters[key].casefold()]
    if "bedrooms" in filters and "bedrooms" in results.columns:
        results = results[pd.to_numeric(results["bedrooms"], errors="coerce") == filters["bedrooms"]]
    if "max_price" in filters and "price" in results.columns:
        results = results[pd.to_numeric(results["price"], errors="coerce") <= filters["max_price"]]
    return results, filters


@contextmanager
def _sqlite_connection(path, timeout=20):
    """Transaction-scoped SQLite connection that is always closed."""
    conn = sqlite3.connect(path, timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def get_connection():
    """Open the application database and close it when the block exits."""
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run init_db.py first."
        )
    with _sqlite_connection(DB_PATH) as conn:
        yield conn


def get_properties():
    with get_connection() as conn:
        return pd.read_sql_query("SELECT * FROM properties ORDER BY id DESC", conn)


def get_property_columns():
    with get_connection() as conn:
        rows = conn.execute("PRAGMA table_info(properties)").fetchall()
    return {row[1] for row in rows}


def ensure_optional_columns():
    """Add optional fields used by this app to older databases if missing."""
    optional_columns = {
        "area_sqft": "REAL",
        "image_path": "TEXT",
        "description": "TEXT",
        "amenities": "TEXT",
    }
    columns = get_property_columns()
    with get_connection() as conn:
        for column, sql_type in optional_columns.items():
            if column not in columns:
                conn.execute(f'ALTER TABLE properties ADD COLUMN "{column}" {sql_type}')


def ensure_enquiries_table():
    """Create and safely upgrade local CRM tables for existing project databases."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS enquiries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER,
                property_title TEXT NOT NULL,
                property_city TEXT NOT NULL DEFAULT '',
                customer_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'Other',
                preferred_budget REAL,
                move_in_timeline TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'Medium',
                qualification_score INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'New',
                follow_up_date TEXT,
                agent_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        existing = {row[1] for row in conn.execute("PRAGMA table_info(enquiries)").fetchall()}
        migrations = {
            "property_city": "TEXT NOT NULL DEFAULT ''",
            "source": "TEXT NOT NULL DEFAULT 'Other'",
            "preferred_budget": "REAL",
            "move_in_timeline": "TEXT NOT NULL DEFAULT ''",
            "priority": "TEXT NOT NULL DEFAULT 'Medium'",
            "qualification_score": "INTEGER NOT NULL DEFAULT 0",
        }
        for column, sql_type in migrations.items():
            if column not in existing:
                conn.execute(f'ALTER TABLE enquiries ADD COLUMN "{column}" {sql_type}')

        conn.execute("CREATE INDEX IF NOT EXISTS idx_enquiries_status ON enquiries(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enquiries_property_id ON enquiries(property_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enquiries_phone ON enquiries(phone)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enquiries_follow_up ON enquiries(follow_up_date)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL,
                interaction_type TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_enquiry ON lead_interactions(enquiry_id, created_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS property_ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                property_id INTEGER,
                property_title TEXT NOT NULL,
                reviewer_name TEXT NOT NULL DEFAULT '',
                rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                review TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_property_ratings_property ON property_ratings(property_id)")


def ensure_phase9_tables():
    """Create Phase 9 tables without overwriting existing customer or property data."""
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS saved_property_searches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                query TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS customer_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL UNIQUE,
                preferred_city TEXT NOT NULL DEFAULT '',
                preferred_property_type TEXT NOT NULL DEFAULT '',
                preferred_listing_type TEXT NOT NULL DEFAULT '',
                bedrooms INTEGER,
                max_budget REAL,
                min_area_sqft REAL,
                max_area_sqft REAL,
                must_have_amenities TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_customer_preferences_enquiry ON customer_preferences(enquiry_id)")


def save_property_search(name, query):
    """Save a named natural-language search; names are unique case-insensitively."""
    name = str(name or '').strip()
    query = str(query or '').strip()
    if not name:
        raise ValueError('Enter a name for this saved search.')
    if len(name) > 100:
        raise ValueError('The saved-search name must be 100 characters or fewer.')
    if not query:
        raise ValueError('Enter a search query before saving.')
    if len(query) > 500:
        raise ValueError('The search query must be 500 characters or fewer.')
    with get_connection() as conn:
        conn.execute(
            'INSERT INTO saved_property_searches (name, query) VALUES (?, ?)',
            (name, query),
        )


def get_saved_property_searches():
    """Return saved searches newest-first."""
    with get_connection() as conn:
        return pd.read_sql_query(
            'SELECT id, name, query, created_at, updated_at FROM saved_property_searches ORDER BY datetime(updated_at) DESC, id DESC',
            conn,
        )


def delete_saved_property_search(search_id):
    """Delete one saved search without touching properties or customer records."""
    with get_connection() as conn:
        cursor = conn.execute('DELETE FROM saved_property_searches WHERE id = ?', (int(search_id),))
        return cursor.rowcount > 0


def get_customer_preferences(enquiry_id):
    """Get the preference profile associated with one CRM enquiry, or None."""
    with get_connection() as conn:
        row = conn.execute(
            'SELECT * FROM customer_preferences WHERE enquiry_id = ?', (int(enquiry_id),)
        ).fetchone()
        if row is None:
            return None
        columns = [item[1] for item in conn.execute('PRAGMA table_info(customer_preferences)').fetchall()]
        return dict(zip(columns, row))


def save_customer_preferences(enquiry_id, preferences):
    """Create or update a preference profile for an existing enquiry."""
    try:
        enquiry_id = int(enquiry_id)
    except (TypeError, ValueError):
        raise ValueError('Select a valid customer enquiry.')

    def optional_number(key, cast=float):
        value = preferences.get(key)
        if value in (None, '', 0, 0.0):
            return None
        try:
            number = cast(value)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f'Invalid value for {key.replace("_", " ")}.')
        if number <= 0:
            return None
        return number

    city = str(preferences.get('preferred_city') or '').strip()
    property_type = str(preferences.get('preferred_property_type') or '').strip()
    listing_type = str(preferences.get('preferred_listing_type') or '').strip()
    if property_type not in {'', 'Apartment', 'Villa', 'House', 'Plot', 'Commercial'}:
        raise ValueError('Choose a supported property type.')
    if listing_type not in {'', 'Rent', 'Sale'}:
        raise ValueError('Choose Rent, Sale, or Any listing type.')
    bedrooms = optional_number('bedrooms', int)
    if bedrooms is not None and not 1 <= bedrooms <= 20:
        raise ValueError('Bedrooms must be between 1 and 20.')
    max_budget = optional_number('max_budget', float)
    min_area = optional_number('min_area_sqft', float)
    max_area = optional_number('max_area_sqft', float)
    if min_area is not None and max_area is not None and min_area > max_area:
        raise ValueError('Minimum area cannot be greater than maximum area.')
    amenities = str(preferences.get('must_have_amenities') or '').strip()[:500]
    notes = str(preferences.get('notes') or '').strip()[:2000]

    with get_connection() as conn:
        exists = conn.execute('SELECT 1 FROM enquiries WHERE id = ?', (enquiry_id,)).fetchone()
        if not exists:
            raise ValueError('This enquiry no longer exists. Refresh and select another lead.')
        conn.execute(
            """
            INSERT INTO customer_preferences (
                enquiry_id, preferred_city, preferred_property_type, preferred_listing_type,
                bedrooms, max_budget, min_area_sqft, max_area_sqft, must_have_amenities, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(enquiry_id) DO UPDATE SET
                preferred_city=excluded.preferred_city,
                preferred_property_type=excluded.preferred_property_type,
                preferred_listing_type=excluded.preferred_listing_type,
                bedrooms=excluded.bedrooms,
                max_budget=excluded.max_budget,
                min_area_sqft=excluded.min_area_sqft,
                max_area_sqft=excluded.max_area_sqft,
                must_have_amenities=excluded.must_have_amenities,
                notes=excluded.notes,
                updated_at=datetime('now', 'localtime')
            """,
            (enquiry_id, city, property_type, listing_type, bedrooms, max_budget,
             min_area, max_area, amenities, notes),
        )


def get_smart_followup_suggestions(today=None):
    """Return rule-based follow-up suggestions; no messages are sent automatically."""
    today = today or date.today()
    today = today if isinstance(today, date) else date.fromisoformat(str(today))
    enquiries = get_enquiries()
    columns = ['id', 'customer_name', 'phone', 'property_title', 'property_city', 'status',
               'priority', 'follow_up_date', 'move_in_timeline', 'updated_at',
               'suggested_action', 'reason', 'recommended_date']
    if enquiries.empty:
        return pd.DataFrame(columns=columns)

    closed_statuses = {'closed', 'won', 'lost', 'converted'}
    suggestions = []
    for lead in enquiries.to_dict('records'):
        status = str(lead.get('status') or 'New').strip()
        if status.casefold() in closed_statuses:
            continue
        follow_date = None
        try:
            raw_date = str(lead.get('follow_up_date') or '').strip()
            if raw_date:
                follow_date = date.fromisoformat(raw_date[:10])
        except (TypeError, ValueError):
            follow_date = None
        priority = str(lead.get('priority') or 'Medium').title()
        timeline = str(lead.get('move_in_timeline') or '').casefold()
        updated_date = None
        try:
            updated_date = datetime.fromisoformat(str(lead.get('updated_at') or lead.get('created_at'))[:19]).date()
        except (TypeError, ValueError):
            try:
                updated_date = datetime.fromisoformat(str(lead.get('created_at') or '')[:19]).date()
            except (TypeError, ValueError):
                updated_date = None

        if follow_date is not None and follow_date < today:
            action = 'Follow up now — overdue'
            reason = f'Follow-up date was {follow_date.isoformat()}.'
            rec_date = today
        elif follow_date == today:
            action = 'Follow up today'
            reason = 'The scheduled follow-up date is today.'
            rec_date = today
        elif follow_date is not None and 0 < (follow_date - today).days <= 7:
            action = 'Prepare upcoming follow-up'
            reason = f'Follow-up is scheduled for {follow_date.isoformat()}.'
            rec_date = follow_date
        elif follow_date is None:
            days_since_update = (today - updated_date).days if updated_date else None
            if priority == 'High' or 'immediately' in timeline or '2 weeks' in timeline:
                action = 'Prioritise contact and schedule follow-up'
                reason = 'Lead is marked High priority or has a near-term move-in timeline.'
            elif days_since_update is not None and days_since_update >= 7:
                action = 'Re-engage inactive lead'
                reason = f'No follow-up date is scheduled and the record has not been updated for {days_since_update} days.'
            else:
                action = 'Schedule the first follow-up'
                reason = 'This active lead does not have a follow-up date.'
            rec_date = today
        else:
            if priority != 'High' and 'immediately' not in timeline:
                continue
            action = 'Prepare early contact plan'
            reason = f'Follow-up is set for {follow_date.isoformat()}, but the lead is high priority or time-sensitive.'
            rec_date = max(today, follow_date - timedelta(days=3))

        lead['suggested_action'] = action
        lead['reason'] = reason
        lead['recommended_date'] = rec_date.isoformat()
        suggestions.append(lead)

    suggestions.sort(key=lambda item: (
        0 if 'overdue' in item['suggested_action'].casefold() else
        1 if 'today' in item['suggested_action'].casefold() or 'now' in item['suggested_action'].casefold() else
        2 if str(item.get('priority', '')).casefold() == 'high' else 3,
        str(item.get('recommended_date') or ''),
        int(item.get('id') or 0),
    ))
    return pd.DataFrame(suggestions, columns=columns)


def get_customer_preference_matches(properties_frame, preferences, limit=5):
    """Rank listings for a preference profile, then enforce area and must-have amenities."""
    preferences = preferences or {}
    filters = {}
    city = str(preferences.get('preferred_city') or '').strip()
    property_type = str(preferences.get('preferred_property_type') or '').strip()
    listing_type = str(preferences.get('preferred_listing_type') or '').strip()
    if city: filters['city'] = city
    if property_type: filters['property_type'] = property_type
    if listing_type: filters['listing_type'] = listing_type
    if preferences.get('bedrooms') not in (None, ''):
        filters['bedrooms'] = int(preferences['bedrooms'])
    if preferences.get('max_budget') not in (None, '', 0, 0.0):
        filters['max_price'] = float(preferences['max_budget'])

    records, exact = get_ranked_recommendations(properties_frame, filters, limit=max(limit * 5, len(properties_frame)))
    minimum_area = preferences.get('min_area_sqft')
    maximum_area = preferences.get('max_area_sqft')
    amenity_terms = [part.strip().casefold() for part in re.split(r'[,;\n]+', str(preferences.get('must_have_amenities') or '')) if part.strip()]
    filtered = []
    for record in records:
        try:
            area = float(record.get('area_sqft') or 0)
        except (TypeError, ValueError):
            area = 0
        if minimum_area not in (None, '', 0, 0.0) and area < float(minimum_area):
            continue
        if maximum_area not in (None, '', 0, 0.0) and area > float(maximum_area):
            continue
        searchable = ' '.join(str(record.get(key) or '') for key in ('amenities', 'description', 'title')).casefold()
        if amenity_terms and not all(term in searchable for term in amenity_terms):
            continue
        filtered.append(record)
        if len(filtered) >= max(1, int(limit)):
            break
    return filtered, exact


def get_enquiries():
    """Read enquiries newest-first; property_title is retained as a historical snapshot."""
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT * FROM enquiries ORDER BY datetime(created_at) DESC, id DESC", conn
        )


def add_enquiry(data):
    """Persist a customer enquiry and its qualification fields in SQLite."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO enquiries (
                property_id, property_title, property_city, customer_name, phone, email,
                message, source, preferred_budget, move_in_timeline, priority,
                qualification_score, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'New')
            """,
            (
                data.get("property_id"), data["property_title"], data.get("property_city", ""),
                data["customer_name"], data["phone"], data.get("email", ""),
                data.get("message", ""), data.get("source", "Other"),
                data.get("preferred_budget"), data.get("move_in_timeline", ""),
                data.get("priority", "Medium"), int(data.get("qualification_score", 0)),
            ),
        )


def update_enquiry(
    enquiry_id, status, follow_up_date, agent_notes, priority="Medium",
    preferred_budget=None, move_in_timeline="", qualification_score=0
):
    """Update workflow fields for one enquiry."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            UPDATE enquiries
            SET status = ?, follow_up_date = ?, agent_notes = ?, priority = ?,
                preferred_budget = ?, move_in_timeline = ?, qualification_score = ?,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
            """,
            (
                status, follow_up_date, agent_notes, priority, preferred_budget,
                move_in_timeline, int(qualification_score), int(enquiry_id),
            ),
        )
        if cursor.rowcount == 0:
            raise ValueError("This enquiry no longer exists. Refresh the page and try again.")


def add_interaction(enquiry_id, interaction_type, notes):
    """Add an immutable timeline entry for a call, visit, message, or status change."""
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM enquiries WHERE id = ?", (int(enquiry_id),)).fetchone()
        if not exists:
            raise ValueError("This enquiry no longer exists.")
        conn.execute(
            "INSERT INTO lead_interactions (enquiry_id, interaction_type, notes) VALUES (?, ?, ?)",
            (int(enquiry_id), str(interaction_type), str(notes).strip()),
        )
        conn.execute(
            "UPDATE enquiries SET updated_at = datetime('now', 'localtime') WHERE id = ?",
            (int(enquiry_id),),
        )


def get_interactions(enquiry_id):
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT interaction_type, notes, created_at FROM lead_interactions WHERE enquiry_id = ? ORDER BY datetime(created_at) DESC, id DESC",
            conn, params=(int(enquiry_id),)
        )


def find_duplicate_enquiries(phone, property_id=None):
    """Find possible duplicates by digits-only phone; matching a property is an extra signal."""
    digits = ''.join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) < 7:
        return pd.DataFrame()
    frame = get_enquiries()
    if frame.empty or "phone" not in frame.columns:
        return pd.DataFrame()
    def only_digits(value):
        return ''.join(ch for ch in str(value or '') if ch.isdigit())
    mask = frame["phone"].map(only_digits) == digits
    matches = frame[mask].copy()
    if property_id is not None and "property_id" in matches.columns:
        same_property = matches[matches["property_id"].fillna(-1).astype(int) == int(property_id)]
        if not same_property.empty:
            return same_property
    return matches



def _duplicate_normalize_text(value):
    """Normalize user-facing text for conservative duplicate comparisons."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return " ".join(str(value).strip().lower().split())


def _duplicate_normalize_number(value):
    """Normalize numbers so values like 3 and 3.0 compare equally."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return _duplicate_normalize_text(value)


def find_duplicate_property_groups(properties_frame):
    """Find exact-like duplicate listings based on their core searchable fields.

    The lowest database ID is treated as the record to keep; only the other
    records in each group are offered as deletion candidates.
    """
    if properties_frame is None or properties_frame.empty:
        return []

    groups = {}
    for _, row in properties_frame.iterrows():
        record = row.to_dict()
        title = _duplicate_normalize_text(record.get("title"))
        city = _duplicate_normalize_text(record.get("city"))
        if not title or not city:
            # Do not guess at duplicates when the two key identity fields are absent.
            continue
        signature = (
            title,
            city,
            _duplicate_normalize_text(record.get("property_type")),
            _duplicate_normalize_text(record.get("listing_type")),
            _duplicate_normalize_number(record.get("price")),
            _duplicate_normalize_number(record.get("bedrooms")),
            _duplicate_normalize_number(record.get("area_sqft")),
        )
        groups.setdefault(signature, []).append(record)

    duplicate_groups = []
    for records in groups.values():
        if len(records) < 2:
            continue
        records.sort(key=lambda rec: int(rec.get("id", 0) or 0))
        duplicate_groups.append({
            "keep": records[0],
            "candidates": records[1:],
            "records": records,
        })
    duplicate_groups.sort(key=lambda group: int(group["keep"].get("id", 0) or 0))
    return duplicate_groups


def find_duplicate_enquiry_groups(enquiries_frame):
    """Find likely duplicate enquiries using phone, name, property, and message.

    These are candidates for manual review, not guaranteed duplicates. The oldest
    (lowest-ID) record is marked to keep by default.
    """
    if enquiries_frame is None or enquiries_frame.empty:
        return []

    groups = {}
    for _, row in enquiries_frame.iterrows():
        record = row.to_dict()
        digits = "".join(ch for ch in str(record.get("phone") or "") if ch.isdigit())
        name = _duplicate_normalize_text(record.get("customer_name"))
        property_id = record.get("property_id")
        if property_id is not None and not _is_missing_scalar(property_id):
            property_key = "id:" + _duplicate_normalize_number(property_id)
        else:
            property_key = "title:" + _duplicate_normalize_text(record.get("property_title"))
        message = _duplicate_normalize_text(record.get("message"))
        has_property_reference = (
            (property_id is not None and not _is_missing_scalar(property_id))
            or bool(_duplicate_normalize_text(record.get("property_title")))
        )
        if len(digits) < 7 or not name or not has_property_reference:
            continue
        signature = (digits, name, property_key, message)
        groups.setdefault(signature, []).append(record)

    duplicate_groups = []
    for records in groups.values():
        if len(records) < 2:
            continue
        records.sort(key=lambda rec: int(rec.get("id", 0) or 0))
        duplicate_groups.append({
            "keep": records[0],
            "candidates": records[1:],
            "records": records,
        })
    duplicate_groups.sort(key=lambda group: int(group["keep"].get("id", 0) or 0))
    return duplicate_groups


def _is_missing_scalar(value):
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def delete_enquiry_records(enquiry_ids):
    """Delete selected enquiry IDs and their associated interaction history."""
    ids = sorted({int(value) for value in enquiry_ids})
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    with get_connection() as conn:
        existing = conn.execute(
            f"SELECT id FROM enquiries WHERE id IN ({placeholders})", ids
        ).fetchall()
        valid_ids = [int(row[0]) for row in existing]
        if not valid_ids:
            return 0
        valid_placeholders = ",".join("?" for _ in valid_ids)
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "lead_interactions" in tables:
            conn.execute(
                f"DELETE FROM lead_interactions WHERE enquiry_id IN ({valid_placeholders})",
                valid_ids,
            )
        cursor = conn.execute(
            f"DELETE FROM enquiries WHERE id IN ({valid_placeholders})", valid_ids
        )
        return int(cursor.rowcount)


def qualification_score_for(data):
    """Transparent completeness score based only on provided enquiry details (0-100)."""
    score = 20  # a valid name and phone are required by the form
    if has_value(data.get("property_id")):
        score += 15
    if data.get("preferred_budget") is not None and float(data.get("preferred_budget") or 0) > 0:
        score += 25
    if str(data.get("move_in_timeline", "")).strip():
        score += 25
    if len(str(data.get("message", "")).strip()) >= 20:
        score += 15
    return min(100, score)


def get_property_rating_summary():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT property_id, AVG(rating), COUNT(*) FROM property_ratings WHERE property_id IS NOT NULL GROUP BY property_id"
        ).fetchall()
    return {int(property_id): (float(avg_rating), int(count)) for property_id, avg_rating, count in rows}


def submit_property_rating(property_id, property_title, reviewer_name, rating, review):
    with get_connection() as conn:
        if property_id is not None:
            exists = conn.execute("SELECT 1 FROM properties WHERE id = ?", (int(property_id),)).fetchone()
            if not exists:
                raise ValueError("This property no longer exists. Refresh the page and try again.")
        conn.execute(
            "INSERT INTO property_ratings (property_id, property_title, reviewer_name, rating, review) VALUES (?, ?, ?, ?, ?)",
            (property_id, property_title, reviewer_name.strip(), int(rating), review.strip()),
        )


def get_property_ratings(property_id=None):
    with get_connection() as conn:
        if property_id is None:
            return pd.read_sql_query(
                "SELECT id, property_id, property_title, reviewer_name, rating, review, created_at FROM property_ratings ORDER BY datetime(created_at) DESC, id DESC",
                conn,
            )
        return pd.read_sql_query(
            "SELECT id, property_id, property_title, reviewer_name, rating, review, created_at FROM property_ratings WHERE property_id = ? ORDER BY datetime(created_at) DESC, id DESC",
            conn, params=(int(property_id),)
        )


def create_database_backup():
    """Create a consistent SQLite backup in data/backups and return its path."""
    backup_dir = BASE_DIR / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"property_agent_backup_{timestamp}.db"
    with _sqlite_connection(DB_PATH) as source, _sqlite_connection(backup_path) as destination:
        source.backup(destination)
    with _sqlite_connection(backup_path) as check_conn:
        integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity check failed: {integrity}")
    return backup_path


def validate_database_backup(file_path):
    """Validate that an uploaded backup is a healthy SQLite database with a properties table."""
    try:
        with _sqlite_connection(file_path) as check_conn:
            integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in check_conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if integrity != "ok" or "properties" not in tables:
                return False, "The selected file is not a healthy Property Agent database."
            columns = {row[1] for row in check_conn.execute("PRAGMA table_info(properties)").fetchall()}
            required = {"id", "title", "city", "property_type", "listing_type", "price", "bedrooms"}
            if not required.issubset(columns):
                return False, "The backup's properties table does not contain the required columns."
        return True, "Valid SQLite property database."
    except sqlite3.Error as exc:
        return False, f"Could not read this database backup: {exc}"


def generate_followup_draft(lead_record):
    """Use local Ollama for a draft, with a safe local template as a fallback."""
    name = str(lead_record.get("customer_name") or "there").strip() or "there"
    property_title = str(lead_record.get("property_title") or "the property you enquired about").strip()
    message = str(lead_record.get("message") or "").strip()
    prompt = (
        "Write one polite, concise follow-up message from a property agent to a customer. "
        "Do not invent property facts, prices, availability, or promises. Keep it under 90 words. "
        f"Customer first name/name: {name}. Property enquiry: {property_title}. "
        f"Customer's original message: {message or 'No message supplied'}. Return only the message text."
    )
    payload = json.dumps({
        "model": os.environ.get("OLLAMA_MODEL", "llama3.2:3b"),
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.4},
    }).encode("utf-8")
    request = urllib.request.Request(
        os.environ.get("OLLAMA_GENERATE_URL", "http://localhost:11434/api/generate"),
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        draft = str(result.get("response", "")).strip()
        if draft:
            return draft, True
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        pass
    fallback = (
        f"Hello {name}, thank you for your interest in {property_title}. "
        "I wanted to follow up and see whether you have any questions or would like to discuss the next steps. "
        "Please let me know a convenient time to connect."
    )
    return fallback, False


def add_property(data):
    columns = get_property_columns()
    insert_data = {key: value for key, value in data.items() if key in columns}
    if not insert_data:
        raise ValueError("The properties table does not contain compatible columns.")
    column_sql = ", ".join(f'"{column}"' for column in insert_data)
    placeholders = ", ".join("?" for _ in insert_data)
    with get_connection() as conn:
        cursor = conn.execute(
            f"INSERT INTO properties ({column_sql}) VALUES ({placeholders})",
            list(insert_data.values()),
        )
        return int(cursor.lastrowid)


def save_image_paths():
    """Assign bundled images to the first three sample listings when possible."""
    columns = get_property_columns()
    if "image_path" not in columns or "id" not in columns:
        return
    with get_connection() as conn:
        for property_id, image_path in PROPERTY_IMAGE_MAP.items():
            if image_path.is_file():
                conn.execute(
                    "UPDATE properties SET image_path = ? WHERE id = ?",
                    (str(image_path.relative_to(BASE_DIR)), property_id),
                )


def delete_property(property_id):
    """Delete one listing and, when safe, its own uploaded image file.

    Only files directly inside assets/properties/user_uploads are eligible for
    deletion. Bundled/sample images and files outside that folder are preserved.
    """
    columns = get_property_columns()
    image_value = None
    uploaded_image_to_remove = None

    with get_connection() as conn:
        if "image_path" in columns:
            row = conn.execute(
                "SELECT image_path FROM properties WHERE id = ?", (property_id,)
            ).fetchone()
            if row is None:
                raise ValueError("The selected property no longer exists.")
            image_value = row[0]
        else:
            row = conn.execute(
                "SELECT id FROM properties WHERE id = ?", (property_id,)
            ).fetchone()
            if row is None:
                raise ValueError("The selected property no longer exists.")

        # Keep lead history if the property is deleted. The enquiry retains its saved
        # property_title snapshot while the live property link is cleared.
        try:
            conn.execute("UPDATE enquiries SET property_id = NULL WHERE property_id = ?", (property_id,))
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("UPDATE property_ratings SET property_id = NULL WHERE property_id = ?", (property_id,))
        except sqlite3.OperationalError:
            pass
        conn.execute("DELETE FROM properties WHERE id = ?", (property_id,))

        if image_value and "image_path" in columns:
            # Do not remove an image if another listing still references the same path.
            remaining = conn.execute(
                "SELECT COUNT(*) FROM properties WHERE image_path = ?", (image_value,)
            ).fetchone()[0]
            if remaining == 0:
                stored_path = Path(str(image_value).strip().strip('"'))
                if not stored_path.is_absolute():
                    stored_path = BASE_DIR / stored_path
                try:
                    resolved_path = stored_path.resolve()
                    upload_root = USER_UPLOADS_DIR.resolve()
                    # Only remove files created in our dedicated upload directory.
                    if resolved_path.is_file() and resolved_path.parent == upload_root:
                        uploaded_image_to_remove = resolved_path
                except (OSError, RuntimeError):
                    uploaded_image_to_remove = None

    if uploaded_image_to_remove is not None:
        try:
            uploaded_image_to_remove.unlink(missing_ok=True)
        except OSError:
            # The database record is already deleted; an unused image can be
            # removed manually later if the operating system blocks deletion.
            pass


# =========================================================
# SAFE VALUE AND DISPLAY HELPERS
# =========================================================
def has_value(value):
    """Safely check scalar values that may be None, NaN, or pandas objects."""
    if value is None:
        return False
    if isinstance(value, (pd.Series, pd.DataFrame)):
        return not value.empty
    try:
        return bool(pd.notna(value)) and bool(str(value).strip())
    except (TypeError, ValueError):
        return False


def display_value(record, key, default="Not provided"):
    value = record.get(key, default)
    return value if has_value(value) else default


def resolve_image(image_value):
    """Resolve property images from absolute, relative, or old Windows paths."""
    if not has_value(image_value):
        return None
    raw_path = str(image_value).strip().strip('"')
    normalized = raw_path.replace("\\", "/")
    candidates = [Path(raw_path), BASE_DIR / normalized]
    # A database copied from another computer can contain a stale absolute path.
    # If its filename is still under assets/properties, recover it by basename.
    basename = Path(normalized).name
    if basename:
        candidates.extend([
            USER_UPLOADS_DIR / basename,
            PROPERTY_IMAGES_DIR / basename,
        ])
    for candidate in candidates:
        try:
            if candidate.is_file():
                return str(candidate.resolve())
        except (OSError, RuntimeError):
            continue
    return None



def format_filters(filters):
    if not filters:
        return "No specific requirements detected yet."
    labels = {
        "city": "City", "listing_type": "Listing", "property_type": "Property type",
        "bedrooms": "Bedrooms", "max_price": "Maximum budget",
    }
    parts = []
    for key, value in filters.items():
        if key == "max_price":
            try:
                value = f"₹{float(value):,.0f}"
            except (TypeError, ValueError):
                pass
        parts.append(f"{labels.get(key, key.replace('_', ' ').title())}: {value}")
    return " · ".join(parts)


def normalise_ai_filters(raw_filters):
    """Normalise common Ollama JSON key/value variations into app filter names."""
    if not isinstance(raw_filters, dict):
        return {}
    aliases = {
        "city": ("city", "location", "locality", "preferred_city", "area"),
        "listing_type": ("listing_type", "listing", "purpose", "transaction_type", "deal_type"),
        "property_type": ("property_type", "type", "home_type", "property"),
        "bedrooms": ("bedrooms", "bhk", "beds", "bedroom_count"),
        "max_price": ("max_price", "max_budget", "budget", "maximum_price", "price_limit", "budget_max"),
    }
    lowered = {str(key).strip().lower(): value for key, value in raw_filters.items()}
    result = {}
    for target, keys in aliases.items():
        value = next((lowered[key] for key in keys if key in lowered), None)
        if value is None or str(value).strip().lower() in {"", "none", "null", "any", "not specified", "unknown"}:
            continue
        if target == "listing_type":
            value_text = str(value).strip().lower()
            if any(word in value_text for word in ("rent", "rental", "lease")):
                result[target] = "Rent"
            elif any(word in value_text for word in ("sale", "buy", "purchase", "sell")):
                result[target] = "Sale"
        elif target == "property_type":
            value_text = str(value).strip().lower()
            type_map = {
                "apartment": "Apartment", "flat": "Apartment", "villa": "Villa",
                "house": "House", "bungalow": "House", "plot": "Plot", "land": "Plot",
                "commercial": "Commercial", "office": "Commercial", "shop": "Commercial",
            }
            matched = next((canonical for word, canonical in type_map.items() if word in value_text), None)
            if matched:
                result[target] = matched
        elif target == "bedrooms":
            try:
                # Handles numeric values and strings such as "3 BHK".
                import re
                match = re.search(r"\d+", str(value))
                if match:
                    result[target] = int(match.group())
            except (TypeError, ValueError):
                pass
        elif target == "max_price":
            try:
                import re
                text = str(value).lower().replace(",", "").replace("₹", "").replace("rs.", "").strip()
                match = re.search(r"\d+(?:\.\d+)?", text)
                if match:
                    amount = float(match.group())
                    if "crore" in text or re.search(r"\bcr\b", text):
                        amount *= 10_000_000
                    elif "lakh" in text or re.search(r"\bl\b", text):
                        amount *= 100_000
                    elif "thousand" in text or re.search(r"\bk\b", text):
                        amount *= 1_000
                    result[target] = amount
            except (TypeError, ValueError):
                pass
        elif target == "city":
            result[target] = str(value).strip()
    return result


def find_city_in_text(text, property_frame):
    """Find a city mentioned by the user, matching database city names case-insensitively."""
    if not text or "city" not in property_frame.columns:
        return None
    text_lower = str(text).lower()
    cities = property_frame["city"].dropna().astype(str).unique().tolist()
    # Prefer the longest match in case one city name contains another.
    matches = [city for city in cities if city.strip() and city.lower() in text_lower]
    return max(matches, key=len) if matches else None


def merge_filter_context(previous_filters, current_filters):
    """Carry forward constraints from prior turns; current explicit constraints win."""
    merged = dict(previous_filters or {})
    for key, value in (current_filters or {}).items():
        if value is not None:
            merged[key] = value
    return merged


def filter_with_constraints(frame, filters):
    """Apply all available structured constraints; return a copy of matching rows."""
    result = frame.copy()
    if result.empty:
        return result
    city = filters.get("city")
    if city and "city" in result.columns:
        result = result[result["city"].astype(str).str.strip().str.lower() == str(city).strip().lower()]
    listing_type = filters.get("listing_type")
    if listing_type and "listing_type" in result.columns:
        result = result[result["listing_type"].astype(str).str.strip().str.lower() == str(listing_type).lower()]
    property_type = filters.get("property_type")
    if property_type and "property_type" in result.columns:
        result = result[result["property_type"].astype(str).str.strip().str.lower() == str(property_type).lower()]
    bedrooms = filters.get("bedrooms")
    if bedrooms is not None and "bedrooms" in result.columns:
        numeric_beds = pd.to_numeric(result["bedrooms"], errors="coerce")
        result = result[numeric_beds == int(bedrooms)]
    max_price = filters.get("max_price")
    if max_price is not None and "price" in result.columns:
        numeric_prices = pd.to_numeric(result["price"], errors="coerce")
        result = result[numeric_prices <= float(max_price)]
    return result.copy()


def score_property(row, filters):
    """Return a relevance score and human-readable reasons for a listing."""
    score = 0.0
    reasons = []
    city = filters.get("city")
    actual_city = str(row.get("city", "")).strip()
    if city:
        if actual_city.lower() == str(city).lower():
            score += 12
            reasons.append(f"in {actual_city}, your requested city")
        else:
            score -= 5
            reasons.append(f"an alternative city ({actual_city or 'location not provided'})")
    listing_type = filters.get("listing_type")
    if listing_type:
        if str(row.get("listing_type", "")).strip().lower() == str(listing_type).lower():
            score += 8
            reasons.append(f"available for {str(listing_type).lower()}")
        else:
            score -= 4
    property_type = filters.get("property_type")
    if property_type:
        if str(row.get("property_type", "")).strip().lower() == str(property_type).lower():
            score += 5
            reasons.append(f"matches your {property_type} preference")
        else:
            score -= 3
    bedrooms = filters.get("bedrooms")
    try:
        actual_beds = int(float(row.get("bedrooms")))
    except (TypeError, ValueError):
        actual_beds = None
    if bedrooms is not None and actual_beds is not None:
        if actual_beds == int(bedrooms):
            score += 4
            reasons.append(f"has your requested {bedrooms} bedroom(s)")
        else:
            score -= min(4, abs(actual_beds - int(bedrooms)) * 1.5)
    max_price = filters.get("max_price")
    try:
        actual_price = float(row.get("price"))
    except (TypeError, ValueError):
        actual_price = None
    if max_price is not None and actual_price is not None:
        if actual_price <= float(max_price):
            score += 7
            reasons.append("within your budget")
        else:
            over_ratio = (actual_price - float(max_price)) / max(float(max_price), 1)
            score -= min(8, 2 + over_ratio * 8)
            reasons.append("above your budget")
    if not reasons:
        reasons.append("one of the available listings in your database")
    return score, ", ".join(reasons)


def get_ranked_recommendations(frame, filters, limit=5):
    """Return exact matches when available, otherwise rank the closest alternatives."""
    if not filters:
        return frame.head(limit).to_dict("records"), False

    exact = filter_with_constraints(frame, filters)
    if not exact.empty:
        records = exact.to_dict("records")
        records.sort(key=lambda row: score_property(row, filters)[0], reverse=True)
        return records[:limit], True

    if frame.empty:
        return [], False

    # If the requested city has listings, prefer staying in that city even when
    # another requirement must be relaxed. If it doesn't, consider alternatives.
    candidates = frame.copy()
    city = filters.get("city")
    city_match_count = 0
    if city and "city" in candidates.columns:
        city_rows = candidates[candidates["city"].astype(str).str.strip().str.lower() == str(city).strip().lower()]
        city_match_count = len(city_rows)
        if not city_rows.empty:
            candidates = city_rows

    listing = filters.get("listing_type")
    if listing and not candidates.empty:
        listing_rows = candidates[candidates["listing_type"].astype(str).str.lower() == str(listing).lower()]
        if not listing_rows.empty:
            candidates = listing_rows

    ranked = []
    for record in candidates.to_dict("records"):
        score, reason = score_property(record, filters)
        ranked.append((score, record, reason))
    ranked.sort(key=lambda item: item[0], reverse=True)
    # Preserve the reason so the UI can explain compromises in recommendations.
    output = []
    for score, record, reason in ranked[:limit]:
        record["_recommendation_reason"] = reason
        record["_recommendation_score"] = round(score, 1)
        output.append(record)
    return output, False




# =========================================================
# PHASE 8: SYSTEM HEALTH & DIAGNOSTICS
# =========================================================
def run_system_diagnostics(ollama_timeout=2.0):
    """Run read-only health checks for the local application.

    This function does not create, migrate, modify, or delete application data.
    It returns a list of dictionaries suitable for display in the Streamlit UI.
    """
    import importlib.metadata
    import sys

    checks = []

    def record(name, status, details, action=""):
        checks.append({
            "Check": str(name),
            "Status": str(status),
            "Details": str(details),
            "Suggested action": str(action),
        })

    # Runtime and key dependencies
    record("Python runtime", "PASS", sys.version.split()[0])
    for package in ("streamlit", "pandas", "Pillow"):
        try:
            version = importlib.metadata.version(package)
            record(f"Dependency: {package}", "PASS", f"Installed version {version}")
        except importlib.metadata.PackageNotFoundError:
            record(f"Dependency: {package}", "FAIL", "Package is not installed",
                   f"Install project dependencies with: python -m pip install -r requirements.txt")

    # Important local paths
    record("Project directory", "PASS" if BASE_DIR.is_dir() else "FAIL", str(BASE_DIR),
           "Restore the project folder if it is missing.")
    record("Database file", "PASS" if DB_PATH.is_file() else "FAIL", str(DB_PATH),
           "Restore data/property_agent.db from a trusted backup. Do not run init_db.py on your only copy.")
    record("Property image folder", "PASS" if PROPERTY_IMAGES_DIR.is_dir() else "WARN",
           str(PROPERTY_IMAGES_DIR),
           "Create assets/properties or restore your property image files if photos are missing.")

    image_count = 0
    if PROPERTY_IMAGES_DIR.is_dir():
        image_count = sum(1 for p in PROPERTY_IMAGES_DIR.rglob("*")
                          if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"})
    record("Property image files", "PASS" if image_count else "WARN",
           f"Found {image_count} supported image file(s)",
           "Add property images under assets/properties if your listings need photos.")

    backup_dir = BASE_DIR / "data" / "backups"
    backup_files = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True) if backup_dir.is_dir() else []
    record("Local backups", "PASS" if backup_files else "WARN",
           f"Found {len(backup_files)} .db backup file(s) in data/backups",
           "Create a database backup in the Data Safety & Backup tab before risky changes.")

    # Database health checks are strictly read-only.
    if DB_PATH.is_file():
        try:
            db_uri = DB_PATH.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(db_uri, uri=True, timeout=3)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity == "ok":
                    record("SQLite integrity", "PASS", "PRAGMA integrity_check returned 'ok'")
                else:
                    record("SQLite integrity", "FAIL", str(integrity),
                           "Stop the app and restore from a verified database backup.")
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()}
                if "properties" not in tables:
                    record("Properties table", "FAIL", "Required table 'properties' was not found",
                           "Use the matching database backup; do not overwrite data without a backup.")
                else:
                    columns = {row[1] for row in conn.execute("PRAGMA table_info(properties)").fetchall()}
                    required = {"id", "title", "city", "property_type", "listing_type", "price", "bedrooms"}
                    missing = sorted(required - columns)
                    if missing:
                        record("Property schema", "FAIL", "Missing columns: " + ", ".join(missing),
                               "Back up the database and review the expected schema before upgrading it.")
                    else:
                        count = int(conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0])
                        record("Properties table", "PASS", f"Schema is valid; {count} property record(s)")
                phase9_tables = {"saved_property_searches", "customer_preferences"}
                present_phase9_tables = tables & phase9_tables
                if present_phase9_tables == phase9_tables:
                    record("Phase 9 personalisation tables", "PASS", "Saved searches and customer preference tables are present")
                else:
                    missing_phase9 = sorted(phase9_tables - present_phase9_tables)
                    record("Phase 9 personalisation tables", "WARN", "Missing: " + ", ".join(missing_phase9),
                           "Restart the application so its safe Phase 9 table setup can run.")
            finally:
                conn.close()
        except Exception as exc:
            record("Database read test", "FAIL", f"{type(exc).__name__}: {exc}",
                   "Check file permissions and restore only from a verified backup if needed.")

    # Local Ollama check: short timeout and no model generation.
    tags_url = re.sub(r"/api/generate/?$", "/api/tags", OLLAMA_URL)
    if tags_url == OLLAMA_URL:
        tags_url = OLLAMA_URL.rsplit("/", 1)[0] + "/tags"
    try:
        request = urllib.request.Request(tags_url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=max(0.5, float(ollama_timeout))) as response:
            payload = json.loads(response.read().decode("utf-8"))
        model_names = [str(item.get("name", "")) for item in payload.get("models", [])
                       if isinstance(item, dict)]
        model_present = any(name == MODEL_NAME or name.split(":", 1)[0] == MODEL_NAME.split(":", 1)[0]
                            for name in model_names)
        if model_present:
            record("Ollama service", "PASS", f"Reachable; configured model '{MODEL_NAME}' is listed")
        else:
            record("Ollama service", "WARN",
                   f"Service is reachable, but configured model '{MODEL_NAME}' was not found. Models listed: {len(model_names)}",
                   f"Run 'ollama pull {MODEL_NAME}' if this is the model you want to use.")
    except Exception as exc:
        record("Ollama service", "WARN", f"Not reachable at {tags_url} ({type(exc).__name__})",
               "Start Ollama before using AI features. Basic property search should remain available.")

    return checks


# =========================================================
# PHASE 10 — SMART MATCHING, NOTIFICATIONS, TASKS & PIPELINE
# =========================================================
PHASE10_PIPELINE_STAGES = [
    "Enquiry", "Contacted", "Qualified", "Matched", "Visit Scheduled",
    "Negotiation", "Converted", "Lost", "Closed",
]


def ensure_phase10_tables():
    """Create Phase 10 tables and add a lead-pipeline stage safely."""
    with get_connection() as conn:
        enquiry_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(enquiries)").fetchall()
        }
        if "pipeline_stage" not in enquiry_columns:
            conn.execute(
                "ALTER TABLE enquiries ADD COLUMN pipeline_stage TEXT NOT NULL DEFAULT 'Enquiry'"
            )
        if "stage_updated_at" not in enquiry_columns:
            conn.execute("ALTER TABLE enquiries ADD COLUMN stage_updated_at TEXT")

        # Preserve the existing CRM status and initialise a separate pipeline stage.
        conn.execute(
            """
            UPDATE enquiries
            SET pipeline_stage = CASE lower(COALESCE(status, 'new'))
                WHEN 'new' THEN 'Enquiry'
                WHEN 'contacted' THEN 'Contacted'
                WHEN 'follow-up' THEN 'Qualified'
                WHEN 'won' THEN 'Converted'
                WHEN 'lost' THEN 'Lost'
                WHEN 'closed' THEN 'Closed'
                ELSE COALESCE(NULLIF(pipeline_stage, ''), 'Enquiry')
            END,
            stage_updated_at = COALESCE(stage_updated_at, updated_at, datetime('now', 'localtime'))
            WHERE pipeline_stage IS NULL OR pipeline_stage = '' OR stage_updated_at IS NULL
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_enquiries_pipeline_stage ON enquiries(pipeline_stage)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS property_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL,
                property_id INTEGER NOT NULL,
                match_score INTEGER NOT NULL CHECK(match_score BETWEEN 0 AND 100),
                match_level TEXT NOT NULL,
                match_reasons TEXT NOT NULL DEFAULT '',
                match_status TEXT NOT NULL DEFAULT 'New',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                UNIQUE(enquiry_id, property_id),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE,
                FOREIGN KEY(property_id) REFERENCES properties(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_property_matches_enquiry ON property_matches(enquiry_id, match_score DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_property_matches_property ON property_matches(property_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER,
                property_id INTEGER,
                category TEXT NOT NULL DEFAULT 'General',
                priority TEXT NOT NULL DEFAULT 'Medium',
                title TEXT NOT NULL,
                message TEXT NOT NULL DEFAULT '',
                dedupe_key TEXT UNIQUE,
                is_read INTEGER NOT NULL DEFAULT 0 CHECK(is_read IN (0, 1)),
                is_dismissed INTEGER NOT NULL DEFAULT 0 CHECK(is_dismissed IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_notifications_unread ON notifications(is_read, is_dismissed, created_at)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS follow_up_tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                due_date TEXT NOT NULL,
                priority TEXT NOT NULL DEFAULT 'Medium',
                status TEXT NOT NULL DEFAULT 'Pending',
                completed_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_follow_up_tasks_due ON follow_up_tasks(status, due_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_follow_up_tasks_enquiry ON follow_up_tasks(enquiry_id)")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lead_activities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                enquiry_id INTEGER NOT NULL,
                activity_type TEXT NOT NULL,
                details TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY(enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_lead_activities_enquiry ON lead_activities(enquiry_id, created_at)")


def _normalise_match_text(value):
    return " ".join(str(value or "").strip().casefold().split())


def score_property_for_customer(property_record, preferences):
    """Return deterministic 0–100 weighted matching score and explanation."""
    preferences = preferences or {}
    weights = {
        "city": 25, "property_type": 15, "listing_type": 15,
        "bedrooms": 15, "budget": 15, "area": 10, "amenities": 5,
    }
    possible = 0.0
    earned = 0.0
    reasons = []
    exact_checks = []

    def award(key, supplied, ratio, good_text, miss_text):
        nonlocal possible, earned
        if not supplied:
            return
        weight = weights[key]
        possible += weight
        earned += weight * max(0.0, min(1.0, float(ratio)))
        if ratio >= 0.999:
            reasons.append(good_text)
            exact_checks.append(True)
        else:
            reasons.append(miss_text)
            exact_checks.append(False)

    pref_city = str(preferences.get("preferred_city") or "").strip()
    if pref_city:
        match = _normalise_match_text(property_record.get("city")) == _normalise_match_text(pref_city)
        award("city", True, 1 if match else 0,
              f"City matches ({pref_city})", f"City differs from preferred {pref_city}")

    pref_type = str(preferences.get("preferred_property_type") or "").strip()
    if pref_type:
        match = _normalise_match_text(property_record.get("property_type")) == _normalise_match_text(pref_type)
        award("property_type", True, 1 if match else 0,
              f"Property type matches ({pref_type})", f"Property type differs from {pref_type}")

    pref_listing = str(preferences.get("preferred_listing_type") or "").strip()
    if pref_listing:
        match = _normalise_match_text(property_record.get("listing_type")) == _normalise_match_text(pref_listing)
        award("listing_type", True, 1 if match else 0,
              f"Listing type matches ({pref_listing})", f"Listing type differs from {pref_listing}")

    bedrooms = preferences.get("bedrooms")
    if bedrooms not in (None, "", 0, 0.0):
        try:
            match = int(property_record.get("bedrooms") or 0) == int(bedrooms)
        except (TypeError, ValueError):
            match = False
        award("bedrooms", True, 1 if match else 0,
              f"Bedrooms match ({int(bedrooms)} BHK)", f"Bedroom count differs from {bedrooms}")

    budget = preferences.get("max_budget")
    if budget not in (None, "", 0, 0.0):
        try:
            price = float(property_record.get("price") or 0)
            budget_value = float(budget)
            ratio = 1.0 if price <= budget_value else (0.35 if price <= budget_value * 1.15 else 0.0)
        except (TypeError, ValueError):
            price, budget_value, ratio = 0.0, 0.0, 0.0
        award("budget", True, ratio,
              f"Within budget (₹{budget_value:,.0f})",
              f"Over budget (₹{price:,.0f} vs ₹{budget_value:,.0f})")

    min_area = preferences.get("min_area_sqft")
    max_area = preferences.get("max_area_sqft")
    if min_area not in (None, "", 0, 0.0) or max_area not in (None, "", 0, 0.0):
        try:
            area = float(property_record.get("area_sqft") or 0)
            low_ok = min_area in (None, "", 0, 0.0) or area >= float(min_area)
            high_ok = max_area in (None, "", 0, 0.0) or area <= float(max_area)
            exact_area = low_ok and high_ok
            if exact_area:
                ratio = 1.0
            else:
                nearest = float(min_area) if min_area not in (None, "", 0, 0.0) and area < float(min_area) else float(max_area)
                boundary = max(nearest, 1.0)
                ratio = 0.35 if abs(area - nearest) / boundary <= 0.15 else 0.0
        except (TypeError, ValueError):
            exact_area, ratio = False, 0.0
        award("area", True, ratio,
              "Area is within preferred range", "Area is outside preferred range")

    amenity_terms = [part.strip().casefold() for part in re.split(
        r"[,;\n]+", str(preferences.get("must_have_amenities") or "")
    ) if part.strip()]
    if amenity_terms:
        searchable = " ".join(str(property_record.get(key) or "") for key in ("amenities", "description", "title")).casefold()
        found = sum(1 for term in amenity_terms if term in searchable)
        ratio = found / len(amenity_terms)
        award("amenities", True, ratio,
              "All must-have amenities are present",
              f"{found}/{len(amenity_terms)} must-have amenities found")

    score = int(round((earned / possible) * 100)) if possible else 0
    if score >= 85:
        level = "Excellent"
    elif score >= 65:
        level = "Good"
    elif score >= 40:
        level = "Alternative"
    else:
        level = "Low"
    exact = bool(exact_checks) and all(exact_checks)
    return {"score": score, "level": level, "reasons": reasons, "is_exact": exact}


def get_customer_profiles():
    """Return existing enquiry records that have a Phase 9 preference profile."""
    with get_connection() as conn:
        return pd.read_sql_query(
            """SELECT e.id AS enquiry_id, e.customer_name, e.phone, e.email,
                      e.status, e.pipeline_stage,
                      cp.preferred_city, cp.preferred_property_type,
                      cp.preferred_listing_type, cp.bedrooms, cp.max_budget,
                      cp.min_area_sqft, cp.max_area_sqft,
                      cp.must_have_amenities, cp.notes
               FROM customer_preferences cp
               JOIN enquiries e ON e.id = cp.enquiry_id
               ORDER BY e.customer_name COLLATE NOCASE, e.id""",
            conn,
        )


def _save_property_match(enquiry_id, property_record, match_result, notify=True):
    property_id = int(property_record["id"])
    score = int(match_result["score"])
    if score < 40:
        return False
    reasons = "; ".join(match_result["reasons"])
    with get_connection() as conn:
        existing = conn.execute(
            "SELECT id FROM property_matches WHERE enquiry_id = ? AND property_id = ?",
            (int(enquiry_id), property_id),
        ).fetchone()
        conn.execute(
            """INSERT INTO property_matches
               (enquiry_id, property_id, match_score, match_level, match_reasons)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(enquiry_id, property_id) DO UPDATE SET
                 match_score=excluded.match_score, match_level=excluded.match_level,
                 match_reasons=excluded.match_reasons,
                 updated_at=datetime('now', 'localtime')""",
            (int(enquiry_id), property_id, score, match_result["level"], reasons),
        )
        if notify and score >= 65 and existing is None:
            lead = conn.execute(
                "SELECT customer_name FROM enquiries WHERE id = ?", (int(enquiry_id),)
            ).fetchone()
            property_title = str(property_record.get("title") or f"Property #{property_id}")
            customer_name = lead[0] if lead else f"Customer #{enquiry_id}"
            conn.execute(
                """INSERT OR IGNORE INTO notifications
                   (enquiry_id, property_id, category, priority, title, message, dedupe_key)
                   VALUES (?, ?, 'Property Match', 'High', ?, ?, ?)""",
                (int(enquiry_id), property_id,
                 f"New property match for {customer_name}",
                 f"{property_title} scored {score}% ({match_result['level']}). {reasons}",
                 f"property-match:{int(enquiry_id)}:{property_id}"),
            )
    return True


def refresh_matches_for_customer(enquiry_id, properties_frame=None, notify=True, min_score=40):
    """Recalculate and persist this customer's best current property matches."""
    if properties_frame is None:
        properties_frame = get_properties()
    preferences = get_customer_preferences(int(enquiry_id))
    if not preferences:
        return []
    if properties_frame is None or properties_frame.empty:
        return []
    ranked = []
    for record in properties_frame.to_dict("records"):
        result = score_property_for_customer(record, preferences)
        if result["score"] >= int(min_score):
            ranked.append((result["score"], record, result))
    ranked.sort(key=lambda item: (-item[0], int(item[1].get("id") or 0)))
    ranked = ranked[:50]
    eligible_ids = [int(record["id"]) for _, record, _ in ranked]
    # Remove stale match rows when preferences change or a listing no longer qualifies.
    with get_connection() as conn:
        if eligible_ids:
            placeholders = ",".join("?" for _ in eligible_ids)
            conn.execute(
                f"DELETE FROM property_matches WHERE enquiry_id = ? AND property_id NOT IN ({placeholders})",
                [int(enquiry_id), *eligible_ids],
            )
            conn.execute(
                f"UPDATE notifications SET is_dismissed = 1 WHERE category = 'Property Match' AND enquiry_id = ? AND property_id NOT IN ({placeholders})",
                [int(enquiry_id), *eligible_ids],
            )
        else:
            conn.execute("DELETE FROM property_matches WHERE enquiry_id = ?", (int(enquiry_id),))
            conn.execute(
                "UPDATE notifications SET is_dismissed = 1 WHERE category = 'Property Match' AND enquiry_id = ?",
                (int(enquiry_id),),
            )
    saved = []
    for _, record, result in ranked:
        if _save_property_match(enquiry_id, record, result, notify=notify):
            saved.append({**record, **result})
    return saved


def match_property_to_customers(property_id, properties_frame=None, notify=True, min_score=40):
    """When a listing is added, score it against every saved customer preference."""
    if properties_frame is None:
        properties_frame = get_properties()
    if properties_frame is None or properties_frame.empty:
        return []
    matches = properties_frame[pd.to_numeric(properties_frame["id"], errors="coerce") == int(property_id)]
    if matches.empty:
        return []
    property_record = matches.iloc[0].to_dict()
    profiles = get_customer_profiles()
    created = []
    for profile in profiles.to_dict("records"):
        result = score_property_for_customer(property_record, profile)
        if result["score"] >= int(min_score):
            _save_property_match(int(profile["enquiry_id"]), property_record, result, notify=notify)
            created.append({
                "enquiry_id": int(profile["enquiry_id"]),
                "customer_name": profile.get("customer_name", "Customer"),
                "property_id": int(property_id),
                "property_title": property_record.get("title", "Property"),
                **result,
            })
    return created


def match_all_customers(properties_frame=None, notify=True, min_score=40):
    """Scan every saved customer profile against all active listings."""
    if properties_frame is None:
        properties_frame = get_properties()
    profiles = get_customer_profiles()
    results = []
    for profile in profiles.to_dict("records"):
        customer_matches = refresh_matches_for_customer(
            int(profile["enquiry_id"]), properties_frame=properties_frame,
            notify=notify, min_score=min_score,
        )
        for match in customer_matches:
            results.append({
                "enquiry_id": int(profile["enquiry_id"]),
                "customer_name": profile.get("customer_name", "Customer"),
                "property_id": int(match["id"]),
                "property_title": match.get("title", "Property"),
                "score": int(match["score"]),
                "level": match["level"],
                "reasons": "; ".join(match["reasons"]),
            })
    results.sort(key=lambda row: (-row["score"], row["customer_name"].casefold()))
    return pd.DataFrame(results, columns=[
        "enquiry_id", "customer_name", "property_id", "property_title", "score", "level", "reasons"
    ])


def get_property_matches(enquiry_id=None, limit=100):
    """Read persisted customer-property matches with current listing and lead details."""
    sql = """SELECT pm.id, pm.enquiry_id, e.customer_name, e.phone,
                    pm.property_id, p.title AS property_title, p.city, p.property_type,
                    p.listing_type, p.price, p.bedrooms, p.area_sqft, p.image_path,
                    pm.match_score, pm.match_level, pm.match_reasons,
                    pm.match_status, pm.created_at, pm.updated_at
             FROM property_matches pm
             JOIN enquiries e ON e.id = pm.enquiry_id
             JOIN properties p ON p.id = pm.property_id"""
    params = []
    if enquiry_id is not None:
        sql += " WHERE pm.enquiry_id = ?"
        params.append(int(enquiry_id))
    sql += " ORDER BY pm.match_score DESC, pm.updated_at DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def update_property_match_status(match_id, status):
    allowed = {"New", "Viewed", "Contacted", "Dismissed"}
    status = str(status or "").strip().title()
    if status not in allowed:
        raise ValueError("Choose New, Viewed, Contacted, or Dismissed for a match.")
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE property_matches SET match_status = ?, updated_at = datetime('now', 'localtime') WHERE id = ?",
            (status, int(match_id)),
        )
        return cursor.rowcount > 0


def create_notification(title, message="", category="General", priority="Medium",
                        enquiry_id=None, property_id=None, dedupe_key=None):
    """Create a persistent notification; a dedupe key prevents repeated alerts."""
    title = str(title or "").strip()[:180]
    message = str(message or "").strip()[:1000]
    if not title:
        raise ValueError("Notification title cannot be empty.")
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO notifications
               (enquiry_id, property_id, category, priority, title, message, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (int(enquiry_id) if enquiry_id is not None else None,
             int(property_id) if property_id is not None else None,
             str(category or "General")[:50], priority, title, message,
             str(dedupe_key)[:240] if dedupe_key else None),
        )
        return cursor.rowcount > 0


def get_notifications(unread_only=False, include_dismissed=False, limit=100):
    sql = "SELECT * FROM notifications WHERE 1 = 1"
    params = []
    if unread_only:
        sql += " AND is_read = 0"
    if not include_dismissed:
        sql += " AND is_dismissed = 0"
    sql += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_unread_notification_count():
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM notifications WHERE is_read = 0 AND is_dismissed = 0"
        ).fetchone()
        return int(row[0] or 0)


def mark_notification_read(notification_id):
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE notifications SET is_read = 1 WHERE id = ?", (int(notification_id),)
        )
        return cursor.rowcount > 0


def dismiss_notification(notification_id):
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE notifications SET is_dismissed = 1 WHERE id = ?", (int(notification_id),)
        )
        return cursor.rowcount > 0


def create_follow_up_task(enquiry_id, title, due_date, description="", priority="Medium"):
    """Create a task linked to an existing CRM enquiry."""
    title = str(title or "").strip()
    if not title:
        raise ValueError("Enter a task title.")
    if len(title) > 160:
        raise ValueError("Task title must be 160 characters or fewer.")
    try:
        parsed_due = date.fromisoformat(str(due_date)[:10])
    except (TypeError, ValueError):
        raise ValueError("Choose a valid task due date.")
    if priority not in {"Low", "Medium", "High"}:
        priority = "Medium"
    with get_connection() as conn:
        exists = conn.execute("SELECT 1 FROM enquiries WHERE id = ?", (int(enquiry_id),)).fetchone()
        if not exists:
            raise ValueError("The selected customer enquiry no longer exists.")
        cursor = conn.execute(
            "INSERT INTO follow_up_tasks (enquiry_id, title, description, due_date, priority) VALUES (?, ?, ?, ?, ?)",
            (int(enquiry_id), title, str(description or "").strip()[:2000], parsed_due.isoformat(), priority),
        )
        task_id = int(cursor.lastrowid)
        conn.execute(
            "INSERT INTO lead_activities (enquiry_id, activity_type, details) VALUES (?, 'Task created', ?)",
            (int(enquiry_id), f"Task #{task_id}: {title} (due {parsed_due.isoformat()})"),
        )
        return task_id


def get_follow_up_tasks(status="Open", enquiry_id=None, limit=300):
    sql = """SELECT t.*, e.customer_name, e.phone, e.property_title, e.pipeline_stage,
                    CASE WHEN t.status = 'Pending' AND t.due_date < date('now','localtime') THEN 'Overdue'
                         WHEN t.status = 'Pending' AND t.due_date = date('now','localtime') THEN 'Due today'
                         WHEN t.status = 'Pending' THEN 'Upcoming'
                         ELSE t.status END AS due_state
             FROM follow_up_tasks t JOIN enquiries e ON e.id = t.enquiry_id WHERE 1 = 1"""
    params = []
    if status == "Open":
        sql += " AND t.status = 'Pending'"
    elif status in {"Pending", "Completed", "Cancelled"}:
        sql += " AND t.status = ?"
        params.append(status)
    if enquiry_id is not None:
        sql += " AND t.enquiry_id = ?"
        params.append(int(enquiry_id))
    sql += " ORDER BY CASE WHEN t.status = 'Pending' THEN 0 ELSE 1 END, date(t.due_date), t.id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    with get_connection() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def update_follow_up_task(task_id, status=None, due_date=None, description=None):
    allowed = {"Pending", "Completed", "Cancelled"}
    if status is not None and status not in allowed:
        raise ValueError("Task status must be Pending, Completed, or Cancelled.")
    updates, values = [], []
    if status is not None:
        updates.append("status = ?")
        values.append(status)
        updates.append("completed_at = ?")
        values.append(datetime.now().isoformat(timespec="seconds") if status == "Completed" else None)
    if due_date is not None:
        try:
            values.append(date.fromisoformat(str(due_date)[:10]).isoformat())
        except (TypeError, ValueError):
            raise ValueError("Choose a valid task due date.")
        updates.append("due_date = ?")
    if description is not None:
        updates.append("description = ?")
        values.append(str(description)[:2000])
    if not updates:
        return False
    updates.append("updated_at = datetime('now', 'localtime')")
    values.append(int(task_id))
    with get_connection() as conn:
        row = conn.execute("SELECT enquiry_id, title FROM follow_up_tasks WHERE id = ?", (int(task_id),)).fetchone()
        if not row:
            return False
        cursor = conn.execute(f"UPDATE follow_up_tasks SET {', '.join(updates)} WHERE id = ?", values)
        if status == "Completed":
            conn.execute(
                "INSERT INTO lead_activities (enquiry_id, activity_type, details) VALUES (?, 'Task completed', ?)",
                (int(row[0]), f"Task #{int(task_id)} completed: {row[1]}"),
            )
        return cursor.rowcount > 0


def update_lead_pipeline_stage(enquiry_id, stage, notes=""):
    """Move a lead through the Phase 10 pipeline and record its history."""
    stage = str(stage or "").strip()
    if stage not in PHASE10_PIPELINE_STAGES:
        raise ValueError("Choose a valid lead pipeline stage.")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT pipeline_stage FROM enquiries WHERE id = ?", (int(enquiry_id),)
        ).fetchone()
        if not row:
            raise ValueError("The selected enquiry no longer exists.")
        previous = row[0] or "Enquiry"
        conn.execute(
            "UPDATE enquiries SET pipeline_stage = ?, stage_updated_at = datetime('now', 'localtime'), updated_at = datetime('now', 'localtime') WHERE id = ?",
            (stage, int(enquiry_id)),
        )
        detail = f"Pipeline stage changed: {previous} → {stage}"
        if str(notes or "").strip():
            detail += f". {str(notes).strip()[:1000]}"
        conn.execute(
            "INSERT INTO lead_activities (enquiry_id, activity_type, details) VALUES (?, 'Pipeline stage', ?)",
            (int(enquiry_id), detail),
        )
        if stage == "Converted":
            conn.execute("UPDATE enquiries SET status = 'Won' WHERE id = ?", (int(enquiry_id),))
        elif stage == "Lost":
            conn.execute("UPDATE enquiries SET status = 'Lost' WHERE id = ?", (int(enquiry_id),))


def get_lead_activities(enquiry_id, limit=100):
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT activity_type, details, created_at FROM lead_activities WHERE enquiry_id = ? ORDER BY datetime(created_at) DESC, id DESC LIMIT ?",
            conn, params=(int(enquiry_id), max(1, min(int(limit), 500))),
        )


def get_phase10_analytics():
    """Return lightweight counts for the Phase 10 overview dashboard."""
    with get_connection() as conn:
        lead_count = conn.execute("SELECT COUNT(*) FROM enquiries").fetchone()[0]
        match_count = conn.execute("SELECT COUNT(*) FROM property_matches").fetchone()[0]
        unread = conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read = 0 AND is_dismissed = 0").fetchone()[0]
        open_tasks = conn.execute("SELECT COUNT(*) FROM follow_up_tasks WHERE status = 'Pending'").fetchone()[0]
        converted = conn.execute("SELECT COUNT(*) FROM enquiries WHERE pipeline_stage = 'Converted'").fetchone()[0]
        stage_rows = conn.execute(
            "SELECT pipeline_stage, COUNT(*) FROM enquiries GROUP BY pipeline_stage ORDER BY COUNT(*) DESC"
        ).fetchall()
        match_rows = conn.execute(
            "SELECT match_level, COUNT(*) FROM property_matches GROUP BY match_level ORDER BY COUNT(*) DESC"
        ).fetchall()
    return {
        "leads": int(lead_count), "matches": int(match_count),
        "unread_notifications": int(unread), "open_tasks": int(open_tasks),
        "converted": int(converted), "stages": stage_rows, "match_levels": match_rows,
    }


def notify_from_due_followups(today=None):
    """Create deduplicated in-app alerts from Phase 9's due/overdue suggestions."""
    suggestions = get_smart_followup_suggestions(today=today)
    created = 0
    for row in suggestions.to_dict("records"):
        action = str(row.get("suggested_action") or "")
        if not re.search(r"overdue|today|now", action, flags=re.IGNORECASE):
            continue
        lead_id = int(row["id"])
        due_date = str(row.get("recommended_date") or date.today().isoformat())[:10]
        priority = "High" if "overdue" in action.casefold() else str(row.get("priority") or "Medium").title()
        if priority not in {"Low", "Medium", "High"}:
            priority = "Medium"
        was_created = create_notification(
            title=f"Follow-up needed: {row.get('customer_name', 'Customer')}",
            message=f"{action}. {row.get('reason', '')}",
            category="Follow-up", priority=priority, enquiry_id=lead_id,
            dedupe_key=f"follow-up:{lead_id}:{due_date}",
        )
        created += int(was_created)
    return created
