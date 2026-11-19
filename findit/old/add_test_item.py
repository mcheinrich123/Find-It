import sqlite3
from pathlib import Path

BASE_DIRECTORY = Path(__file__).resolve().parent
DB_PATH = BASE_DIRECTORY / "findit.db"

connection = sqlite3.connect(DB_PATH)

# Executing the SQL Insert command safely using placeholders
connection.execute("""
    INSERT INTO items (
        item_code,
        title,
        description,
        category,
        location,
        date_found,
        status
    )
    VALUES (?, ?, ?, ?, ?, ?, ?)
""", (
    "TEST-001",
    "Black Dell Laptop",
    "Black Dell laptop with a cracked screen corner",
    "Electronics",
    "Main Library",
    "2026-10-27",
    "unclaimed"
))

connection.commit()
connection.close()

print("Test item added successfully!")

