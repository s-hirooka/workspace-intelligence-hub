"""Local capture-date extraction for indexed image assets."""

import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import ImageAsset
from app.services.image_search import safe_image_path

JST = ZoneInfo("Asia/Tokyo")
EXIF_DATE_TAGS = ((36867, "DateTimeOriginal"), (36868, "DateTimeDigitized"), (306, "DateTime"))


def _valid_date(year: int, month: int = 1, day: int = 1) -> bool:
    try:
        datetime(year, month, day)
    except ValueError:
        return False
    return 1900 <= year <= datetime.now(JST).year + 1


def parse_date_from_text(value: str) -> dict | None:
    """Find a conservative date in a file or folder name."""
    patterns = (
        r"(?<!\d)(20\d{2}|19\d{2})[年._-](\d{1,2})[月._-](\d{1,2})日?(?!\d)",
        r"(?<!\d)(20\d{2}|19\d{2})(\d{2})(\d{2})(?:[_-]?\d{6})?(?!\d)",
    )
    for pattern in patterns:
        match = re.search(pattern, value)
        if not match:
            continue
        year, month, day = map(int, match.groups())
        if _valid_date(year, month, day):
            return {"year": year, "month": month, "day": day, "raw": match.group(0)}
    year_match = re.search(r"(?<!\d)(20\d{2}|19\d{2})年?(?!\d)", value)
    if year_match and _valid_date(int(year_match.group(1))):
        return {"year": int(year_match.group(1)), "month": None, "day": None,
                "raw": year_match.group(0)}
    return None


def _exif_capture(path: Path) -> dict | None:
    with Image.open(path) as image:
        exif = image.getexif()
        for tag, label in EXIF_DATE_TAGS:
            raw = exif.get(tag)
            if not raw:
                continue
            try:
                captured = datetime.strptime(str(raw).strip(), "%Y:%m:%d %H:%M:%S").replace(tzinfo=JST)
            except ValueError:
                continue
            if _valid_date(captured.year, captured.month, captured.day):
                return {"at": captured, "year": captured.year, "month": captured.month,
                        "day": captured.day, "source": "exif", "confidence": "confirmed",
                        "raw": f"{label}: {raw}"}
    return None


def extract_capture_metadata(path: Path, relative_path: str, modified_at: datetime) -> dict:
    """Prefer EXIF, then name/path hints, and finally filesystem modification time."""
    try:
        exif = _exif_capture(path)
    except Exception:
        exif = None
    if exif:
        return exif

    file_hint = parse_date_from_text(path.stem)
    folder_hint = parse_date_from_text(str(Path(relative_path).parent))
    hint = file_hint or folder_hint
    if hint:
        month, day = hint["month"], hint["day"]
        capture_at = None
        if month and day:
            capture_at = datetime(hint["year"], month, day, tzinfo=JST)
        return {"at": capture_at, **hint,
                "source": "file_name" if file_hint else "folder_name", "confidence": "candidate"}

    reference = modified_at.astimezone(JST) if modified_at.tzinfo else modified_at.replace(tzinfo=JST)
    return {"at": reference, "year": reference.year, "month": reference.month, "day": reference.day,
            "source": "file_modified", "confidence": "reference", "raw": reference.isoformat()}


def refresh_capture_metadata(db: Session, settings: Settings, force: bool = False) -> dict:
    rows = db.scalars(select(ImageAsset).order_by(ImageAsset.file_path)).all()
    result = {"total": len(rows), "updated": 0, "skipped": 0, "errors": 0,
              "exif": 0, "candidate": 0, "reference": 0}
    for row in rows:
        if row.capture_source and not force:
            result["skipped"] += 1
            result["exif" if row.capture_source == "exif" else row.capture_confidence or "reference"] += 1
            continue
        try:
            path = safe_image_path(settings, row.file_path)
            data = extract_capture_metadata(path, row.relative_path, row.modified_at)
            row.capture_at = data["at"]
            row.capture_year = data["year"]
            row.capture_month = data["month"]
            row.capture_day = data["day"]
            row.capture_source = data["source"]
            row.capture_confidence = data["confidence"]
            row.capture_raw = data["raw"]
            result["updated"] += 1
            result["exif" if data["source"] == "exif" else data["confidence"]] += 1
        except Exception:
            result["errors"] += 1
        db.commit()
    return result
