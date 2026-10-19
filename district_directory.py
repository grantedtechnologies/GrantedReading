"""Public school-district lookup loaded from data/us_districts.csv.

The file is the NCES 2024-25 list of regular public school districts: name,
city, state, and office coordinates. State agencies, charter networks, and
individual schools are left out. Country is United States for every row.
Other countries stay typeable; this list just has no matches for them.
"""
import csv
import os

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_districts.csv")


def _place_name(value):
    value = (value or "").strip()
    if value.isupper():
        return value.title()
    return value


def load(cursor):
    cursor.execute("DELETE FROM districts")
    statement = """
        INSERT INTO districts (country, state, stateCode, city, name, latitude, longitude)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """
    batch = []
    count = 0
    with open(CSV_PATH, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            batch.append((
                row["country"],
                _place_name(row["state"]),
                row["state_code"] or None,
                _place_name(row["city"]),
                row["name"],
                row["latitude"] or None,
                row["longitude"] or None,
            ))
            if len(batch) == 500:
                cursor.executemany(statement, batch)
                count += len(batch)
                batch = []
    if batch:
        cursor.executemany(statement, batch)
        count += len(batch)
    return count
