import streamlit as st
from services.property_search import search_properties
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "property_agent.db"

st.set_page_config(
    page_title="Property Agent Bot",
    page_icon="🏠",
    layout="wide",
)

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
                price, bedrooms, area_sqft, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            data,
        )

st.title("🏠 Property Agent Bot")
st.caption("Search properties, compare budgets, and add listings.")

if not DB_PATH.exists():
    st.error("Database not found. Run: python init_db.py")
    st.stop()

properties = get_properties()

# Dashboard
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

search_tab, add_tab = st.tabs(
    ["🔎 Search properties", "➕ Add property"]
)

with search_tab:
    st.subheader("Find your property")

    natural_query = st.text_input(
        "Describe the property you want",
        placeholder="e.g. 2 BHK apartment for rent in Ahmedabad under 20000",
    )

    smart_results, detected_filters = search_properties(
        properties, natural_query
    )

    if natural_query.strip():
        st.caption("Requirements detected from your search:")
        st.write(detected_filters)

    with st.sidebar:
        st.header("Search filters")

        cities = ["All"] + sorted(
            properties["city"].dropna().unique().tolist()
        )
        city = st.selectbox("City", cities)

        listing_type = st.selectbox(
            "Listing type", ["All", "Sale", "Rent"]
        )

        types = ["All"] + sorted(
            properties["property_type"].dropna().unique().tolist()
        )
        property_type = st.selectbox("Property type", types)

        min_price = int(properties["price"].min())
        max_price = int(properties["price"].max())

        budget_min, budget_max = st.slider(
            "Budget range (₹)",
            min_value=0,
            max_value=max(100000, max_price),
            value=(min_price, max_price),
            step=1000,
        )

        min_bedrooms = st.selectbox(
            "Minimum bedrooms", [0, 1, 2, 3, 4]
        )

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

    results = results[results["bedrooms"] >= min_bedrooms]

    st.write(f"Found **{len(results)}** matching properties")

    if results.empty:
        st.info("No properties match your filters. Try a wider budget.")
    else:
        for _, prop in results.iterrows():
            with st.container(border=True):
                st.subheader(prop["title"])
                st.write(
                    f'📍 {prop["city"]} · '
                    f'{prop["property_type"]} · '
                    f'{prop["listing_type"]}'
                )
                st.markdown(
                    f'### ₹{prop["price"]:,.0f}'
                    + (
                        " / month"
                        if prop["listing_type"] == "Rent"
                        else ""
                    )
                )
                st.write(
                    f'🛏️ {prop["bedrooms"]} bedrooms · '
                    f'📐 {prop["area_sqft"]:,.0f} sq ft'
                )
                st.write(prop["description"] or "No description provided.")
                st.caption(f'Listing ID: {prop["id"]}')

with add_tab:
    st.subheader("Add a property listing")

    with st.form("add_property_form"):
        title = st.text_input("Property title")
        city = st.text_input("City")
        property_type = st.selectbox(
            "Property type",
            ["Apartment", "House", "Villa", "Plot", "Commercial"],
        )
        listing_type = st.selectbox(
            "Listing type", ["Sale", "Rent"]
        )
        price = st.number_input(
            "Price in rupees", min_value=1, value=100000
        )
        bedrooms = st.number_input(
            "Bedrooms", min_value=0, max_value=20, value=2
        )
        area = st.number_input(
            "Area in square feet", min_value=1, value=1000
        )
        description = st.text_area("Description")

        submitted = st.form_submit_button("Save property")

        if submitted:
            if not title.strip() or not city.strip():
                st.error("Please enter both a title and a city.")
            else:
                add_property((
                    title.strip(),
                    city.strip(),
                    property_type,
                    listing_type,
                    price,
                    bedrooms,
                    area,
                    description.strip(),
                ))
                st.success("Property saved successfully!")
                st.rerun()

st.divider()
st.caption("Property Agent Bot · Local development prototype")
