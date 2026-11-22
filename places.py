"""US state and city dropdowns built from data/us_districts.csv.

Country is fixed to the United States for signup/account. Cities are the
unique city names that appear for each state in the NCES district list.
"""
from __future__ import annotations

import csv
import os
from functools import lru_cache

CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "us_districts.csv")

COUNTRY = "United States"

# Full US state list so every state is selectable even if the CSV is thin.
US_STATES = (
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("DC", "District of Columbia"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
)

_CODE_TO_NAME = {code: name for code, name in US_STATES}
_NAME_TO_CODE = {name.lower(): code for code, name in US_STATES}


def list_states():
    return [{"code": code, "name": name} for code, name in US_STATES]


@lru_cache(maxsize=1)
def _cities_by_state_code():
    by_code = {code: set() for code, _ in US_STATES}
    if not os.path.isfile(CSV_PATH):
        return {code: [] for code in by_code}
    with open(CSV_PATH, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            code = (row.get("state_code") or "").strip().upper()
            city = (row.get("city") or "").strip()
            if code in by_code and city:
                by_code[code].add(city)
    return {code: sorted(cities, key=str.casefold) for code, cities in by_code.items()}


def normalize_state(raw):
    """Return (state_name, state_code) or (None, None) if unknown."""
    value = (raw or "").strip()
    if not value:
        return None, None
    if len(value) == 2:
        code = value.upper()
        name = _CODE_TO_NAME.get(code)
        return (name, code) if name else (None, None)
    code = _NAME_TO_CODE.get(value.lower())
    if code:
        return _CODE_TO_NAME[code], code
    return None, None


def list_cities(state):
    name, code = normalize_state(state)
    if not code:
        return []
    return list(_cities_by_state_code().get(code, []))


def state_matches(teacher_state, pilot_state, pilot_state_code=None):
    given_name, given_code = normalize_state(teacher_state)
    if not given_name:
        return False
    allowed = {
        (pilot_state or "").strip().lower(),
        (pilot_state_code or "").strip().lower(),
    }
    allowed.discard("")
    return given_name.lower() in allowed or (given_code or "").lower() in allowed
