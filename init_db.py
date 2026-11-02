import sqlite3
from contextlib import closing
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "property_agent.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)

with closing(sqlite3.connect(DB_PATH)) as conn:
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS properties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            city TEXT NOT NULL,
            property_type TEXT NOT NULL,
            listing_type TEXT NOT NULL,
            price REAL NOT NULL,
            bedrooms INTEGER NOT NULL DEFAULT 1,
            area_sqft REAL NOT NULL DEFAULT 0,
            description TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    count = conn.execute(
        "SELECT COUNT(*) FROM properties"
    ).fetchone()[0]

    if count == 0:
        sample_properties = [
            (
                "Modern 2 BHK Apartment",
                "Ahmedabad", "Apartment", "Rent",
                18000, 2, 1100,
                "Bright apartment near shops and public transport."
            ),
            (
                "Family Villa",
                "Surat", "Villa", "Sale",
                8500000, 3, 2200,
                "Spacious family villa with parking."
            ),
            (
                "Affordable 1 BHK Flat",
                "Rajkot", "Apartment", "Rent",
                9000, 1, 650,
                "Compact flat suitable for a small family."
            ),
        ]

        conn.executemany("""
            INSERT INTO properties (
                title, city, property_type, listing_type,
                price, bedrooms, area_sqft, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, sample_properties)

    conn.commit()

print(f"Database ready: {DB_PATH}")
