"""District lookup by place and by coordinates."""
import pytest

import db


def _skip_without_directory():
    try:
        with db.engine.connect() as conn:
            count = conn.exec_driver_sql("SELECT COUNT(*) FROM districts").scalar()
    except Exception as exc:
        pytest.skip(f"MySQL is not available: {exc}")
    if not count:
        pytest.skip("district directory is not loaded")


def test_city_search_finds_the_local_district():
    _skip_without_directory()
    rows = db.search_districts("United States", "PA", "Philadelphia")
    names = [row["name"] for row in rows]
    assert "Philadelphia City SD" in names


def test_nearby_search_uses_coordinates_only_for_the_list():
    _skip_without_directory()
    rows = db.nearby_districts(39.9526, -75.1652)
    assert rows
    assert rows[0]["state"] == "Pennsylvania"
    assert "Philadelphia City SD" in [row["name"] for row in rows]
    assert "latitude" not in rows[0]
