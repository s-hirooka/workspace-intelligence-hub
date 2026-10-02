from datetime import datetime, timezone
from pathlib import Path

from app.services.image_metadata import extract_capture_metadata, parse_date_from_text


def test_parse_date_from_compact_file_name():
    assert parse_date_from_text("event_20260915_210147.jpg") == {
        "year": 2026, "month": 9, "day": 15, "raw": "20260915_210147",
    }


def test_parse_date_from_japanese_year_only():
    assert parse_date_from_text("2026年の記念事業") == {
        "year": 2026, "month": None, "day": None, "raw": "2026年",
    }


def test_extract_capture_metadata_uses_file_name_before_modified_time(tmp_path: Path):
    path = tmp_path / "seminar_2026-09-15.jpg"
    path.write_bytes(b"not an image")
    result = extract_capture_metadata(
        path, path.name, datetime(2025, 1, 2, tzinfo=timezone.utc)
    )
    assert result["source"] == "file_name"
    assert result["confidence"] == "candidate"
    assert (result["year"], result["month"], result["day"]) == (2026, 9, 15)


def test_extract_capture_metadata_falls_back_to_modified_time(tmp_path: Path):
    path = tmp_path / "seminar.jpg"
    path.write_bytes(b"not an image")
    result = extract_capture_metadata(
        path, path.name, datetime(2025, 1, 2, tzinfo=timezone.utc)
    )
    assert result["source"] == "file_modified"
    assert result["confidence"] == "reference"
    assert result["year"] == 2025