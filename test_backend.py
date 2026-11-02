"""Offline unit tests for the Property Agent Bot backend.

Run from the project root: python -m unittest discover -s tests -v
Tests use sample in-memory DataFrames and do not modify the project database.
"""
import unittest
import pandas as pd

from backend import (
    build_search_query,
    parse_property_query,
    search_properties,
    get_ranked_recommendations,
)


class QueryParserTests(unittest.TestCase):
    def test_budget_and_listing_filters(self):
        filters = parse_property_query("2 BHK apartment for rent in Ahmedabad under 20000")
        self.assertEqual(filters["bedrooms"], 2)
        self.assertEqual(filters["property_type"], "Apartment")
        self.assertEqual(filters["listing_type"], "Rent")
        self.assertEqual(filters["max_price"], 20000)

    def test_budget_lakh_conversion(self):
        filters = parse_property_query("villa for sale under 50 lakh")
        self.assertEqual(filters["max_price"], 5_000_000)
        self.assertEqual(filters["property_type"], "Villa")
        self.assertEqual(filters["listing_type"], "Sale")

    def test_budget_wording(self):
        self.assertEqual(parse_property_query("flat budget 20k")["max_price"], 20_000)

    def test_build_search_query(self):
        filters = {"bedrooms": 2, "property_type": "Apartment", "listing_type": "Rent",
                   "city": "Ahmedabad", "max_price": 20000}
        self.assertEqual(build_search_query(filters, "original"),
                         "2 BHK Apartment for rent in Ahmedabad under 20000")

    def test_search_returns_only_matching_rows(self):
        sample = pd.DataFrame([
            {"id": 1, "title": "Modern 2 BHK Apartment", "city": "Ahmedabad",
             "property_type": "Apartment", "listing_type": "Rent", "price": 18000, "bedrooms": 2},
            {"id": 2, "title": "Family Villa", "city": "Surat",
             "property_type": "Villa", "listing_type": "Sale", "price": 8_500_000, "bedrooms": 3},
            {"id": 3, "title": "Affordable Flat", "city": "Ahmedabad",
             "property_type": "Apartment", "listing_type": "Rent", "price": 9000, "bedrooms": 1},
        ])
        result, filters = search_properties(sample, "2 BHK apartment for rent in Ahmedabad under 20000")
        self.assertEqual(result["id"].tolist(), [1])
        self.assertEqual(filters["bedrooms"], 2)

    def test_recommendations_return_alternatives(self):
        sample = pd.DataFrame([
            {"id": 1, "title": "Apartment", "city": "Ahmedabad", "property_type": "Apartment",
             "listing_type": "Rent", "price": 18000, "bedrooms": 2},
            {"id": 2, "title": "Villa", "city": "Surat", "property_type": "Villa",
             "listing_type": "Sale", "price": 8_500_000, "bedrooms": 3},
        ])
        records, exact = get_ranked_recommendations(sample,
            {"city": "Ahmedabad", "listing_type": "Rent", "bedrooms": 3}, limit=2)
        self.assertFalse(exact)
        self.assertTrue(records)
        self.assertIn("_recommendation_reason", records[0])


if __name__ == "__main__":
    unittest.main()
