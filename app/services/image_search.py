"""Local-only image inventory, face candidate and expression search."""

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import Settings
from app.db.models import ImageAsset, ImageFace

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_image(path: Path):
    payload = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(payload, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("画像を読み込めません")
    return image


def safe_image_path(settings: Settings, file_path: str) -> Path:
    root = settings.image_root.resolve()
    path = Path(file_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("画像フォルダ外のファイルです") from exc
    if path.suffix.lower() not in IMAGE_EXTENSIONS or not path.is_file():
        raise ValueError("画像が見つかりません")
    return path


class LocalImageAnalyzer:
    def __init__(self, settings: Settings):
        detector_path = settings.image_yunet_model.resolve()
        recognizer_path = settings.image_sface_model.resolve()
        if not detector_path.is_file() or not recognizer_path.is_file():
            raise RuntimeError("OpenCV顔解析モデルがありません")
        self.detector = cv2.FaceDetectorYN.create(str(detector_path), "", (320, 320), 0.80, 0.30, 5000)
        self.recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        self.smile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_smile.xml")

    def analyze(self, path: Path) -> dict:
        original = _read_image(path)
        original_height, original_width = original.shape[:2]
        scale = min(1.0, 1600.0 / max(original_width, original_height))
        image = original if scale == 1.0 else cv2.resize(
            original, (max(1, int(original_width * scale)), max(1, int(original_height * scale))),
            interpolation=cv2.INTER_AREA,
        )
        height, width = image.shape[:2]
        self.detector.setInputSize((width, height))
        _status, detected = self.detector.detect(image)
        faces = []
        for index, face in enumerate(detected if detected is not None else []):
            x, y, w, h = [float(value) for value in face[:4]]
            x1, y1 = max(0, int(x)), max(0, int(y))
            x2, y2 = min(width, int(x + w)), min(height, int(y + h))
            if x2 <= x1 or y2 <= y1:
                continue
            aligned = self.recognizer.alignCrop(image, face)
            feature = self.recognizer.feature(aligned).flatten().astype(np.float32)
            norm = float(np.linalg.norm(feature))
            if norm:
                feature /= norm
            gray = cv2.cvtColor(image[y1:y2, x1:x2], cv2.COLOR_BGR2GRAY)
            minimum = max(12, min(gray.shape[:2]) // 5)
            smiles = self.smile.detectMultiScale(
                gray, scaleFactor=1.5, minNeighbors=18, minSize=(minimum, minimum)
            ) if gray.size else []
            smiling = len(smiles) > 0
            faces.append({
                "face_index": index,
                "bbox": [x / width, y / height, w / width, h / height],
                "smiling": smiling,
                "smile_score": 1.0 if smiling else 0.0,
                "embedding": feature.tolist(),
            })
        return {"width": original_width, "height": original_height, "faces": faces}


def scan_images(db: Session, settings: Settings) -> dict:
    root = settings.image_root.resolve()
    if not root.is_dir():
        raise ValueError(f"画像フォルダが見つかりません: {root}")
    analyzer = LocalImageAnalyzer(settings)
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS)
    existing = {row.file_path: row for row in db.scalars(select(ImageAsset)).all()}
    current_paths = {str(path.resolve()) for path in files}
    result = {"total": len(files), "added": 0, "updated": 0, "skipped": 0, "deleted": 0, "errors": 0}
    for path in files:
        resolved = str(path.resolve())
        stat = path.stat()
        digest = _hash_file(path)
        row = existing.get(resolved)
        if row and row.content_hash == digest and row.analysis_status == "completed":
            result["skipped"] += 1
            continue
        is_new = row is None
        if is_new:
            row = ImageAsset(
                file_path=resolved, relative_path=str(path.relative_to(root)), file_name=path.name,
                extension=path.suffix.lower(), file_size=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, timezone.utc), content_hash=digest,
            )
            db.add(row)
            db.flush()
        else:
            db.execute(delete(ImageFace).where(ImageFace.image_id == row.id))
            row.relative_path = str(path.relative_to(root))
            row.file_name = path.name
            row.extension = path.suffix.lower()
            row.file_size = stat.st_size
            row.modified_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc)
            row.content_hash = digest
            row.content_analysis_status = "pending"
            row.ocr_text = ""
            row.ocr_lines = []
            row.ai_tags = []
            row.title_candidates = []
        try:
            analysis = analyzer.analyze(path)
            row.width, row.height = analysis["width"], analysis["height"]
            row.face_count = len(analysis["faces"])
            row.smiling_face_count = sum(1 for face in analysis["faces"] if face["smiling"])
            row.analysis_status = "completed"
            row.analysis_error = None
            row.analyzed_at = _now()
            for face in analysis["faces"]:
                db.add(ImageFace(image_id=row.id, **face))
            result["added" if is_new else "updated"] += 1
        except Exception as exc:
            row.analysis_status = "error"
            row.analysis_error = type(exc).__name__
            row.face_count = 0
            row.smiling_face_count = 0
            row.analyzed_at = _now()
            result["errors"] += 1
        db.commit()
    for file_path, row in existing.items():
        if file_path not in current_paths:
            db.delete(row)
            result["deleted"] += 1
    db.commit()
    # Import locally to avoid a module cycle: metadata path validation reuses this module.
    from app.services.image_metadata import refresh_capture_metadata
    result["capture_dates"] = refresh_capture_metadata(db, settings, force=False)
    return result


def parse_image_query(query: str) -> dict:
    normalized = query.strip()
    exact_match = re.search(r"(\d+)\s*人\s*(?:だけ|のみ|ちょうど)", normalized)
    people_match = re.search(r"(\d+)\s*人以上", normalized)
    range_match = re.search(
        r"(19\d{2}|20\d{2})\s*年?\s*(?:から|～|〜|-)\s*(19\d{2}|20\d{2})\s*年?", normalized
    )
    date_match = re.search(r"(19\d{2}|20\d{2})\s*年(?:\s*(\d{1,2})\s*月)?", normalized)
    current_year = datetime.now().astimezone().year
    year = current_year if "今年" in normalized else (int(date_match.group(1)) if date_match else 0)
    return {
        "smile_only": any(word in normalized for word in ("笑顔", "笑って", "笑った", "微笑")),
        "min_people": int(people_match.group(1)) if people_match and not exact_match else 0,
        "exact_people": int(exact_match.group(1)) if exact_match else 0,
        "capture_year": 0 if range_match else year,
        "capture_month": int(date_match.group(2)) if date_match and date_match.group(2) and not range_match else 0,
        "capture_year_from": int(range_match.group(1)) if range_match else 0,
        "capture_year_to": int(range_match.group(2)) if range_match else 0,
        "capture_unknown": any(word in normalized for word in (
            "撮影日不明", "撮影日時不明", "撮影日が分からない", "撮影日がわからない",
        )),
    }

def _face_json(face: ImageFace) -> dict:
    return {
        "id": face.id, "face_index": face.face_index, "smiling": face.smiling,
        "smile_score": face.smile_score, "person_name": face.person_name,
        "person_confirmed": face.person_confirmed,
    }


def _asset_json(row: ImageAsset) -> dict:
    return {
        "id": row.id, "relative_path": row.relative_path, "file_name": row.file_name,
        "width": row.width, "height": row.height, "face_count": row.face_count,
        "smiling_face_count": row.smiling_face_count, "analysis_status": row.analysis_status,
        "tags": row.tags or [], "ai_tags": row.ai_tags or [],
        "ocr_text": row.ocr_text or "", "title_candidates": row.title_candidates or [],
        "content_analysis_status": row.content_analysis_status,
        "capture_at": row.capture_at.isoformat() if row.capture_at else None,
        "capture_year": row.capture_year, "capture_month": row.capture_month,
        "capture_day": row.capture_day, "capture_source": row.capture_source,
        "capture_confidence": row.capture_confidence,
        "faces": [_face_json(face) for face in sorted(row.faces, key=lambda item: item.face_index)],
    }

def search_images(db: Session, query: str = "", smile_only: bool = False,
                  person_name: str = "", min_people: int = 0, exact_people: int = 0,
                  limit: int = 100) -> list[dict]:
    parsed = parse_image_query(query)
    smile_only = smile_only or parsed["smile_only"]
    min_people = max(min_people, parsed["min_people"])
    exact_people = max(exact_people, parsed["exact_people"])
    all_tag_sets = db.execute(select(ImageAsset.tags, ImageAsset.ai_tags)).all()
    known_tags = sorted({
        str(tag) for manual, automatic in all_tag_sets
        for tag in ((manual or []) + (automatic or [])) if str(tag).strip()
    })
    query_tags = [tag for tag in known_tags if tag.lower() in query.lower()]
    known_names = [name for name in db.scalars(select(ImageFace.person_name).where(
        ImageFace.person_name.is_not(None)).distinct()).all() if name]
    if not person_name:
        person_name = next((name for name in known_names if name.lower() in query.lower()), "")
    statement = select(ImageAsset).options(selectinload(ImageAsset.faces)).where(
        ImageAsset.analysis_status == "completed")
    if smile_only:
        statement = statement.where(ImageAsset.smiling_face_count > 0)
    if exact_people:
        statement = statement.where(ImageAsset.face_count == exact_people)
    elif min_people:
        statement = statement.where(ImageAsset.face_count >= min_people)
    if person_name:
        statement = statement.where(ImageAsset.faces.any(ImageFace.person_name.ilike(f"%{person_name.strip()}%")))
    if parsed["capture_year"]:
        statement = statement.where(ImageAsset.capture_year == parsed["capture_year"])
    if parsed["capture_month"]:
        statement = statement.where(ImageAsset.capture_month == parsed["capture_month"])
    if parsed["capture_year_from"]:
        low, high = sorted((parsed["capture_year_from"], parsed["capture_year_to"]))
        statement = statement.where(ImageAsset.capture_year.between(low, high))
    if parsed["capture_unknown"]:
        statement = statement.where(ImageAsset.capture_source == "file_modified")

    searchable = query.strip()
    for word in ("画像", "写真", "ファイル", "ありますか", "集めて", "ください", "写っている", "載っている",
                 "笑っている", "笑った", "笑顔", "微笑んでいる", "を教えて", "候補"):
        searchable = searchable.replace(word, " ")
    searchable = re.sub(
        r"(?:19\d{2}|20\d{2})\s*年?\s*(?:から|～|〜|-)\s*(?:19\d{2}|20\d{2})\s*年?", " ", searchable
    )
    searchable = re.sub(r"(?:19\d{2}|20\d{2})\s*年(?:\s*\d{1,2}\s*月)?", " ", searchable)
    for word in ("今年", "に撮影", "撮影した", "撮影日不明", "撮影日時不明", "撮影日が分からない", "撮影日がわからない"):
        searchable = searchable.replace(word, " ")
    searchable = re.sub(r"\s+", " ", searchable).strip(" ？?。")
    terms = [term.lower() for term in searchable.split() if len(term) >= 2]
    rows = db.scalars(statement.order_by(ImageAsset.modified_at.desc()).limit(500)).all()

    if query_tags:
        rows = [
            row for row in rows
            if all(tag.lower() in {str(value).lower() for value in ((row.tags or []) + (row.ai_tags or []))}
                   for tag in query_tags)
        ]
    has_date_filter = any((parsed["capture_year"], parsed["capture_year_from"], parsed["capture_unknown"]))
    if terms and not person_name and not smile_only and not min_people and not exact_people and not query_tags and not has_date_filter:
        filtered = []
        for row in rows:
            titles = " ".join(str(item.get("text", "")) + " " + str(item.get("rag_match", ""))
                              for item in (row.title_candidates or []))
            haystack = " ".join([
                row.relative_path or "", row.ocr_text or "", titles,
                " ".join(row.tags or []), " ".join(row.ai_tags or []),
            ]).lower()
            if all(term in haystack for term in terms):
                filtered.append(row)
        rows = filtered
    return [_asset_json(row) for row in rows[:max(1, min(limit, 500))]]

def update_image_tags(db: Session, image_id: str, tags: list[str]) -> dict:
    row = db.get(ImageAsset, image_id)
    if not row:
        raise ValueError("画像が見つかりません")
    normalized = []
    seen = set()
    for raw_tag in tags:
        tag = re.sub(r"\s+", " ", str(raw_tag)).strip(" ,、　")[:50]
        key = tag.lower()
        if tag and key not in seen:
            seen.add(key)
            normalized.append(tag)
        if len(normalized) >= 30:
            break
    row.tags = normalized
    db.commit()
    return {"id": row.id, "tags": normalized}

def image_stats(db: Session, settings: Settings) -> dict:
    return {
        "image_root": str(settings.image_root),
        "registered": db.scalar(select(func.count()).select_from(ImageAsset)) or 0,
        "faces": db.scalar(select(func.count()).select_from(ImageFace)) or 0,
        "smiling_images": db.scalar(select(func.count()).select_from(ImageAsset).where(
            ImageAsset.smiling_face_count > 0)) or 0,
        "ocr_completed": db.scalar(select(func.count()).select_from(ImageAsset).where(
            ImageAsset.content_analysis_status == "completed")) or 0,
        "lecture_candidate_images": db.scalar(select(func.count()).select_from(ImageAsset).where(
            func.jsonb_array_length(ImageAsset.title_candidates) > 0)) or 0,
        "labeled_faces": db.scalar(select(func.count()).select_from(ImageFace).where(
            ImageFace.person_name.is_not(None))) or 0,
        "capture_exif": db.scalar(select(func.count()).select_from(ImageAsset).where(
            ImageAsset.capture_source == "exif")) or 0,
        "capture_candidate": db.scalar(select(func.count()).select_from(ImageAsset).where(
            ImageAsset.capture_confidence == "candidate")) or 0,
        "capture_reference": db.scalar(select(func.count()).select_from(ImageAsset).where(
            ImageAsset.capture_confidence == "reference")) or 0,
    }


def label_face(db: Session, settings: Settings, face_id: str, person_name: str,
               propagate: bool = True) -> dict:
    face = db.get(ImageFace, face_id)
    if not face:
        raise ValueError("顔候補が見つかりません")
    name = person_name.strip()[:200]
    if not name:
        face.person_name = None
        face.person_confirmed = False
        db.commit()
        return {"person_name": None, "candidate_count": 0}
    face.person_name = name
    face.person_confirmed = True
    candidate_count = 0
    if propagate:
        distance = ImageFace.embedding.cosine_distance(face.embedding)
        maximum_distance = 1.0 - max(0.0, min(settings.image_similarity_threshold, 1.0))
        candidates = db.execute(select(ImageFace, distance.label("distance")).where(
            ImageFace.id != face.id, ImageFace.image_id != face.image_id,
            distance <= maximum_distance,
        ).order_by(distance).limit(500)).all()
        for candidate, _distance in candidates:
            if candidate.person_confirmed and candidate.person_name and candidate.person_name != name:
                continue
            candidate.person_name = name
            candidate.person_confirmed = False
            candidate_count += 1
    db.commit()
    return {"person_name": name, "candidate_count": candidate_count}


def render_thumbnail(path: Path, max_px: int) -> bytes:
    image = _read_image(path)
    height, width = image.shape[:2]
    scale = min(1.0, max_px / max(width, height))
    if scale < 1.0:
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 84])
    if not ok:
        raise ValueError("サムネイルを作成できません")
    return encoded.tobytes()


def render_face_thumbnail(path: Path, bbox: list, max_px: int = 220) -> bytes:
    image = _read_image(path)
    height, width = image.shape[:2]
    x, y, w, h = bbox
    margin = 0.22
    x1 = max(0, int((x - w * margin) * width))
    y1 = max(0, int((y - h * margin) * height))
    x2 = min(width, int((x + w * (1 + margin)) * width))
    y2 = min(height, int((y + h * (1 + margin)) * height))
    crop = image[y1:y2, x1:x2]
    if not crop.size:
        raise ValueError("顔画像を作成できません")
    return render_array_thumbnail(crop, max_px)


def render_array_thumbnail(image, max_px: int) -> bytes:
    height, width = image.shape[:2]
    scale = min(1.0, max_px / max(width, height))
    if scale < 1.0:
        image = cv2.resize(image, (max(1, int(width * scale)), max(1, int(height * scale))), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 86])
    if not ok:
        raise ValueError("画像を変換できません")
    return encoded.tobytes()