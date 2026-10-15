import sqlite3
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


DEFAULT_DATABASE_PATH = Path(__file__).with_name("pantry.db")


def initialize_database(database_path=DEFAULT_DATABASE_PATH):
    """Create the pantry tables and insert sample data if they are missing."""
    database_path = Path(database_path)
    sample_date = date.today()

    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                quantity REAL NOT NULL CHECK (quantity >= 0),
                category TEXT NOT NULL,
                expiry_date TEXT
            );

            CREATE TABLE IF NOT EXISTS consumption_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                consumed_amount REAL NOT NULL CHECK (consumed_amount > 0),
                logged_date TEXT NOT NULL,
                FOREIGN KEY (item_id) REFERENCES items (id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS user_profile (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dietary_restrictions TEXT NOT NULL DEFAULT '',
                allergies TEXT NOT NULL DEFAULT ''
            );
            """
        )

        connection.executemany(
            """
            INSERT OR IGNORE INTO items (id, name, quantity, category, expiry_date)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (1, "Apples", 6, "Produce", (sample_date + timedelta(days=7)).isoformat()),
                (2, "Milk", 1, "Dairy", (sample_date + timedelta(days=4)).isoformat()),
                (3, "Rice", 2, "Grains", None),
            ],
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO consumption_logs
                (id, item_id, consumed_amount, logged_date)
            VALUES (?, ?, ?, ?)
            """,
            (1, 1, 1, sample_date.isoformat()),
        )
        connection.execute(
            """
            INSERT OR IGNORE INTO user_profile (id, dietary_restrictions, allergies)
            VALUES (?, ?, ?)
            """,
            (1, "Vegetarian", "Peanuts"),
        )


if __name__ == "__main__":
    initialize_database()