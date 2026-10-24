import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from services.local_ai import (
    extract_property_filters,
    build_search_query,
)

from services.property_search import search_properties


# =========================================================
# CONFIGURATION
# =========================================================

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "property_agent.db"
ASSETS_DIR = BASE_DIR / "assets"
PROPERTY_IMAGES_DIR = ASSETS_DIR / "properties"

# Your existing image files
PROPERTY_IMAGE_MAP = {
    1: PROPERTY_IMAGES_DIR / "apartment.jpg",
    2: PROPERTY_IMAGES_DIR / "villa.jpg",
    3: PROPERTY_IMAGES_DIR / "flat.jpg",
}

st.set_page_config(
    page_title="Property Agent Bot",
    page_icon="🏠",
    layout="wide",
)


# =========================================================
# DATABASE FUNCTIONS
# =========================================================

def get_connection():
    return sqlite3.connect(DB_PATH)


def get_properties():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT * FROM properties ORDER BY id DESC",
            conn,
        )


def add_property(data):
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO properties (
                title, city, property_type, listing_type,
                price, bedrooms, area_sqft, description,
                image_path, amenities
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )


def save_image_paths():
    """Assign the supplied images to the three existing listings."""
    with get_connection() as conn:
        for property_id, image_path in PROPERTY_IMAGE_MAP.items():
            if image_path.is_file():
                conn.execute(
                    """
                    UPDATE properties
                    SET image_path = ?
                    WHERE id = ?
                    """,
                    (
                        str(image_path.relative_to(BASE_DIR)),
                        property_id,
                    ),
                )


# =========================================================
# IMAGE FUNCTIONS
# =========================================================

def resolve_image_path(image_path, property_id=None):
    """Find a property's local image."""
    candidates = []

    if image_path and pd.notna(image_path):
        path = Path(str(image_path))

        if path.is_absolute():
            candidates.append(path)
        else:
            candidates.append(BASE_DIR / path)
            candidates.append(PROPERTY_IMAGES_DIR / path.name)

    # Use the supplied images as fallbacks for the existing listings.
    if property_id is not None:
        fallback = PROPERTY_IMAGE_MAP.get(int(property_id))
        if fallback:
            candidates.append(fallback)

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    return None


def display_property_image(image_path, property_id, caption=None):
    resolved = resolve_image_path(image_path, property_id)

    if resolved:
        st.image(
            str(resolved),
            caption=caption,
            use_container_width=True,
        )
    else:
        st.info("🏠 No image available for this property.")


# =========================================================
# INITIAL CHECKS
# =========================================================

if not DB_PATH.exists():
    st.error(
        "Database not found. Check that data/property_agent.db exists."
    )
    st.stop()

# Save image paths for existing sample listings.
# This only updates image_path and does not recreate listings.
save_image_paths()

properties = get_properties()

if properties.empty:
    st.title("🏠 Property Agent Bot")
    st.info("No property listings yet. Add your first property.")
    st.stop()

required_columns = {"image_path", "amenities"}
missing_columns = required_columns - set(properties.columns)

if missing_columns:
    st.error(
        "Phase 3 database columns are missing: "
        + ", ".join(sorted(missing_columns))
        + ". Run migrate_phase3.py first."
    )
    st.stop()


# =========================================================
# HEADER AND DASHBOARD
# =========================================================

st.title("🏠 Property Agent Bot")
st.caption(
    "Search properties, explore photos, view details, and manage listings."
)

col1, col2, col3 = st.columns(3)

col1.metric("Total properties", len(properties))
col2.metric(
    "For sale",
    int((properties["listing_type"] == "Sale").sum()),
)
col3.metric(
    "For rent",
    int((properties["listing_type"] == "Rent").sum()),
)

st.divider()

search_tab, add_tab, tab_ai = st.tabs(
    ["Search Properties", "Add Property", "AI Property Assistant"]
)

# =========================================================
# SEARCH TAB AND PROPERTY DETAILS
# =========================================================

with search_tab:

    if "selected_property_id" not in st.session_state:
        st.session_state.selected_property_id = None

    selected_id = st.session_state.selected_property_id

    # ---------------- PROPERTY DETAIL VIEW ----------------

    if selected_id is not None:
        selected_rows = properties[
            properties["id"] == selected_id
        ]

        if selected_rows.empty:
            st.session_state.selected_property_id = None
            st.rerun()

        prop = selected_rows.iloc[0]

        if st.button("← Back to search"):
            st.session_state.selected_property_id = None
            st.rerun()

        st.header(prop["title"])

        image_col, detail_col = st.columns([1.2, 1])

        with image_col:
            display_property_image(
                prop.get("image_path"),
                prop["id"],
                prop["title"],
            )

        with detail_col:
            st.subheader(f'₹{prop["price"]:,.0f}')

            if prop["listing_type"] == "Rent":
                st.caption("Rent per month")

            st.write(f'📍 **City:** {prop["city"]}')
            st.write(f'🏘️ **Property type:** {prop["property_type"]}')
            st.write(f'📋 **Listing:** {prop["listing_type"]}')
            st.write(f'🛏️ **Bedrooms:** {prop["bedrooms"]}')
            st.write(f'📐 **Area:** {prop["area_sqft"]:,.0f} sq ft')
            st.write(f'🆔 **Listing ID:** {prop["id"]}')

        st.divider()

        st.subheader("Property Description")
        description = prop.get("description")

        if pd.notna(description) and str(description).strip():
            st.write(description)
        else:
            st.write("No description provided.")

        st.subheader("Amenities")
        amenities = prop.get("amenities")

        if pd.notna(amenities) and str(amenities).strip():
            amenity_list = [
                item.strip()
                for item in str(amenities).split(",")
                if item.strip()
            ]

            if amenity_list:
                st.write(" · ".join(amenity_list))
            else:
                st.write("No amenities listed.")
        else:
            st.write("No amenities listed.")

    # ---------------- SEARCH RESULTS ----------------

    else:
        st.subheader("Find Your Property")

        natural_query = st.text_input(
            "Describe the property you want",
            placeholder=(
                "e.g. 2 BHK apartment for rent in Ahmedabad under 20000"
            ),
        )

        smart_results, detected_filters = search_properties(
            properties,
            natural_query,
        )

        if natural_query.strip():
            st.caption("Requirements detected")
            st.json(detected_filters)

        # Sidebar filters
        with st.sidebar:
            st.header("Search Filters")

            cities = ["All"] + sorted(
                properties["city"].dropna().unique().tolist()
            )
            city = st.selectbox("City", cities)

            listing_type = st.selectbox(
                "Listing Type",
                ["All", "Sale", "Rent"],
            )

            types = ["All"] + sorted(
                properties["property_type"].dropna().unique().tolist()
            )
            property_type = st.selectbox("Property Type", types)

            min_price = int(properties["price"].min())
            max_price = int(properties["price"].max())

            budget_min, budget_max = st.slider(
                "Budget Range (₹)",
                min_value=0,
                max_value=max(100000, max_price),
                value=(min_price, max_price),
                step=1000,
            )

            min_bedrooms = st.selectbox(
                "Minimum Bedrooms",
                [0, 1, 2, 3, 4],
            )

        # Apply both smart search and sidebar filters.
        results = smart_results.copy()

        if city != "All":
            results = results[results["city"] == city]

        if listing_type != "All":
            results = results[
                results["listing_type"] == listing_type
            ]

        if property_type != "All":
            results = results[
                results["property_type"] == property_type
            ]

        results = results[
            results["price"].between(budget_min, budget_max)
        ]

        results = results[
            results["bedrooms"] >= min_bedrooms
        ]

        st.write(f"Found **{len(results)}** matching properties")

        if results.empty:
            st.info(
                "No properties match your requirements. "
                "Try a different search or adjust your filters."
            )

        else:
            for _, prop in results.iterrows():
                with st.container(border=True):
                    image_col, info_col = st.columns([1, 2])

                    with image_col:
                        display_property_image(
                            prop.get("image_path"),
                            prop["id"],
                            prop["title"],
                        )

                    with info_col:
                        st.subheader(prop["title"])

                        st.write(
                            f'📍 {prop["city"]} · '
                            f'{prop["property_type"]} · '
                            f'{prop["listing_type"]}'
                        )

                        price_text = f'₹{prop["price"]:,.0f}'

                        if prop["listing_type"] == "Rent":
                            price_text += " / month"

                        st.markdown(f"### {price_text}")

                        st.write(
                            f'🛏️ {prop["bedrooms"]} bedrooms · '
                            f'📐 {prop["area_sqft"]:,.0f} sq ft'
                        )

                        description = prop.get("description")

                        if (
                            pd.notna(description)
                            and str(description).strip()
                        ):
                            st.write(str(description))

                        if st.button(
                            "View Details",
                            key=f"view_details_{int(prop['id'])}",
                        ):
                            st.session_state.selected_property_id = int(
                                prop["id"]
                            )
                            st.rerun()


# =========================================================
# ADD PROPERTY TAB
# =========================================================

with add_tab:
    st.subheader("Add a Property Listing")

    with st.form("add_property_form"):
        title = st.text_input("Property Title")
        city = st.text_input("City")

        property_type = st.selectbox(
            "Property Type",
            ["Apartment", "House", "Villa", "Plot", "Commercial"],
        )

        listing_type = st.selectbox(
            "Listing Type",
            ["Sale", "Rent"],
        )

        price = st.number_input(
            "Price in Rupees",
            min_value=1,
            value=100000,
            step=1000,
        )

        bedrooms = st.number_input(
            "Bedrooms",
            min_value=0,
            max_value=20,
            value=2,
        )

        area = st.number_input(
            "Area in Square Feet",
            min_value=1,
            value=1000,
            step=50,
        )

        description = st.text_area("Description")

        amenities = st.text_input(
            "Amenities (comma-separated)",
            placeholder="Parking, Lift, Security, Garden",
        )

        image_filename = st.text_input(
            "Image Filename (optional)",
            placeholder="apartment.jpg",
            help=(
                "Put the image inside assets/properties or assets. "
                "Enter the filename, not the full Windows path."
            ),
        )

        submitted = st.form_submit_button("Save Property")

        if submitted:
            if not title.strip() or not city.strip():
                st.error("Please enter both a title and a city.")

            else:
                image_path = None

                if image_filename.strip():
                    filename = Path(image_filename.strip()).name

                    possible_paths = [
                        PROPERTY_IMAGES_DIR / filename,
                        ASSETS_DIR / filename,
                    ]

                    matching_path = next(
                        (
                            path for path in possible_paths
                            if path.is_file()
                        ),
                        None,
                    )

                    if matching_path is None:
                        st.error(
                            "Image not found. Check the filename. "
                            "The file must be inside assets or "
                            "assets/properties."
                        )
                        st.stop()

                    image_path = str(
                        matching_path.relative_to(BASE_DIR)
                    )

                add_property(
                    (
                        title.strip(),
                        city.strip(),
                        property_type,
                        listing_type,
                        float(price),
                        int(bedrooms),
                        float(area),
                        description.strip(),
                        image_path,
                        amenities.strip(),
                    )
                )

                st.success("Property saved successfully!")
                st.rerun()


# =========================================================
# FOOTER
# =========================================================

st.divider()
st.caption("Property Agent Bot · Phase 3 · Local development")




# ============================================================
# PHASE 4: LOCAL AI PROPERTY ASSISTANT
# ============================================================

with tab_ai:
    st.subheader("AI Property Assistant")
    st.caption(
        "Describe your property requirements in simple language. "
        "The assistant uses local Ollama AI and your existing "
        "property search service."
    )

    # Initialize conversation history
    if "phase4_messages" not in st.session_state:
        st.session_state["phase4_messages"] = []

    # Display existing conversation
    for message in st.session_state["phase4_messages"]:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

            if message.get("filters"):
                with st.expander("View extracted filters"):
                    st.json(message["filters"])

            if "results" in message:
                results = message["results"]

                if results:
                    st.write(
                        f"Found {len(results)} matching properties."
                    )

                    for item in results:
                        st.markdown(
                            f"### {item.get('title', 'Property listing')}"
                        )

                        st.write(
                            f"**City:** {item.get('city', 'Not specified')}"
                        )
                        st.write(
                            f"**Price:** ₹{item.get('price', 'Not specified')}"
                        )
                        st.write(
                            f"**Property type:** "
                            f"{item.get('property_type', 'Not specified')}"
                        )
                        st.write(
                            f"**Listing type:** "
                            f"{item.get('listing_type', 'Not specified')}"
                        )
                        st.write(
                            f"**Bedrooms:** "
                            f"{item.get('bedrooms', 'Not specified')}"
                        )

                        description = item.get("description")

			if description is not None and not isinstance(description, pd.Series):
    				if not pd.isna(description) and str(description).strip():
        				st.write(str(description))

                        image_path = item.get("image_path")
                        if image_path:
                            from pathlib import Path

                            image_file = Path(str(image_path))

                            if not image_file.is_absolute():
                                image_file = (
                                    Path(__file__).resolve().parent
                                    / image_file
                                )

                            if image_file.is_file():
                                st.image(str(image_file), width=300)

                        st.divider()

                else:
                    st.info(
                        "No matching properties were found in your "
                        "database. Try changing the city, budget, "
                        "or property requirements."
                    )

    # Chat input
    user_message = st.chat_input(
        "Example: 2 BHK apartment for rent in Ahmedabad under 20000"
    )

    if user_message:
        st.session_state["phase4_messages"].append(
            {
                "role": "user",
                "content": user_message,
            }
        )

        try:
            with st.spinner("Understanding your requirements with Ollama..."):
                filters = extract_property_filters(user_message)
                search_query = build_search_query(
                    filters,
                    user_message,
                )

            with st.spinner("Searching your property database..."):
                matches = search_properties(properties, search_query)

            # Support either a pandas DataFrame or a list of dictionaries.
            if matches is None:
                result_records = []
            elif hasattr(matches, "empty"):
                result_records = (
                    []
                    if matches.empty
                    else matches.to_dict(orient="records")
                )
            else:
                result_records = list(matches)

            response_text = (
                f"I found {len(result_records)} matching property "
                "listing(s) in your database."
                if result_records
                else
                "I couldn't find a matching property in your current "
                "database. Try changing your requirements."
            )

            st.session_state["phase4_messages"].append(
                {
                    "role": "assistant",
                    "content": response_text,
                    "filters": filters,
                    "results": result_records,
                }
            )

        except Exception as exc:
            st.session_state["phase4_messages"].append(
                {
                    "role": "assistant",
                    "content": (
                        f"I couldn't complete that search: {exc}\n\n"
                        "Check that Ollama is running and that the "
                        "property search service is available."
                    ),
                }
            )

        st.rerun()

    if st.button("Clear chat history", key="clear_phase4_chat"):
        st.session_state["phase4_messages"] = []
        st.rerun()
