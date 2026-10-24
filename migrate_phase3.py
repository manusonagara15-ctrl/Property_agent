
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "property_agent.db"


def main():
    with sqlite3.connect(DB_PATH) as conn:
        columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(properties)"
            ).fetchall()
        }

        if "image_path" not in columns:
            conn.execute(
                "ALTER TABLE properties ADD COLUMN image_path TEXT"
            )
            print("Added image_path column.")

        if "amenities" not in columns:
            conn.execute(
                "ALTER TABLE properties ADD COLUMN amenities TEXT"
            )
            print("Added amenities column.")

        conn.commit()

    print("Phase 3 database migration completed.")


if __name__ == "__main__":
    main()
