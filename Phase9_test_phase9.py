"""Phase 9 persistence and follow-up tests using a temporary SQLite database."""
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import backend


class Phase9Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "phase9_test.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """CREATE TABLE enquiries (
                    id INTEGER PRIMARY KEY,
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
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            conn.execute(
                "INSERT INTO enquiries (id, property_title, property_city, customer_name, phone, status, priority, follow_up_date, move_in_timeline, created_at, updated_at) VALUES (1, 'Apartment', 'Ahmedabad', 'Test Customer', '9999999999', 'New', 'High', ?, 'Immediately', ?, ?)" ,
                ((date.today()).isoformat(), f"{date.today().isoformat()} 09:00:00", f"{date.today().isoformat()} 09:00:00"),
            )
        self.db_patch = patch.object(backend, "DB_PATH", self.db_path)
        self.db_patch.start()
        backend.ensure_phase9_tables()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_saved_search_create_read_delete(self):
        backend.save_property_search("Ahmedabad Rentals", "2 BHK apartment for rent in Ahmedabad under 20000")
        rows = backend.get_saved_property_searches()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.iloc[0]["name"], "Ahmedabad Rentals")
        self.assertTrue(backend.delete_saved_property_search(int(rows.iloc[0]["id"])))
        self.assertTrue(backend.get_saved_property_searches().empty)

    def test_saved_search_names_are_unique_case_insensitively(self):
        backend.save_property_search("Weekend Homes", "villa for sale")
        with self.assertRaises(sqlite3.IntegrityError):
            backend.save_property_search("weekend homes", "house for sale")

    def test_customer_preferences_upsert(self):
        payload = {
            "preferred_city": "Ahmedabad", "preferred_property_type": "Apartment",
            "preferred_listing_type": "Rent", "bedrooms": 2, "max_budget": 20000,
            "min_area_sqft": 800, "max_area_sqft": 1500,
            "must_have_amenities": "parking", "notes": "Near public transport",
        }
        backend.save_customer_preferences(1, payload)
        first = backend.get_customer_preferences(1)
        self.assertEqual(first["preferred_city"], "Ahmedabad")
        self.assertEqual(first["bedrooms"], 2)
        payload["preferred_city"] = "Surat"
        backend.save_customer_preferences(1, payload)
        updated = backend.get_customer_preferences(1)
        self.assertEqual(updated["preferred_city"], "Surat")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM customer_preferences").fetchone()[0], 1)

    def test_customer_preferences_validate_area_range(self):
        with self.assertRaises(ValueError):
            backend.save_customer_preferences(1, {"min_area_sqft": 1500, "max_area_sqft": 900})

    def test_followup_suggestions_exclude_closed_leads(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO enquiries (id, property_title, customer_name, phone, status, priority, created_at, updated_at) VALUES (2, 'Closed apartment', 'Closed Customer', '8888888888', 'Won', 'Medium', ?, ?)", (f"{date.today().isoformat()} 09:00:00", f"{date.today().isoformat()} 09:00:00"))
        suggestions = backend.get_smart_followup_suggestions(today=date.today())
        self.assertIn(1, suggestions["id"].tolist())
        self.assertNotIn(2, suggestions["id"].tolist())
        self.assertIn("Follow up today", suggestions.iloc[0]["suggested_action"])

    def test_preference_matches_respect_area_and_amenity(self):
        properties = pd.DataFrame([
            {"id": 1, "title": "Parking apartment", "city": "Ahmedabad", "property_type": "Apartment", "listing_type": "Rent", "price": 18000, "bedrooms": 2, "area_sqft": 1100, "amenities": "parking,lift", "description": "Near station"},
            {"id": 2, "title": "Apartment without parking", "city": "Ahmedabad", "property_type": "Apartment", "listing_type": "Rent", "price": 15000, "bedrooms": 2, "area_sqft": 700, "amenities": "lift", "description": "Affordable"},
        ])
        matches, exact = backend.get_customer_preference_matches(properties, {
            "preferred_city": "Ahmedabad", "preferred_property_type": "Apartment",
            "preferred_listing_type": "Rent", "bedrooms": 2, "max_budget": 20000,
            "min_area_sqft": 900, "must_have_amenities": "parking",
        }, limit=5)
        self.assertEqual([item["id"] for item in matches], [1])
        self.assertTrue(exact)


if __name__ == "__main__":
    unittest.main()
