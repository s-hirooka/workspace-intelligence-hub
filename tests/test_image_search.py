from pathlib import Path

import pytest

from app.config import Settings
from app.services.image_search import parse_image_query, safe_image_path


def test_parse_image_query_detects_smile_and_people():
    parsed = parse_image_query("笑っている人が3人以上の画像を集めてください")
    assert parsed["smile_only"] is True
    assert parsed["min_people"] == 3
    assert parsed["exact_people"] == 0


def test_parse_image_query_exact_people():
    parsed = parse_image_query("1人だけ写っている画像")
    assert parsed["smile_only"] is False
    assert parsed["min_people"] == 0
    assert parsed["exact_people"] == 1


def test_parse_image_query_defaults():
    parsed = parse_image_query("松本さんが写っている画像")
    assert parsed["smile_only"] is False
    assert parsed["min_people"] == 0
    assert parsed["exact_people"] == 0


def test_parse_image_query_capture_year_and_month():
    parsed = parse_image_query("2026年9月に撮影した画像")
    assert parsed["capture_year"] == 2026
    assert parsed["capture_month"] == 9


def test_parse_image_query_capture_year_range_and_unknown():
    parsed = parse_image_query("2024年から2026年に撮影した画像")
    assert parsed["capture_year_from"] == 2024
    assert parsed["capture_year_to"] == 2026
    assert parsed["capture_year"] == 0
    assert parse_image_query("撮影日が分からない画像")["capture_unknown"] is True


def test_safe_image_path_accepts_only_configured_root(tmp_path: Path):
    root = tmp_path / "images"
    root.mkdir()
    image = root / "sample.jpg"
    image.write_bytes(b"test")
    settings = Settings(image_root=root)
    assert safe_image_path(settings, str(image)) == image.resolve()

    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"test")
    with pytest.raises(ValueError, match="画像フォルダ外"):
        safe_image_path(settings, str(outside))