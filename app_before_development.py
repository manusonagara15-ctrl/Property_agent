
# import streamlit as st
# import pandas as pd

# st.set_page_config(
#     page_title="AI Property Agent",
#     page_icon="🏠",
#     layout="wide"
# )

# # DEMO DATA ONLY — these are fictional properties.
# properties = [
#     {
#         "id": "P001",
#         "city": "Ahmedabad",
#         "locality": "Satellite",
#         "type": "Apartment",
#         "bedrooms": 2,
#         "price_lakh": 45,
#         "area_sqft": 1050,
#         "amenities": "Parking, Lift, Security",
#         "status": "Sample listing"
#     },
#     {
#         "id": "P002",
#         "city": "Ahmedabad",
#         "locality": "Bopal",
#         "type": "Apartment",
#         "bedrooms": 3,
#         "price_lakh": 65,
#         "area_sqft": 1450,
#         "amenities": "Parking, Garden, Lift",
#         "status": "Sample listing"
#     },
#     {
#         "id": "P003",
#         "city": "Rajkot",
#         "locality": "Kalawad Road",
#         "type": "Villa",
#         "bedrooms": 3,
#         "price_lakh": 80,
#         "area_sqft": 1800,
#         "amenities": "Garden, Parking, Terrace",
#         "status": "Sample listing"
#     },
#     {
#         "id": "P004",
#         "city": "Surat",
#         "locality": "Adajan",
#         "type": "Apartment",
#         "bedrooms": 2,
#         "price_lakh": 38,
#         "area_sqft": 950,
#         "amenities": "Lift, Security",
#         "status": "Sample listing"
#     },
#     {
#         "id": "P005",
#         "city": "Ahmedabad",
#         "locality": "Gota",
#         "type": "Apartment",
#         "bedrooms": 1,
#         "price_lakh": 28,
#         "area_sqft": 650,
#         "amenities": "Parking, Lift",
#         "status": "Sample listing"
#     }
# ]

# df = pd.DataFrame(properties)

# st.title("🏠 AI Property Agent")
# st.caption(
#     "Find, filter, and compare properties in India."
# )

# st.info(
#     "Demo mode: all displayed property records are fictional. "
#     "They are not verified real-world listings."
# )

# # Sidebar filters
# st.sidebar.header("Property Search")

# city = st.sidebar.selectbox(
#     "City",
#     ["All"] + sorted(df["city"].unique().tolist())
# )

# max_budget = st.sidebar.number_input(
#     "Maximum budget (₹ lakh)",
#     min_value=1,
#     max_value=10000,
#     value=50
# )

# bedrooms = st.sidebar.selectbox(
#     "Bedrooms",
#     ["Any", 1, 2, 3, 4, 5]
# )

# property_type = st.sidebar.selectbox(
#     "Property type",
#     ["Any"] + sorted(df["type"].unique().tolist())
# )

# # Apply filters
# results = df[df["price_lakh"] <= max_budget]

# if city != "All":
#     results = results[results["city"] == city]

# if bedrooms != "Any":
#     results = results[results["bedrooms"] == bedrooms]

# if property_type != "Any":
#     results = results[results["type"] == property_type]

# st.subheader("Matching Properties")
# st.write(f"Found {len(results)} matching sample properties.")

# if results.empty:
#     st.warning(
#         "No matching properties in the demo dataset. "
#         "Try changing your filters."
#     )

# for _, prop in results.iterrows():
#     with st.container(border=True):
#         st.subheader(
#             f'{prop["bedrooms"]} BHK {prop["type"]}'
#         )

#         st.write(
#             f'📍 {prop["locality"]}, {prop["city"]}'
#         )

#         col1, col2, col3 = st.columns(3)

#         col1.metric(
#             "Price",
#             f'₹{prop["price_lakh"]} lakh'
#         )
#         col2.metric(
#             "Area",
#             f'{prop["area_sqft"]:,} sq. ft.'
#         )
#         col3.metric(
#             "Bedrooms",
#             prop["bedrooms"]
#         )

#         st.write(f'Amenities: {prop["amenities"]}')
#         st.caption(
#             f'ID: {prop["id"]} | {prop["status"]}'
#         )

# st.divider()

# # Basic conversational search
# st.subheader("Ask the Property Assistant")

# question = st.text_input(
#     "Describe the property you want",
#     placeholder="Example: Find a 2 BHK home in Ahmedabad under 50 lakh"
# )

# if st.button("Search using my request"):
#     if not question.strip():
#         st.warning("Please enter your property requirements.")
#     else:
#         q = question.lower()

#         # Extract a few common requirements.
#         requested_city = next(
#             (
#                 c for c in df["city"].unique()
#                 if c.lower() in q
#             ),
#             None
#         )

#         requested_bedrooms = next(
#             (
#                 n for n in [1, 2, 3, 4, 5]
#                 if f"{n} bhk" in q or f"{n} bedroom" in q
#             ),
#             None
#         )

#         # Read budget expressed in lakh.
#         budget = None
#         if "lakh" in q or "lac" in q:
#             import re
#             match = re.search(
#                 r"(\d+(?:\.\d+)?)\s*(?:lakh|lac)",
#                 q
#             )
#             if match:
#                 budget = float(match.group(1))

#         matches = df.copy()

#         if requested_city:
#             matches = matches[
#                 matches["city"] == requested_city
#             ]

#         if requested_bedrooms:
#             matches = matches[
#                 matches["bedrooms"] == requested_bedrooms
#             ]

#         if budget is not None:
#             matches = matches[
#                 matches["price_lakh"] <= budget
#             ]

#         st.markdown("### Search results")

#         if matches.empty:
#             st.info(
#                 "No matching sample properties found. "
#                 "Try a different city, budget, or bedroom count."
#             )
#         else:
#             st.success(
#                 f"Found {len(matches)} matching sample properties."
#             )

#             st.dataframe(
#                 matches[
#                     [
#                         "id", "city", "locality", "type",
#                         "bedrooms", "price_lakh",
#                         "area_sqft", "amenities"
#                     ]
#                 ],
#                 hide_index=True,
#                 use_container_width=True
#             )
import streamlit as st

st.set_page_config(
    page_title="Property Agent Bot",
    page_icon="🏠",
    layout="wide",
)

st.title("🏠 AI Property Agent Bot")
st.success("Streamlit is working correctly!")

st.write("If you can see this message, the basic app is rendering.")

city = st.selectbox(
    "Choose a city",
    ["Ahmedabad", "Surat", "Rajkot"],
)

st.write("Selected city:", city)