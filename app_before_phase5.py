import sqlite3
from pathlib import Path
from uuid import uuid4

import pandas as pd
import streamlit as st

from services.local_ai import extract_property_filters, build_search_query
from services.property_search import search_properties

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


def show_property_card(record):
    with st.container(border=True):
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
        return "No structured filters extracted."
    return " · ".join(f"{key.replace('_', ' ').title()}: {value}" for key, value in filters.items())


# =========================================================
# MAIN APP
# =========================================================
st.title("🏠 Property Agent Bot")
st.caption("Search and manage property listings with a local AI assistant powered by Ollama.")

notice = st.session_state.pop("property_agent_notice", None)
if notice:
    st.success(notice)

try:
    ensure_optional_columns()
    save_image_paths()
    properties = get_properties()
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

search_tab, add_tab, tab_ai, delete_tab = st.tabs(["📊 Dashboard & Search", "➕ Add Property", "🤖 AI Property Assistant", "🗑️ Remove Property"])

with search_tab:
    st.header("Property Dashboard")
    metric1, metric2, metric3 = st.columns(3)
    metric1.metric("Total properties", len(properties))
    metric2.metric("For rent", int((properties["listing_type"].astype(str).str.lower() == "rent").sum()))
    metric3.metric("For sale", int((properties["listing_type"].astype(str).str.lower() == "sale").sum()))

    st.divider()
    st.header("Search Properties")
    query = st.text_input("Describe the property you want", placeholder="e.g. 2 BHK apartment for rent in Ahmedabad under 20000")
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
        title = st.text_input("Property title", placeholder="Modern 2 BHK Apartment")
        city = st.text_input("City", placeholder="Ahmedabad")
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
        amenities = st.text_input("Amenities", placeholder="Parking, lift, security")
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
    st.write("Describe your requirements in everyday language. The assistant uses your local Ollama model and property database.")
    ai_message = st.text_area("What kind of property are you looking for?", placeholder="I need a 2 BHK apartment for rent in Ahmedabad under ₹20,000", height=100)
    if st.button("Find properties with AI", type="primary"):
        if not ai_message.strip():
            st.warning("Please enter your property requirements first.")
        else:
            with st.spinner("Understanding your request..."):
                try:
                    extracted = extract_property_filters(ai_message.strip())
                    if not isinstance(extracted, dict):
                        extracted = {}
                    ai_query = build_search_query(extracted, ai_message.strip())
                    if not isinstance(ai_query, str) or not ai_query.strip():
                        ai_query = ai_message.strip()
                    st.write("**Detected requirements:**", format_filters(extracted))
                    ai_results, _ = search_properties(properties, ai_query)
                    if ai_results.empty:
                        st.info("No exact matches found. Try a wider budget or different location.")
                    else:
                        st.success(f"Found {len(ai_results)} matching property/properties.")
                        for _, row in ai_results.iterrows():
                            show_property_card(row.to_dict())
                except Exception as exc:
                    st.error(f"The local AI assistant could not complete the search: {exc}")
                    st.caption("Check that Ollama is running and that services/local_ai.py uses the installed model name.")

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

