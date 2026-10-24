import html
import json
import os
import shutil
import sqlite3
import tempfile
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.local_ai import extract_property_filters, build_search_query
from services.property_search import search_properties, parse_property_query

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

st.set_page_config(page_title="Property Agent Bot", page_icon="🏠", layout="wide")

# Polished visual system for lead management and property ratings.
st.markdown(
    """
    <style>
    .block-container { padding-top: 1.5rem; padding-bottom: 2.5rem; }
    .crm-hero {
        padding: 1.45rem 1.6rem; border-radius: 20px; margin: 0.2rem 0 1.1rem 0;
        background: linear-gradient(120deg, #101e3d 0%, #203e72 58%, #326b91 100%);
        color: #ffffff; border: 1px solid rgba(255,255,255,.13);
        box-shadow: 0 12px 28px rgba(21, 43, 78, .16);
    }
    .crm-hero h2 { color: #ffffff !important; margin: 0 0 .35rem 0; font-size: 1.65rem; }
    .crm-hero p { color: #e4efff !important; margin: 0; font-size: .98rem; }
    .crm-hero .hero-eyebrow { color: #a9d8ff; text-transform: uppercase; letter-spacing: .13em; font-weight: 800; font-size: .72rem; margin-bottom: .45rem; }
    .crm-kpi {
        --accent: #4776e6; background: var(--secondary-background-color);
        border: 1px solid rgba(128, 145, 170, .25); border-left: 4px solid var(--accent);
        padding: 1rem 1rem .95rem; border-radius: 15px; min-height: 118px;
        box-shadow: 0 4px 14px rgba(15, 23, 42, .045); margin-bottom: .35rem;
    }
    .crm-kpi-top { display:flex; align-items:center; justify-content:space-between; gap:.5rem; }
    .crm-kpi-label { font-size:.80rem; font-weight:650; opacity:.78; line-height:1.3; }
    .crm-kpi-icon { font-size:1.15rem; }
    .crm-kpi-value { font-size:1.8rem; font-weight:800; line-height:1.25; margin-top:.45rem; letter-spacing:-.035em; }
    .crm-kpi-note { font-size:.76rem; opacity:.67; margin-top:.22rem; }
    .crm-section-title { font-size:1.08rem; font-weight:800; margin:.45rem 0 .6rem; }
    .crm-mini-card {
        background: var(--secondary-background-color); border:1px solid rgba(128,145,170,.24);
        padding:.9rem 1rem; border-radius:14px; margin:.35rem 0; border-left:4px solid #4776e6;
    }
    .crm-mini-card.overdue { border-left-color:#e05252; }
    .crm-mini-card.today { border-left-color:#e4a33a; }
    .crm-mini-card.upcoming { border-left-color:#279b7c; }
    .crm-mini-title { font-weight:750; font-size:.98rem; margin-bottom:.2rem; }
    .crm-mini-meta { font-size:.82rem; opacity:.74; line-height:1.45; }
    .crm-pill { display:inline-block; border-radius:99px; padding:.22rem .6rem; font-size:.72rem; font-weight:750; background:rgba(71,118,230,.13); color:#6f9cff; margin:0 .2rem .25rem 0; }
    .rating-summary {
        background: linear-gradient(135deg, rgba(246, 184, 54, .13), rgba(255, 255, 255, .015));
        border:1px solid rgba(226, 171, 55, .3); padding:1rem 1.1rem; border-radius:16px;
        margin:.35rem 0 .8rem; min-height:95px;
    }
    .rating-property-card {
        background:var(--secondary-background-color); border:1px solid rgba(128,145,170,.25);
        border-radius:15px; padding:1rem; margin:.35rem 0; min-height:132px;
    }
    .rating-property-title { font-size:1rem; font-weight:800; margin-bottom:.28rem; }
    .rating-score { font-size:1.45rem; font-weight:850; color:#d69a16; }
    .rating-stars { color:#e3a008; letter-spacing:.06em; font-size:1.08rem; }
    .rating-muted { opacity:.68; font-size:.82rem; }
    .review-card {
        border:1px solid rgba(128,145,170,.25); background:var(--secondary-background-color);
        border-radius:14px; padding:.9rem 1rem; margin:.45rem 0;
    }
    .review-heading { display:flex; flex-wrap:wrap; align-items:center; justify-content:space-between; gap:.35rem; }
    .review-name { font-weight:800; }
    .review-date { font-size:.77rem; opacity:.65; }
    .review-property { font-size:.85rem; font-weight:650; margin-top:.35rem; }
    .review-text { margin-top:.45rem; line-height:1.5; white-space:pre-wrap; }
    div[data-testid="stMetric"] { background: var(--secondary-background-color); padding: .75rem .9rem; border-radius: 13px; border: 1px solid rgba(128,145,170,.22); }
    </style>
    """,
    unsafe_allow_html=True,
)

def render_crm_kpi(label, value, note="", icon="📌", accent="#4776e6"):
    """Render a consistent, accessible KPI card using escaped text values."""
    label_html = html.escape(str(label))
    value_html = html.escape(str(value))
    note_html = html.escape(str(note))
    icon_html = html.escape(str(icon))
    accent_html = html.escape(str(accent), quote=True)
    st.markdown(
        f"""
        <div class="crm-kpi" style="--accent:{accent_html}">
          <div class="crm-kpi-top"><div class="crm-kpi-label">{label_html}</div><div class="crm-kpi-icon">{icon_html}</div></div>
          <div class="crm-kpi-value">{value_html}</div>
          <div class="crm-kpi-note">{note_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_crm_hero(eyebrow, title, description):
    """Render a consistent dashboard banner with escaped text."""
    st.markdown(
        f"""
        <div class="crm-hero">
          <div class="hero-eyebrow">{html.escape(str(eyebrow))}</div>
          <h2>{html.escape(str(title))}</h2>
          <p>{html.escape(str(description))}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# DATABASE FUNCTIONS
# =========================================================
def get_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"Database not found at {DB_PATH}. Run your database setup script first."
        )
    return sqlite3.connect(DB_PATH)


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
    with sqlite3.connect(DB_PATH) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
    with sqlite3.connect(backup_path) as check_conn:
        integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        backup_path.unlink(missing_ok=True)
        raise RuntimeError(f"Backup integrity check failed: {integrity}")
    return backup_path


def validate_database_backup(file_path):
    """Validate that an uploaded backup is a healthy SQLite database with a properties table."""
    try:
        with sqlite3.connect(file_path) as check_conn:
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
        conn.execute(
            f"INSERT INTO properties ({column_sql}) VALUES ({placeholders})",
            list(insert_data.values()),
        )


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
    """Resolve a saved image path whether it is absolute or project-relative."""
    if not has_value(image_value):
        return None
    raw_path = str(image_value).strip().strip('"')
    candidate = Path(raw_path)
    if candidate.is_file():
        return str(candidate)
    # Handle paths saved on Windows but read on another path style, and vice versa.
    normalized = raw_path.replace("\\", "/")
    candidate = BASE_DIR / normalized
    if candidate.is_file():
        return str(candidate)
    return None


def show_property_card(record, recommendation_reason=None):
    with st.container(border=True):
        if recommendation_reason:
            st.caption(f"💡 Why this is recommended: {recommendation_reason}")
        image = resolve_image(record.get("image_path"))
        if image:
            st.image(image, use_container_width=True)
        st.subheader(str(display_value(record, "title", "Property listing")))
        city = display_value(record, "city")
        ptype = display_value(record, "property_type")
        listing = display_value(record, "listing_type")
        st.write(f"**Location:** {city}")
        st.write(f"**Type:** {ptype} · **Listing:** {listing}")
        price = record.get("price")
        if has_value(price):
            try:
                st.write(f"**Price:** ₹{float(price):,.0f}")
            except (TypeError, ValueError):
                st.write(f"**Price:** {price}")
        bedrooms = record.get("bedrooms")
        if has_value(bedrooms):
            st.write(f"**Bedrooms:** {int(float(bedrooms))}")
        area = record.get("area_sqft")
        if has_value(area):
            try:
                st.write(f"**Area:** {float(area):,.0f} sq ft")
            except (TypeError, ValueError):
                st.write(f"**Area:** {area} sq ft")
        description = record.get("description")
        if has_value(description):
            st.write(str(description))
        amenities = record.get("amenities")
        if has_value(amenities):
            st.caption(f"Amenities: {amenities}")


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
# MAIN APP
# =========================================================
st.title("🏠 Property Agent Bot")
st.caption("Phase 6: property search, local AI recommendations, property enquiries, and lead management.")

notice = st.session_state.pop("property_agent_notice", None)
if notice:
    st.success(notice)

try:
    ensure_optional_columns()
    ensure_enquiries_table()
    save_image_paths()
    properties = get_properties()
    PROPERTY_RATING_SUMMARY = get_property_rating_summary()
except Exception as exc:
    st.error(f"Could not load the property database: {exc}")
    st.info("Check that data/property_agent.db exists and that it contains a 'properties' table.")
    st.stop()

if properties.empty:
    st.warning("There are no properties in the database yet. Add one using the Add Property tab.")

# Handle possible older database schemas without crashing the whole app.
for required in ("id", "title", "city", "property_type", "listing_type", "price", "bedrooms"):
    if required not in properties.columns:
        st.error(f"Your properties table is missing the expected column '{required}'. Please share init_db.py so the app can be adapted to your database schema.")
        st.stop()

search_tab, add_tab, tab_ai, enquiry_tab, lead_tab, rating_tab, backup_tab, duplicate_tab, delete_tab = st.tabs([
    "📊 Dashboard & Search",
    "➕ Add Property",
    "🤖 AI Property Assistant",
    "📩 Property Enquiry",
    "📋 Lead Management",
    "⭐ Ratings & Reviews",
    "💾 Data Safety & Backup",
    "🧹 Duplicate Cleanup",
    "🗑️ Remove Property",
])

with search_tab:
    st.header("Property Dashboard")
    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Total properties", len(properties))
    metric2.metric("For rent", int((properties["listing_type"].astype(str).str.lower() == "rent").sum()))
    metric3.metric("For sale", int((properties["listing_type"].astype(str).str.lower() == "sale").sum()))

    st.divider()
    st.header("Search Properties")
    query = st.text_input("Describe the property you want", placeholder="e.g. 2 BHK apartment for rent in Ahmedabad under 20000", key="property_search_query_phase6")
    filter_col1, filter_col2, filter_col3 = st.columns(3)
    city_options = ["Any"] + sorted(properties["city"].dropna().astype(str).unique().tolist())
    type_options = ["Any"] + sorted(properties["property_type"].dropna().astype(str).unique().tolist())
    listing_options = ["Any"] + sorted(properties["listing_type"].dropna().astype(str).unique().tolist())
    chosen_city = filter_col1.selectbox("City", city_options)
    chosen_type = filter_col2.selectbox("Property type", type_options)
    chosen_listing = filter_col3.selectbox("Listing type", listing_options)

    results = properties.copy()
    if query.strip():
        try:
            results, _parsed_filters = search_properties(results, query)
        except Exception as exc:
            st.warning(f"Text search could not be applied: {exc}")
    if chosen_city != "Any":
        results = results[results["city"].astype(str).str.lower() == chosen_city.lower()]
    if chosen_type != "Any":
        results = results[results["property_type"].astype(str).str.lower() == chosen_type.lower()]
    if chosen_listing != "Any":
        results = results[results["listing_type"].astype(str).str.lower() == chosen_listing.lower()]

    st.write(f"Found **{len(results)}** matching property/properties.")
    if results.empty:
        st.info("No properties match these requirements. Try changing the query or filters.")
    else:
        for _, row in results.iterrows():
            show_property_card(row.to_dict())

with add_tab:
    st.header("Add a Property")
    with st.form("add_property_form", clear_on_submit=True):
        title = st.text_input("Property title", placeholder="Modern 2 BHK Apartment", key="add_property_title_phase6")
        city = st.text_input("City", placeholder="Ahmedabad", key="add_property_city_phase6")
        col1, col2 = st.columns(2)
        property_type = col1.selectbox("Property type", ["Apartment", "Villa", "House", "Plot", "Commercial", "Other"])
        listing_type = col2.selectbox("Listing type", ["Rent", "Sale"])
        col3, col4 = st.columns(2)
        price = col3.number_input("Price (₹)", min_value=0.0, step=1000.0)
        bedrooms = col4.number_input(
            "Bedrooms", min_value=0, max_value=20, step=1, value=2,
            key="add_property_bedrooms_phase4_fix",
        )
        area_sqft = st.number_input("Area (sq ft)", min_value=0.0, step=50.0)
        description = st.text_area("Description")
        amenities = st.text_input("Amenities", placeholder="Parking, lift, security", key="add_property_amenities_phase6")
        uploaded_image = st.file_uploader(
            "Property photo (optional)",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=False,
            help="Choose a photo from your computer. JPG, PNG, or WEBP images are supported.",
        )
        if uploaded_image is not None:
            st.image(uploaded_image, caption="Selected property photo", use_container_width=True)
        submitted = st.form_submit_button("Save Property", type="primary")
    if submitted:
        if not title.strip() or not city.strip():
            st.error("Please enter both a property title and city.")
        else:
            saved_image_path = ""
            try:
                if uploaded_image is not None:
                    USER_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
                    suffix = Path(uploaded_image.name).suffix.lower()
                    if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
                        raise ValueError("Please upload a JPG, JPEG, PNG, or WEBP image.")
                    image_filename = f"property_{uuid4().hex}{suffix}"
                    image_destination = USER_UPLOADS_DIR / image_filename
                    image_destination.write_bytes(uploaded_image.getvalue())
                    saved_image_path = str(image_destination.resolve())

                new_data = {
                    "title": title.strip(),
                    "city": city.strip(),
                    "property_type": property_type,
                    "listing_type": listing_type,
                    "price": float(price),
                    "bedrooms": int(bedrooms),
                    "area_sqft": float(area_sqft),
                    "description": description.strip(),
                    "image_path": saved_image_path,
                    "amenities": amenities.strip(),
                }
                add_property(new_data)
                st.success("Property saved successfully. Its photo was saved too, if you uploaded one.")
                # Refresh the records after insert so the newest listing is available immediately.
                properties = get_properties()
                st.info("Your new listing is saved. Open Dashboard & Search to see its details and photo.")
            except Exception as exc:
                st.error(f"Could not save this property: {exc}")

with tab_ai:
    st.header("AI Property Assistant")
    st.write(
        "Search in everyday language. The assistant remembers your previous requirements "
        "during this session, ranks the closest listings, and explains why each property is suggested."
    )

    if "property_ai_turns" not in st.session_state:
        st.session_state["property_ai_turns"] = []
    if "property_ai_filters" not in st.session_state:
        st.session_state["property_ai_filters"] = {}
    if "property_ai_results" not in st.session_state:
        st.session_state["property_ai_results"] = []
    if "property_ai_exact" not in st.session_state:
        st.session_state["property_ai_exact"] = False

    assistant_controls = st.columns([1, 1, 3])
    if assistant_controls[0].button("Clear conversation", key="clear_property_ai"):
        st.session_state["property_ai_turns"] = []
        st.session_state["property_ai_filters"] = {}
        st.session_state["property_ai_results"] = []
        st.session_state["property_ai_exact"] = False
        st.rerun()
    if assistant_controls[1].button("Reset requirements", key="reset_property_ai_filters"):
        st.session_state["property_ai_filters"] = {}
        st.session_state["property_ai_results"] = []
        st.session_state["property_ai_exact"] = False
        st.session_state["property_agent_notice"] = "Previous AI search requirements have been cleared. Your conversation is still visible."
        st.rerun()

    for turn in st.session_state["property_ai_turns"]:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    with st.form("phase5_ai_search_form", clear_on_submit=True):
        ai_message = st.text_input(
            "Your request",
            placeholder="e.g. I need a 2 BHK apartment for rent in Ahmedabad under ₹20,000",
            key="local_ai_assistant_message_phase6",
        )
        submitted_ai_search = st.form_submit_button("Find recommendations", type="primary")

    if submitted_ai_search:
        if not ai_message or not ai_message.strip():
            st.warning("Please enter your property requirements first.")
        elif properties.empty:
            st.warning("There are no listings to recommend yet. Add a property first.")
        else:
            user_message = ai_message.strip()
            st.session_state["property_ai_turns"].append({"role": "user", "content": user_message})
            current_filters = {}
            ai_extraction_ok = True
            try:
                with st.spinner("Understanding your request with local AI..."):
                    raw_ai_filters = extract_property_filters(user_message)
                current_filters.update(normalise_ai_filters(raw_ai_filters))
            except Exception:
                ai_extraction_ok = False
                # The local parser still enables basic search if Ollama is unavailable.

            # Deterministic parsing is used as a safety net and takes precedence
            # for details explicitly present in the user's current sentence.
            try:
                parsed_now = parse_property_query(user_message)
                for key, value in parsed_now.items():
                    current_filters[key] = value
            except Exception:
                pass

            city_in_message = find_city_in_text(user_message, properties)
            if city_in_message:
                current_filters["city"] = city_in_message
            elif any(phrase in user_message.lower() for phrase in ("any city", "anywhere", "no city preference", "city doesn't matter", "city does not matter")):
                current_filters.pop("city", None)
                st.session_state["property_ai_filters"].pop("city", None)

            # Keep last-turn requirements when the user gives a short follow-up like
            # "make it 3 BHK" or "under 15000", while letting new values override old ones.
            effective_filters = merge_filter_context(st.session_state["property_ai_filters"], current_filters)
            st.session_state["property_ai_filters"] = effective_filters

            recommendations, exact_match = get_ranked_recommendations(properties, effective_filters, limit=5)
            st.session_state["property_ai_results"] = recommendations
            st.session_state["property_ai_exact"] = exact_match

            if not ai_extraction_ok:
                reply = "I couldn't reach Ollama for this turn, so I used the built-in search parser instead. Your local search can still work; check that Ollama is running to restore AI extraction."
            elif not effective_filters:
                reply = "I can help you compare available properties. To narrow the results, tell me a city, rent or sale, bedroom count, property type, or maximum budget. Here are the current listings ranked as general suggestions."
            elif exact_match:
                reply = f"I found {len(recommendations)} exact matching listing(s) using the requirements detected below."
            elif recommendations:
                reply = "I couldn't find a listing that satisfies every requirement at once, so I've ranked the closest alternatives. The reason shown on each card highlights which preferences it matches or relaxes."
            else:
                reply = "There are no properties in the database to recommend. Add listings first, then search again."

            st.session_state["property_ai_turns"].append({"role": "assistant", "content": reply + "\n\nDetected requirements: " + format_filters(effective_filters)})
            st.rerun()

    if st.session_state["property_ai_filters"]:
        st.caption("Current search context: " + format_filters(st.session_state["property_ai_filters"]))

    saved_recommendations = st.session_state.get("property_ai_results", [])
    if saved_recommendations:
        st.subheader("Recommended properties")
        if not st.session_state.get("property_ai_filters", {}):
            st.info("These are general suggestions from your current property listings. Add a city, budget, or bedroom count to narrow the results.")
        elif st.session_state.get("property_ai_exact", False):
            st.success("These listings satisfy all detected requirements.")
        else:
            st.info("No complete match was found. These are the closest alternatives available in your database.")
        for record in saved_recommendations:
            reason = record.get("_recommendation_reason")
            # If exact matches were returned, build a reason from the matched filters.
            if not reason:
                _, reason = score_property(record, st.session_state.get("property_ai_filters", {}))
            show_property_card(record, recommendation_reason=reason)
    elif st.session_state.get("property_ai_turns"):
        st.info("No recommendations are currently saved for this conversation. Try a new request or reset the requirements.")


with enquiry_tab:
    st.header("📩 Property Enquiry")
    st.write(
        "Use this form to record a customer's interest in a property. Enquiries are saved "
        "locally in your SQLite database and appear in Lead Management."
    )

    property_labels = {}
    for _, property_row in properties.iterrows():
        try:
            property_id = int(property_row["id"])
        except (TypeError, ValueError, KeyError):
            continue
        property_record = property_row.to_dict()
        property_title = str(display_value(property_record, "title", "Property listing"))
        property_city = str(display_value(property_record, "city", "Unknown city"))
        property_price = property_record.get("price")
        try:
            price_text = f"₹{float(property_price):,.0f}" if has_value(property_price) else "Price not set"
        except (TypeError, ValueError):
            price_text = str(property_price)
        property_labels[property_id] = f"{property_title} | {property_city} | {price_text}"

    property_records_by_id = {}
    for _, property_row in properties.iterrows():
        try:
            property_id_value = int(property_row["id"])
            property_records_by_id[property_id_value] = property_row.to_dict()
        except (TypeError, ValueError, KeyError):
            pass

    with st.form("customer_property_enquiry_form", clear_on_submit=False):
        enquiry_property_options = [None] + list(property_labels.keys())
        selected_enquiry_property = st.selectbox(
            "Interested property",
            options=enquiry_property_options,
            format_func=lambda pid: "General enquiry (no specific property)" if pid is None else property_labels.get(pid, f"Property ID {pid}"),
        )
        customer_name = st.text_input("Customer name *", placeholder="Enter customer name", key="enquiry_customer_name_phase6")
        customer_phone = st.text_input("Phone number *", placeholder="Enter contact number", key="enquiry_customer_phone_phase6")
        customer_email = st.text_input("Email (optional)", placeholder="name@example.com", key="enquiry_customer_email_phase6")
        enquiry_message = st.text_area(
            "Enquiry message (optional)",
            placeholder="e.g. I would like to arrange a visit this weekend.",
        )
        enquiry_source = st.selectbox("Enquiry source", ["Website", "Phone", "Walk-in", "Referral", "Social Media", "Other"])
        budget_col, timeline_col = st.columns(2)
        preferred_budget = budget_col.number_input("Customer's budget (₹, optional)", min_value=0.0, step=1000.0, value=0.0)
        move_in_timeline = timeline_col.selectbox("When is the customer planning to move?", ["Not specified", "Immediately", "Within 2 weeks", "Within 1 month", "Within 3 months", "Flexible"])
        duplicate_override = st.checkbox("I checked possible duplicates and still want to create another enquiry", value=False)
        submit_enquiry = st.form_submit_button("Save Enquiry", type="primary")

    if submit_enquiry:
        digits_in_phone = sum(character.isdigit() for character in customer_phone)
        if not customer_name.strip():
            st.error("Please enter the customer's name.")
        elif not customer_phone.strip() or digits_in_phone < 7:
            st.error("Please enter a valid phone number with at least 7 digits.")
        else:
            duplicates = find_duplicate_enquiries(customer_phone.strip(), selected_enquiry_property)
            if not duplicates.empty and not duplicate_override:
                st.warning("A possible duplicate enquiry was found. Check the records below. Tick the confirmation box in the form only if a separate enquiry is intentional, then submit again.")
                display_cols = [c for c in ("id", "created_at", "customer_name", "phone", "property_title", "status") if c in duplicates.columns]
                st.dataframe(duplicates[display_cols], use_container_width=True, hide_index=True)
            else:
                selected_record = property_records_by_id.get(selected_enquiry_property, {}) if selected_enquiry_property is not None else {}
                selected_property_title = str(selected_record.get("title", "General enquiry (no specific property)"))
                selected_property_city = str(selected_record.get("city", ""))
                timeline_value = "" if move_in_timeline == "Not specified" else move_in_timeline
                enquiry_payload = {
                    "property_id": selected_enquiry_property,
                    "property_title": selected_property_title,
                    "property_city": selected_property_city,
                    "customer_name": customer_name.strip(),
                    "phone": customer_phone.strip(),
                    "email": customer_email.strip(),
                    "message": enquiry_message.strip(),
                    "source": enquiry_source,
                    "preferred_budget": float(preferred_budget) if preferred_budget > 0 else None,
                    "move_in_timeline": timeline_value,
                    "priority": "Medium",
                }
                enquiry_payload["qualification_score"] = qualification_score_for(enquiry_payload)
                try:
                    add_enquiry(enquiry_payload)
                    st.session_state["property_agent_notice"] = f"Enquiry from {customer_name.strip()} saved successfully. Qualification completeness score: {enquiry_payload['qualification_score']}/100."
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save this enquiry: {exc}")

    st.caption("Privacy reminder: this local app stores customer contact details on this computer. Only enter details you are authorized to keep.")

with lead_tab:
    render_crm_hero(
        "Customer relationship workspace",
        "Lead Management",
        "Prioritize enquiries, stay ahead of follow-ups, and understand your conversion pipeline at a glance.",
    )

    try:
        enquiries = get_enquiries()
    except Exception as exc:
        st.error(f"Could not load enquiries: {exc}")
        enquiries = pd.DataFrame()

    if enquiries.empty:
        st.info("No enquiries have been recorded yet. Open Property Enquiry to add the first one.")
        st.markdown(
            "<div class='crm-mini-card'><div class='crm-mini-title'>Your lead workspace is ready</div>"
            "<div class='crm-mini-meta'>Once you add an enquiry, this page will show lead KPIs, follow-up reminders, monthly trends, and customer interaction tools.</div></div>",
            unsafe_allow_html=True,
        )
    else:
        today = date.today()
        follow_dates = pd.to_datetime(
            enquiries.get("follow_up_date", pd.Series(index=enquiries.index, dtype="object")),
            errors="coerce",
        ).dt.date
        active_mask = ~enquiries["status"].astype(str).isin(["Closed", "Won", "Lost"])
        overdue_mask = follow_dates.notna() & (follow_dates < today) & active_mask
        due_today_mask = follow_dates.notna() & (follow_dates == today) & active_mask
        upcoming_mask = follow_dates.notna() & (follow_dates > today) & (follow_dates <= (today + timedelta(days=7))) & active_mask
        total_leads = len(enquiries)
        new_leads = int((enquiries["status"].astype(str) == "New").sum())
        contacted_leads = int((enquiries["status"].astype(str) == "Contacted").sum())
        active_followups = int((enquiries["status"].astype(str) == "Follow-up").sum())
        won_leads = int((enquiries["status"].astype(str) == "Won").sum())
        high_priority = int((enquiries.get("priority", pd.Series("Medium", index=enquiries.index)).fillna("Medium").astype(str) == "High").sum())
        conversion_rate = (won_leads / total_leads * 100) if total_leads else 0.0

        st.markdown("<div class='crm-section-title'>📌 Pipeline at a glance</div>", unsafe_allow_html=True)
        kpi_rows = [
            ("Total leads", total_leads, "All recorded enquiries", "👥", "#4776e6"),
            ("New leads", new_leads, "Awaiting first contact", "✨", "#269b83"),
            ("Contacted", contacted_leads, "First contact completed", "☎️", "#8b6ee8"),
            ("Follow-up", active_followups, "Need continued attention", "🔁", "#d99b2b"),
            ("Overdue", int(overdue_mask.sum()), "Past follow-up date", "⏰", "#d94f59"),
            ("Won conversion", f"{conversion_rate:.1f}%", f"{won_leads} won lead(s)", "🏆", "#269b83"),
        ]
        for start in range(0, len(kpi_rows), 3):
            kpi_cols = st.columns(3)
            for col, metric in zip(kpi_cols, kpi_rows[start:start + 3]):
                with col:
                    render_crm_kpi(*metric)

        info_left, info_right = st.columns([1.35, 1])
        with info_left:
            st.markdown("<div class='crm-section-title'>🔔 Follow-up command center</div>", unsafe_allow_html=True)
            due_rows = enquiries[overdue_mask | due_today_mask | upcoming_mask].copy()
            if due_rows.empty:
                st.success("You're clear — no overdue or upcoming follow-ups in the next 7 days for active leads.")
            else:
                reminder_limit = min(len(due_rows), 8)
                due_rows = due_rows.head(reminder_limit)
                for idx, lead in due_rows.iterrows():
                    due_date = follow_dates.loc[idx]
                    if due_date < today:
                        label, css_class = "OVERDUE", "overdue"
                    elif due_date == today:
                        label, css_class = "DUE TODAY", "today"
                    else:
                        label, css_class = "UPCOMING", "upcoming"
                    name = html.escape(str(lead.get("customer_name", "Customer")))
                    property_title = html.escape(str(lead.get("property_title", "Property enquiry")))
                    phone = html.escape(str(lead.get("phone", "Not provided")))
                    status = html.escape(str(lead.get("status", "New")))
                    priority = html.escape(str(lead.get("priority", "Medium")))
                    st.markdown(
                        f"<div class='crm-mini-card {css_class}'><div class='crm-mini-title'>{name} <span class='crm-pill'>{label}</span></div>"
                        f"<div class='crm-mini-meta'>{property_title}<br>📅 {due_date.isoformat()} &nbsp; · &nbsp; {phone}<br>Status: {status} &nbsp; · &nbsp; Priority: {priority}</div></div>",
                        unsafe_allow_html=True,
                    )
                if len(enquiries[overdue_mask | due_today_mask | upcoming_mask]) > reminder_limit:
                    st.caption(f"Showing the first {reminder_limit} reminders. Use the filters below to find other leads.")

        with info_right:
            st.markdown("<div class='crm-section-title'>🎯 Lead quality & priority</div>", unsafe_allow_html=True)
            st.markdown(
                f"<div class='crm-mini-card'><div class='crm-mini-title'>🔥 {high_priority} high-priority lead(s)</div>"
                "<div class='crm-mini-meta'>Review these customers first and add a next action for each active lead.</div></div>",
                unsafe_allow_html=True,
            )
            avg_score = pd.to_numeric(enquiries.get("qualification_score", pd.Series(0, index=enquiries.index)), errors="coerce").fillna(0).mean()
            st.markdown(
                f"<div class='crm-mini-card' style='border-left-color:#8b6ee8'><div class='crm-mini-title'>🧭 {avg_score:.0f}/100 average completeness</div>"
                "<div class='crm-mini-meta'>This is a completeness measure based on information supplied, not a prediction that someone will buy or rent.</div></div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div class='crm-section-title'>📈 Business insights</div>", unsafe_allow_html=True)
        insight_left, insight_right = st.columns(2)
        with insight_left:
            st.caption("Lead status distribution")
            status_counts = enquiries["status"].fillna("Unknown").astype(str).value_counts().rename_axis("Status").reset_index(name="Count")
            st.bar_chart(status_counts.set_index("Status"))
        with insight_right:
            st.caption("Monthly enquiries")
            created_month = pd.to_datetime(enquiries.get("created_at", pd.Series(index=enquiries.index, dtype="object")), errors="coerce").dt.to_period("M").astype(str)
            monthly = created_month[created_month != "NaT"].value_counts().sort_index().rename_axis("Month").reset_index(name="Enquiries")
            if not monthly.empty:
                st.line_chart(monthly.set_index("Month"))
            else:
                st.info("Monthly trend will appear when enquiry timestamps are available.")

        st.divider()
        st.markdown("<div class='crm-section-title'>🔎 Find, filter and manage leads</div>", unsafe_allow_html=True)
        f1, f2, f3, f4 = st.columns(4)
        status_options = ["All statuses", "New", "Contacted", "Follow-up", "Closed", "Won", "Lost"]
        status_filter = f1.selectbox("Status", status_options, key="lead_status_filter_phase6_advanced")
        priority_filter = f2.selectbox("Priority", ["All priorities", "High", "Medium", "Low"], key="lead_priority_filter_phase6")
        source_options = ["All sources"] + sorted(enquiries.get("source", pd.Series(dtype=str)).fillna("Other").astype(str).unique().tolist())
        source_filter = f3.selectbox("Source", source_options, key="lead_source_filter_phase6")
        lead_search = f4.text_input("Search leads", placeholder="Name, phone, email, property, or message", key="lead_search_text_phase6")
        f5, f6 = st.columns(2)
        city_options = ["All cities"] + sorted([x for x in enquiries.get("property_city", pd.Series(dtype=str)).fillna("").astype(str).unique().tolist() if x.strip()])
        city_filter = f5.selectbox("Property city", city_options, key="lead_city_filter_phase6")
        budget_filter = f6.number_input("Budget maximum (₹, 0 = any)", min_value=0.0, step=1000.0, value=0.0, key="lead_budget_filter_phase6")

        filtered_enquiries = enquiries.copy()
        if status_filter != "All statuses":
            filtered_enquiries = filtered_enquiries[filtered_enquiries["status"].astype(str) == status_filter]
        if priority_filter != "All priorities":
            filtered_enquiries = filtered_enquiries[filtered_enquiries["priority"].fillna("Medium").astype(str) == priority_filter]
        if source_filter != "All sources" and "source" in filtered_enquiries.columns:
            filtered_enquiries = filtered_enquiries[filtered_enquiries["source"].fillna("Other").astype(str) == source_filter]
        if city_filter != "All cities" and "property_city" in filtered_enquiries.columns:
            filtered_enquiries = filtered_enquiries[filtered_enquiries["property_city"].fillna("").astype(str) == city_filter]
        if budget_filter > 0 and "preferred_budget" in filtered_enquiries.columns:
            lead_budgets = pd.to_numeric(filtered_enquiries["preferred_budget"], errors="coerce")
            filtered_enquiries = filtered_enquiries[lead_budgets.isna() | (lead_budgets <= budget_filter)]
        if lead_search.strip():
            query_lower = lead_search.strip().lower()
            searchable_columns = [c for c in ("customer_name", "phone", "email", "property_title", "property_city", "message", "source") if c in filtered_enquiries.columns]
            mask = pd.Series(False, index=filtered_enquiries.index)
            for column in searchable_columns:
                mask = mask | filtered_enquiries[column].fillna("").astype(str).str.lower().str.contains(query_lower, regex=False)
            filtered_enquiries = filtered_enquiries[mask]

        st.write(f"Showing **{len(filtered_enquiries)}** of **{len(enquiries)}** enquiries.")
        if not filtered_enquiries.empty:
            table_columns = [c for c in ("id", "created_at", "customer_name", "phone", "property_title", "property_city", "status", "priority", "qualification_score", "source", "preferred_budget", "follow_up_date") if c in filtered_enquiries.columns]
            st.dataframe(filtered_enquiries[table_columns], use_container_width=True, hide_index=True)
            st.download_button(
                "Download filtered enquiries as CSV",
                data=filtered_enquiries.to_csv(index=False).encode("utf-8-sig"),
                file_name="property_enquiries.csv", mime="text/csv", key="download_property_enquiries_csv_phase6_advanced",
            )

            lead_labels = {}
            for _, lead_row in filtered_enquiries.iterrows():
                lead_id = int(lead_row["id"])
                lead_labels[lead_id] = f"#{lead_id} | {display_value(lead_row.to_dict(), 'customer_name')} | {display_value(lead_row.to_dict(), 'property_title')} | {display_value(lead_row.to_dict(), 'status')}"
            st.subheader("Review a lead")
            selected_lead_id = st.selectbox("Select enquiry", options=list(lead_labels.keys()), format_func=lambda lead_id: lead_labels[lead_id], key="selected_lead_for_edit_phase6_advanced")
            matching_lead = filtered_enquiries[filtered_enquiries["id"] == selected_lead_id]
            if not matching_lead.empty:
                lead_record = matching_lead.iloc[0].to_dict()
                det1, det2 = st.columns(2)
                with det1:
                    st.write(f"**Customer:** {display_value(lead_record, 'customer_name')}")
                    st.write(f"**Phone:** {display_value(lead_record, 'phone')}")
                    st.write(f"**Email:** {display_value(lead_record, 'email')}")
                    st.write(f"**Source:** {display_value(lead_record, 'source')}")
                with det2:
                    st.write(f"**Property:** {display_value(lead_record, 'property_title')}")
                    st.write(f"**City:** {display_value(lead_record, 'property_city')}")
                    st.write(f"**Submitted:** {display_value(lead_record, 'created_at')}")
                    st.write(f"**Original message:** {display_value(lead_record, 'message')}")
                st.caption(f"Qualification completeness: {int(lead_record.get('qualification_score') or 0)}/100. This score measures how complete the enquiry details are, not the customer's likelihood to buy.")

                has_follow_up_default = has_value(lead_record.get("follow_up_date"))
                schedule_follow_up = st.checkbox("Set a follow-up date", value=has_follow_up_default, key=f"lead_followup_enabled_adv_{selected_lead_id}")
                parsed_follow_up = None
                if has_follow_up_default:
                    try:
                        parsed_follow_up = date.fromisoformat(str(lead_record["follow_up_date"]))
                    except (TypeError, ValueError):
                        pass
                current_follow_up = None
                if schedule_follow_up:
                    current_follow_up = st.date_input("Follow-up date", value=parsed_follow_up or date.today(), key=f"lead_followup_date_adv_{selected_lead_id}")

                with st.form(f"update_lead_form_adv_{selected_lead_id}"):
                    statuses = ["New", "Contacted", "Follow-up", "Closed", "Won", "Lost"]
                    saved_status = str(lead_record.get("status", "New"))
                    if saved_status not in statuses:
                        saved_status = "New"
                    updated_status = st.selectbox("Lead status", statuses, index=statuses.index(saved_status), key=f"lead_status_editor_adv_{selected_lead_id}")
                    priority_options = ["High", "Medium", "Low"]
                    saved_priority = str(lead_record.get("priority", "Medium"))
                    if saved_priority not in priority_options:
                        saved_priority = "Medium"
                    updated_priority = st.selectbox("Priority", priority_options, index=priority_options.index(saved_priority), key=f"lead_priority_editor_adv_{selected_lead_id}")
                    budget_default = 0.0
                    try:
                        budget_default = max(0.0, float(lead_record.get("preferred_budget") or 0))
                    except (TypeError, ValueError):
                        pass
                    updated_budget = st.number_input("Customer budget (₹; 0 = not specified)", min_value=0.0, value=budget_default, step=1000.0, key=f"lead_budget_editor_adv_{selected_lead_id}")
                    timeline_options = ["Not specified", "Immediately", "Within 2 weeks", "Within 1 month", "Within 3 months", "Flexible"]
                    saved_timeline = str(lead_record.get("move_in_timeline") or "Not specified")
                    if saved_timeline not in timeline_options:
                        saved_timeline = "Not specified"
                    updated_timeline = st.selectbox("Moving timeline", timeline_options, index=timeline_options.index(saved_timeline), key=f"lead_timeline_editor_adv_{selected_lead_id}")
                    updated_notes = st.text_area("Agent notes", value=str(lead_record.get("agent_notes") or ""), placeholder="Summarize the next action; history is recorded separately below.", key=f"lead_notes_editor_adv_{selected_lead_id}")
                    save_lead_update = st.form_submit_button("Save lead updates", type="primary")

                if save_lead_update:
                    follow_up_value = current_follow_up.isoformat() if schedule_follow_up and current_follow_up else None
                    updated_timeline_value = "" if updated_timeline == "Not specified" else updated_timeline
                    lead_payload_for_score = {
                        "property_id": lead_record.get("property_id"),
                        "preferred_budget": updated_budget if updated_budget > 0 else None,
                        "move_in_timeline": updated_timeline_value,
                        "message": lead_record.get("message", ""),
                    }
                    new_score = qualification_score_for(lead_payload_for_score)
                    try:
                        update_enquiry(selected_lead_id, updated_status, follow_up_value, updated_notes.strip(), updated_priority, updated_budget if updated_budget > 0 else None, updated_timeline_value, new_score)
                        if str(lead_record.get("status", "New")) != updated_status:
                            add_interaction(selected_lead_id, "Status change", f"Status changed from {lead_record.get('status', 'New')} to {updated_status}.")
                        if str(lead_record.get("priority", "Medium")) != updated_priority:
                            add_interaction(selected_lead_id, "Priority change", f"Priority changed from {lead_record.get('priority', 'Medium')} to {updated_priority}.")
                        st.session_state["property_agent_notice"] = f"Lead #{selected_lead_id} updated successfully."
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not update the lead: {exc}")

                st.subheader("🕘 Interaction history")
                history = get_interactions(selected_lead_id)
                if history.empty:
                    st.info("No interaction history yet. Record the first call, visit, message, or meeting below.")
                else:
                    st.dataframe(history, use_container_width=True, hide_index=True)
                with st.form(f"add_interaction_form_{selected_lead_id}", clear_on_submit=True):
                    interaction_type = st.selectbox("Interaction type", ["Call", "WhatsApp/message", "Email", "Property visit", "Meeting", "Status change", "Other"], key=f"interaction_type_{selected_lead_id}")
                    interaction_notes = st.text_area("Interaction notes *", placeholder="What happened and what should happen next?", key=f"interaction_notes_{selected_lead_id}")
                    interaction_submit = st.form_submit_button("Add to history")
                if interaction_submit:
                    if not interaction_notes.strip():
                        st.error("Please add a short note to the interaction.")
                    else:
                        try:
                            add_interaction(selected_lead_id, interaction_type, interaction_notes.strip())
                            st.session_state["property_agent_notice"] = "Interaction added to the lead timeline."
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Could not add interaction: {exc}")

                st.subheader("✍️ AI follow-up message")
                st.caption("The draft is generated locally through Ollama when available. Review it before sending; it is not sent automatically.")
                if st.button("Generate follow-up draft", key=f"generate_followup_{selected_lead_id}"):
                    with st.spinner("Drafting a message locally..."):
                        draft, used_ai = generate_followup_draft(lead_record)
                    st.session_state[f"followup_draft_{selected_lead_id}"] = draft
                    st.session_state[f"followup_used_ai_{selected_lead_id}"] = used_ai
                draft_key = f"followup_draft_{selected_lead_id}"
                if draft_key in st.session_state:
                    if st.session_state.get(f"followup_used_ai_{selected_lead_id}"):
                        st.success("Draft generated with local Ollama. Review before sending.")
                    else:
                        st.info("Ollama was unavailable, so a basic editable template was provided.")
                    edited_draft = st.text_area("Review and edit the message", value=st.session_state[draft_key], key=f"followup_draft_editor_{selected_lead_id}", height=150)
                    st.download_button("Download message draft", data=edited_draft.encode("utf-8"), file_name=f"followup_lead_{selected_lead_id}.txt", mime="text/plain", key=f"download_followup_{selected_lead_id}")
        else:
            st.info("No enquiries match those filters. Change the filters to see more leads.")


with rating_tab:
    render_crm_hero(
        "Property reputation center",
        "Ratings & Reviews",
        "Collect customer feedback, see which listings earn trust, and review property experience in one place.",
    )

    ratings_frame = get_property_ratings()
    rating_property_choices = {}
    for _, row in properties.iterrows():
        try:
            pid = int(row["id"])
            rating_property_choices[pid] = f"{display_value(row.to_dict(), 'title')} | {display_value(row.to_dict(), 'city')}"
        except (TypeError, ValueError, KeyError):
            pass

    if ratings_frame.empty:
        total_reviews = 0
        overall_avg = 0.0
        five_star_count = 0
        rated_property_count = 0
    else:
        ratings_values = pd.to_numeric(ratings_frame["rating"], errors="coerce").dropna()
        total_reviews = int(len(ratings_values))
        overall_avg = float(ratings_values.mean()) if not ratings_values.empty else 0.0
        five_star_count = int((ratings_values == 5).sum())
        rated_property_count = int(ratings_frame.loc[ratings_frame["property_id"].notna(), "property_id"].nunique())

    st.markdown("<div class='crm-section-title'>⭐ Reputation overview</div>", unsafe_allow_html=True)
    rating_kpis = st.columns(4)
    kpi_data = [
        ("Total reviews", total_reviews, "Feedback entries", "🗣️", "#4776e6"),
        ("Average score", f"{overall_avg:.1f}/5" if total_reviews else "—", "Across all reviews", "⭐", "#d99b2b"),
        ("5-star reviews", five_star_count, "Highest rated feedback", "🌟", "#269b83"),
        ("Rated properties", rated_property_count, "Distinct listings reviewed", "🏠", "#8b6ee8"),
    ]
    for col, metric in zip(rating_kpis, kpi_data):
        with col:
            render_crm_kpi(*metric)

    form_col, side_col = st.columns([1.1, 0.9], gap="large")
    with form_col:
        st.markdown("<div class='crm-section-title'>✍️ Add a property review</div>", unsafe_allow_html=True)
        st.caption("Ratings are attached to a property listing. Reviewer name and written feedback are optional.")
        if not rating_property_choices:
            st.info("Add a property before recording a rating.")
        else:
            with st.form("property_rating_form_stylish", clear_on_submit=True):
                rating_property_id = st.selectbox(
                    "Property to rate",
                    options=list(rating_property_choices.keys()),
                    format_func=lambda pid: rating_property_choices[pid],
                    key="rating_property_select_stylish_phase6",
                )
                reviewer_name = st.text_input(
                    "Reviewer name (optional)",
                    placeholder="Customer name",
                    key="ratings_reviewer_name_stylish_phase6",
                )
                rating_value = st.select_slider(
                    "Your rating",
                    options=[1, 2, 3, 4, 5],
                    value=5,
                    format_func=lambda value: f"{'⭐' * value}  {value}/5",
                    key="rating_value_stylish_phase6",
                )
                rating_review = st.text_area(
                    "Written review (optional)",
                    placeholder="What stood out about the property or viewing experience?",
                    key="rating_review_stylish_phase6",
                    height=110,
                )
                submit_rating = st.form_submit_button("⭐ Save property rating", type="primary", use_container_width=True)
            if submit_rating:
                selected_record = properties[properties["id"] == rating_property_id]
                selected_title = str(selected_record.iloc[0].get("title", "Property listing")) if not selected_record.empty else rating_property_choices[rating_property_id]
                try:
                    submit_property_rating(int(rating_property_id), selected_title, reviewer_name, rating_value, rating_review)
                    st.session_state["property_agent_notice"] = "Rating saved successfully. The rating dashboard has been refreshed."
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not save the rating: {exc}")

    with side_col:
        st.markdown("<div class='crm-section-title'>🏆 Best-rated properties</div>", unsafe_allow_html=True)
        if ratings_frame.empty:
            st.markdown(
                "<div class='rating-summary'><div class='rating-property-title'>Your first review starts here</div>"
                "<div class='rating-muted'>After the first rating is saved, property averages and review counts will appear in this panel.</div></div>",
                unsafe_allow_html=True,
            )
        else:
            summary_rows = ratings_frame[ratings_frame["property_id"].notna()].groupby(
                ["property_id", "property_title"], as_index=False
            ).agg(Average_Rating=("rating", "mean"), Ratings=("rating", "count"))
            summary_rows = summary_rows.sort_values(["Average_Rating", "Ratings"], ascending=[False, False]).head(6)
            if summary_rows.empty:
                st.info("No current property-linked reviews are available yet.")
            else:
                for _, summary in summary_rows.iterrows():
                    title_html = html.escape(str(summary["property_title"]))
                    avg_value = float(summary["Average_Rating"])
                    count_value = int(summary["Ratings"])
                    full_stars = "★" * max(0, min(5, int(round(avg_value))))
                    empty_stars = "☆" * (5 - len(full_stars))
                    st.markdown(
                        f"<div class='rating-property-card'><div class='rating-property-title'>{title_html}</div>"
                        f"<div class='rating-stars'>{full_stars}<span style='color:#9aa5b4'>{empty_stars}</span></div>"
                        f"<div><span class='rating-score'>{avg_value:.1f}</span><span class='rating-muted'> / 5 &nbsp; · &nbsp; {count_value} review(s)</span></div></div>",
                        unsafe_allow_html=True,
                    )

    st.divider()
    st.markdown("<div class='crm-section-title'>📚 Browse customer feedback</div>", unsafe_allow_html=True)
    if ratings_frame.empty:
        st.info("No ratings have been submitted yet. Use the form above to add the first review.")
    else:
        property_filter_labels = ["All properties"] + sorted(ratings_frame["property_title"].fillna("Property listing").astype(str).unique().tolist())
        review_filter_col, sort_col = st.columns([2, 1])
        chosen_review_property = review_filter_col.selectbox(
            "Filter by property", property_filter_labels, key="ratings_property_filter_stylish_phase6"
        )
        review_sort = sort_col.selectbox(
            "Order", ["Newest first", "Highest rating first", "Lowest rating first"], key="ratings_sort_stylish_phase6"
        )
        view_ratings = ratings_frame.copy()
        if chosen_review_property != "All properties":
            view_ratings = view_ratings[view_ratings["property_title"].fillna("Property listing").astype(str) == chosen_review_property]
        if review_sort == "Highest rating first":
            view_ratings = view_ratings.sort_values(["rating", "created_at"], ascending=[False, False])
        elif review_sort == "Lowest rating first":
            view_ratings = view_ratings.sort_values(["rating", "created_at"], ascending=[True, False])
        else:
            view_ratings = view_ratings.sort_values(["created_at", "id"], ascending=[False, False])

        st.caption(f"Showing {len(view_ratings)} review(s).")
        if view_ratings.empty:
            st.info("No reviews match this property filter.")
        else:
            for _, review_row in view_ratings.head(30).iterrows():
                reviewer = str(review_row.get("reviewer_name") or "Anonymous reviewer")
                title = str(review_row.get("property_title") or "Property listing")
                review_text = str(review_row.get("review") or "No written comment provided.")
                created_at = str(review_row.get("created_at") or "Date not recorded")
                try:
                    rating_num = max(1, min(5, int(review_row.get("rating", 0))))
                except (TypeError, ValueError):
                    rating_num = 0
                stars_html = "★" * rating_num + "☆" * (5 - rating_num)
                st.markdown(
                    f"<div class='review-card'><div class='review-heading'><div class='review-name'>{html.escape(reviewer)}</div>"
                    f"<div class='rating-stars'>{stars_html}</div><div class='review-date'>{html.escape(created_at)}</div></div>"
                    f"<div class='review-property'>🏠 {html.escape(title)}</div><div class='review-text'>{html.escape(review_text)}</div></div>",
                    unsafe_allow_html=True,
                )
            if len(view_ratings) > 30:
                st.caption("Showing the latest 30 matching reviews to keep the page fast.")


with backup_tab:
    st.header("💾 Data Safety, Backup & Recovery")
    st.write("Create a consistent local SQLite backup before major edits or deletes. Restoring a backup replaces the current database, so always review the confirmation carefully.")
    if st.button("Create a database backup now", type="primary", key="create_db_backup_phase6"):
        try:
            created_backup = create_database_backup()
            st.session_state["latest_created_backup"] = str(created_backup)
            st.success(f"Backup created: {created_backup}")
        except Exception as exc:
            st.error(f"Could not create backup: {exc}")
    latest_backup = st.session_state.get("latest_created_backup")
    if latest_backup and Path(latest_backup).is_file():
        backup_file = Path(latest_backup)
        st.download_button("Download latest backup", data=backup_file.read_bytes(), file_name=backup_file.name, mime="application/x-sqlite3", key="download_latest_db_backup")

    st.divider()
    st.subheader("Restore from a database backup")
    st.warning("Restoration replaces your current database. A backup of the current database is created automatically first. Do not restore a database from an unknown source.")
    uploaded_backup = st.file_uploader("Choose a Property Agent SQLite backup (.db)", type=["db", "sqlite", "sqlite3"], key="restore_database_upload")
    confirm_restore = st.checkbox("I understand that restoring replaces the current database.", key="confirm_restore_database")
    if uploaded_backup is not None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".db") as temp_file:
            temp_file.write(uploaded_backup.getvalue())
            temp_backup_path = Path(temp_file.name)
        valid_backup, validation_message = validate_database_backup(temp_backup_path)
        if valid_backup:
            st.success(validation_message)
        else:
            st.error(validation_message)
        if st.button("Restore selected backup", type="primary", key="restore_database_btn"):
            if not confirm_restore:
                st.error("Tick the confirmation box before restoring.")
            elif not valid_backup:
                st.error("Select a valid backup before restoring.")
            else:
                try:
                    safety_backup = create_database_backup()
                    shutil.copy2(temp_backup_path, DB_PATH)
                    st.session_state["property_agent_notice"] = f"Database restored. Previous database saved to {safety_backup.name}."
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not restore this backup: {exc}")
                finally:
                    try:
                        temp_backup_path.unlink(missing_ok=True)
                    except OSError:
                        pass


with duplicate_tab:
    render_crm_hero(
        "DATA QUALITY",
        "Duplicate Checker & Cleanup",
        "Review repeated property listings and enquiry records, then remove selected duplicates together.",
    )
    st.warning(
        "Duplicates are suggested by matching fields, so a match is not proof of duplication. "
        "Review the records before deletion. A database backup is created automatically before each bulk cleanup."
    )

    st.subheader("1. Duplicate property listings")
    st.caption(
        "Listings are compared using title, city, property type, rent/sale type, price, bedrooms, and area. "
        "Spacing and letter case are ignored. The lowest ID in each group is marked KEEP by default."
    )
    try:
        current_property_frame = get_properties()
        property_groups = find_duplicate_property_groups(current_property_frame)
    except Exception as exc:
        property_groups = []
        st.error(f"Could not scan property duplicates: {exc}")

    duplicate_property_candidates = {}
    if not property_groups:
        st.success("No exact-like duplicate property groups were found.")
    else:
        property_group_rows = []
        for group_number, group in enumerate(property_groups, start=1):
            keep_record = group["keep"]
            for record in group["records"]:
                is_keeper = int(record.get("id", 0) or 0) == int(keep_record.get("id", 0) or 0)
                price = record.get("price")
                try:
                    price_text = f"₹{float(price):,.0f}" if has_value(price) else "Not set"
                except (TypeError, ValueError):
                    price_text = str(price) if has_value(price) else "Not set"
                area = record.get("area_sqft")
                area_text = f"{float(area):,.0f} sq ft" if has_value(area) else "Not set"
                role = "KEEP (oldest ID)" if is_keeper else "Duplicate candidate"
                property_group_rows.append({
                    "Group": group_number,
                    "ID": record.get("id"),
                    "Decision suggestion": role,
                    "Title": record.get("title", ""),
                    "City": record.get("city", ""),
                    "Type": record.get("property_type", ""),
                    "Listing": record.get("listing_type", ""),
                    "Price": price_text,
                    "Bedrooms": record.get("bedrooms", ""),
                    "Area": area_text,
                })
                if not is_keeper:
                    record_id = int(record.get("id", 0) or 0)
                    duplicate_property_candidates[record_id] = (
                        f"ID {record_id} | {record.get('title', 'Property')} | "
                        f"{record.get('city', 'Unknown city')} | {price_text} | group {group_number}"
                    )
        st.dataframe(pd.DataFrame(property_group_rows), use_container_width=True, hide_index=True)
        st.info(
            f"Found {len(property_groups)} duplicate group(s) and "
            f"{len(duplicate_property_candidates)} suggested duplicate listing(s)."
        )
        with st.form("bulk_duplicate_property_cleanup_form"):
            chosen_duplicate_property_ids = st.multiselect(
                "Select duplicate property records to remove",
                options=list(duplicate_property_candidates.keys()),
                format_func=lambda value: duplicate_property_candidates[value],
                key="duplicate_property_ids_to_remove",
                help="Only candidate IDs are selectable here; the suggested KEEP record is not included.",
            )
            confirm_property_cleanup = st.checkbox(
                "I reviewed these property records and understand deletion is permanent.",
                key="confirm_bulk_duplicate_property_cleanup",
            )
            submitted_property_cleanup = st.form_submit_button(
                "Back up and remove selected duplicate properties", type="primary"
            )

        if submitted_property_cleanup:
            if not chosen_duplicate_property_ids:
                st.error("Select at least one duplicate property candidate to remove.")
            elif not confirm_property_cleanup:
                st.error("Confirm that you reviewed the records before removing them.")
            else:
                backup_path = None
                removed_ids = []
                try:
                    backup_path = create_database_backup()
                    for selected_id in chosen_duplicate_property_ids:
                        delete_property(int(selected_id))
                        removed_ids.append(int(selected_id))
                    st.session_state["property_agent_notice"] = (
                        f"Removed {len(removed_ids)} duplicate property record(s): "
                        f"{', '.join(map(str, removed_ids))}. Safety backup: {backup_path.name}."
                    )
                    st.rerun()
                except Exception as exc:
                    backup_note = f" A safety backup was created at {backup_path}." if backup_path else " No cleanup was performed before the backup could be created."
                    st.error(
                        f"Property duplicate cleanup stopped: {exc}. Removed before the error: {removed_ids}.{backup_note}"
                    )

    st.divider()
    st.subheader("2. Duplicate customer enquiries")
    st.caption(
        "Enquiries are considered possible duplicates when normalized phone number, customer name, "
        "property, and message match. Different enquiries from the same customer can be legitimate; review before deleting. "
        "The lowest ID is suggested to keep."
    )
    try:
        current_enquiry_frame = get_enquiries()
        enquiry_groups = find_duplicate_enquiry_groups(current_enquiry_frame)
    except Exception as exc:
        enquiry_groups = []
        st.error(f"Could not scan enquiry duplicates: {exc}")

    duplicate_enquiry_candidates = {}
    if not enquiry_groups:
        st.success("No likely duplicate enquiry groups were found.")
    else:
        enquiry_group_rows = []
        for group_number, group in enumerate(enquiry_groups, start=1):
            keep_record = group["keep"]
            for record in group["records"]:
                is_keeper = int(record.get("id", 0) or 0) == int(keep_record.get("id", 0) or 0)
                record_id = int(record.get("id", 0) or 0)
                role = "KEEP (oldest ID)" if is_keeper else "Duplicate candidate"
                enquiry_group_rows.append({
                    "Group": group_number,
                    "ID": record_id,
                    "Decision suggestion": role,
                    "Created": record.get("created_at", ""),
                    "Customer": record.get("customer_name", ""),
                    "Phone": record.get("phone", ""),
                    "Property": record.get("property_title", ""),
                    "Status": record.get("status", ""),
                    "Message": record.get("message", ""),
                })
                if not is_keeper:
                    duplicate_enquiry_candidates[record_id] = (
                        f"ID {record_id} | {record.get('customer_name', 'Customer')} | "
                        f"{record.get('phone', '')} | {record.get('property_title', 'Property')} | group {group_number}"
                    )
        st.dataframe(pd.DataFrame(enquiry_group_rows), use_container_width=True, hide_index=True)
        st.info(
            f"Found {len(enquiry_groups)} possible duplicate group(s) and "
            f"{len(duplicate_enquiry_candidates)} suggested duplicate enquiry record(s)."
        )
        with st.form("bulk_duplicate_enquiry_cleanup_form"):
            chosen_duplicate_enquiry_ids = st.multiselect(
                "Select duplicate enquiry records to remove",
                options=list(duplicate_enquiry_candidates.keys()),
                format_func=lambda value: duplicate_enquiry_candidates[value],
                key="duplicate_enquiry_ids_to_remove",
                help="The suggested KEEP record is not selectable. Removing an enquiry also removes its interaction timeline.",
            )
            confirm_enquiry_cleanup = st.checkbox(
                "I reviewed these enquiry records and understand their interaction history will also be removed.",
                key="confirm_bulk_duplicate_enquiry_cleanup",
            )
            submitted_enquiry_cleanup = st.form_submit_button(
                "Back up and remove selected duplicate enquiries", type="primary"
            )

        if submitted_enquiry_cleanup:
            if not chosen_duplicate_enquiry_ids:
                st.error("Select at least one duplicate enquiry candidate to remove.")
            elif not confirm_enquiry_cleanup:
                st.error("Confirm that you reviewed the enquiry records before removing them.")
            else:
                backup_path = None
                try:
                    backup_path = create_database_backup()
                    deleted_count = delete_enquiry_records(chosen_duplicate_enquiry_ids)
                    st.session_state["property_agent_notice"] = (
                        f"Removed {deleted_count} duplicate enquiry record(s). "
                        f"Safety backup: {backup_path.name}."
                    )
                    st.rerun()
                except Exception as exc:
                    backup_note = f" A safety backup was created at {backup_path}." if backup_path else " No cleanup was performed before the backup could be created."
                    st.error(f"Enquiry duplicate cleanup failed: {exc}.{backup_note}")


with delete_tab:
    st.header("Remove a Property")
    st.warning(
        "Deleting a property permanently removes its database listing. An uploaded photo "
        "is removed only when it is inside this app's user_uploads folder and is not "
        "referenced by another property. Built-in/sample images are preserved."
    )

    if properties.empty:
        st.info("There are no properties to remove.")
    else:
        property_labels = {}
        for _, property_row in properties.iterrows():
            try:
                property_id = int(property_row["id"])
            except (TypeError, ValueError, KeyError):
                continue
            property_title = str(display_value(property_row.to_dict(), "title", "Property listing"))
            property_city = str(display_value(property_row.to_dict(), "city", "Unknown city"))
            property_price = property_row.get("price")
            if has_value(property_price):
                try:
                    price_text = f"₹{float(property_price):,.0f}"
                except (TypeError, ValueError):
                    price_text = str(property_price)
            else:
                price_text = "Price not set"
            property_labels[property_id] = (
                f"ID {property_id} | {property_title} | {property_city} | {price_text}"
            )

        if not property_labels:
            st.error("No properties with valid IDs were found, so none can be deleted.")
        else:
            with st.form("delete_property_form"):
                selected_property_id = st.selectbox(
                    "Choose the property to remove",
                    options=list(property_labels.keys()),
                    format_func=lambda property_id: property_labels[property_id],
                    key="remove_property_id",
                )
                confirm_delete = st.checkbox(
                    "I understand that this action permanently deletes the selected listing.",
                    key="confirm_remove_property",
                )
                delete_submitted = st.form_submit_button(
                    "Delete selected property", type="primary"
                )

            if delete_submitted:
                if not confirm_delete:
                    st.error("Please tick the confirmation box before deleting the property.")
                else:
                    try:
                        delete_property(int(selected_property_id))
                        st.session_state["property_agent_notice"] = (
                            f"Property ID {selected_property_id} was deleted successfully."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Could not delete the property: {exc}")

