"""Phase 10 tests for matching, notifications, follow-up tasks and pipeline."""
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import backend


class Phase10Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "phase10_test.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""CREATE TABLE properties (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, city TEXT NOT NULL,
                property_type TEXT NOT NULL, listing_type TEXT NOT NULL,
                price REAL NOT NULL, bedrooms INTEGER NOT NULL DEFAULT 1,
                area_sqft REAL DEFAULT 0, description TEXT DEFAULT '',
                image_path TEXT, amenities TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""CREATE TABLE enquiries (
                id INTEGER PRIMARY KEY, property_id INTEGER, property_title TEXT NOT NULL,
                property_city TEXT NOT NULL DEFAULT '', customer_name TEXT NOT NULL,
                phone TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', message TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'Other', preferred_budget REAL,
                move_in_timeline TEXT NOT NULL DEFAULT '', priority TEXT NOT NULL DEFAULT 'Medium',
                qualification_score INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'New',
                follow_up_date TEXT, agent_notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            conn.execute("""INSERT INTO properties
                (id,title,city,property_type,listing_type,price,bedrooms,area_sqft,description,amenities)
                VALUES (1,'Modern 2 BHK Apartment','Ahmedabad','Apartment','Rent',18000,2,1100,
                        'Bright apartment near shops','parking,lift')""")
            conn.execute("""INSERT INTO enquiries
                (id,property_title,property_city,customer_name,phone,status,priority,created_at,updated_at)
                VALUES (1,'Rental Apartment','Ahmedabad','Test Customer','9999999999','New','High',datetime('now'),datetime('now'))""")
        self.db_patch = patch.object(backend, "DB_PATH", self.db_path)
        self.db_patch.start()
        backend.ensure_phase9_tables()
        backend.ensure_phase10_tables()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _save_preferences(self):
        backend.save_customer_preferences(1, {
            "preferred_city": "Ahmedabad",
            "preferred_property_type": "Apartment",
            "preferred_listing_type": "Rent",
            "bedrooms": 2,
            "max_budget": 20000,
            "min_area_sqft": 900,
            "max_area_sqft": 1300,
            "must_have_amenities": "parking,lift",
        })

    def test_phase10_migration_preserves_existing_leads(self):
        leads = backend.get_enquiries()
        self.assertEqual(len(leads), 1)
        self.assertIn("pipeline_stage", leads.columns)
        self.assertEqual(leads.iloc[0]["pipeline_stage"], "Enquiry")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM properties").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM enquiries").fetchone()[0], 1)

    def test_scoring_returns_exact_100_percent(self):
        self._save_preferences()
        property_row = backend.get_properties().iloc[0].to_dict()
        result = backend.score_property_for_customer(property_row, backend.get_customer_preferences(1))
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["level"], "Excellent")
        self.assertTrue(result["is_exact"])

    def test_new_property_match_creates_deduplicated_notification(self):
        self._save_preferences()
        properties = backend.get_properties()
        first = backend.match_property_to_customers(1, properties_frame=properties)
        second = backend.match_property_to_customers(1, properties_frame=properties)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertEqual(backend.get_property_matches(1).shape[0], 1)
        self.assertEqual(backend.get_unread_notification_count(), 1)

    def test_notification_read_and_dismiss(self):
        self.assertTrue(backend.create_notification("Test alert", "Details", dedupe_key="test-one"))
        self.assertFalse(backend.create_notification("Test alert", "Details", dedupe_key="test-one"))
        notification_id = int(backend.get_notifications().iloc[0]["id"])
        self.assertTrue(backend.mark_notification_read(notification_id))
        self.assertEqual(backend.get_unread_notification_count(), 0)
        self.assertTrue(backend.dismiss_notification(notification_id))
        self.assertTrue(backend.get_notifications().empty)

    def test_follow_up_task_lifecycle(self):
        task_id = backend.create_follow_up_task(1, "Call customer", date.today() + timedelta(days=1), "Confirm visit", "High")
        tasks = backend.get_follow_up_tasks()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.iloc[0]["title"], "Call customer")
        self.assertTrue(backend.update_follow_up_task(task_id, status="Completed"))
        completed = backend.get_follow_up_tasks(status="Completed")
        self.assertEqual(completed.iloc[0]["status"], "Completed")
        self.assertFalse(backend.get_follow_up_tasks(status="Open").shape[0])

    def test_pipeline_stage_and_activity_history(self):
        backend.update_lead_pipeline_stage(1, "Visit Scheduled", "Customer confirmed the visit")
        lead = backend.get_enquiries().iloc[0]
        self.assertEqual(lead["pipeline_stage"], "Visit Scheduled")
        history = backend.get_lead_activities(1)
        self.assertEqual(len(history), 1)
        self.assertIn("Visit Scheduled", history.iloc[0]["details"])

    def test_invalid_pipeline_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            backend.update_lead_pipeline_stage(1, "Not A Real Stage")

    def test_analytics_counts_created_records(self):
        self._save_preferences()
        backend.match_property_to_customers(1)
        backend.create_follow_up_task(1, "Call customer", date.today() + timedelta(days=1))
        result = backend.get_phase10_analytics()
        self.assertEqual(result["leads"], 1)
        self.assertEqual(result["matches"], 1)
        self.assertEqual(result["open_tasks"], 1)
        self.assertEqual(result["unread_notifications"], 1)


if __name__ == "__main__":
    unittest.main()
