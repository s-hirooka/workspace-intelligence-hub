"""Local OCR and lecture-title candidate analysis for image assets."""

import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Chunk, ImageAsset, IndexedFile
from app.services.image_search import safe_image_path

EVENT_WORDS = (
    "講演", "セミナー", "講師", "トークショー", "研修", "講座", "シンポジウム",
    "相談会", "記念事業", "周年", "大会", "総会", "式典", "フォーラム",
)
TAG_RULES = {
    "講演・セミナー": ("講演", "セミナー", "講師", "トークショー", "講座", "シンポジウム"),
    "相談会": ("相談会", "相談コーナー"),
    "記念事業": ("記念事業", "周年記念", "創立"),
    "研修": ("研修", "勉強会"),
    "総会・大会": ("総会", "大会", "式典"),
    "空き家": ("空き家", "空家"),
    "相続": ("相続",),
    "不動産": ("不動産", "宅地建物", "宅建"),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_title(value: str) -> str:
    value = re.sub(r"[\s　]+", "", value or "")
    return re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠々ー]", "", value).lower()


def _useful_text(value: str) -> bool:
    normalized = normalize_title(value)
    return len(normalized) >= 4 and not normalized.isdigit()


def build_reference_titles(db: Session, limit: int = 6000) -> list[dict]:
    references = []
    seen = set()

    def add(text: str, file_name: str):
        cleaned = re.sub(r"^[#>*・●○\-\d.\s]+", "", (text or "").strip())[:240]
        key = normalize_title(cleaned)
        if not _useful_text(cleaned) or key in seen:
            return
        seen.add(key)
        references.append({"text": cleaned, "file_name": file_name, "key": key})

    for row in db.execute(select(IndexedFile.file_name)).all():
        add(Path(row.file_name).stem, row.file_name)

    chunks = db.execute(select(Chunk.file_name, Chunk.chunk_text).limit(limit)).all()
    for file_name, chunk_text in chunks:
        for line in (chunk_text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or any(word in stripped for word in EVENT_WORDS):
                add(stripped, file_name)
    return references


def infer_candidates(lines: list[dict], path_text: str, references: list[dict]) -> tuple[list[str], list[dict]]:
    combined = "\n".join([path_text] + [line["text"] for line in lines])
    ai_tags = [tag for tag, words in TAG_RULES.items() if any(word in combined for word in words)]
    if lines:
        ai_tags.append("文字あり")

    direct = [line for line in lines if _useful_text(line["text"]) and any(
        word in line["text"] for word in EVENT_WORDS
    )]
    if not direct:
        direct = [line for line in lines if _useful_text(line["text"]) and len(line["text"].strip()) >= 8][:3]

    candidates = []
    seen = set()
    for line in direct[:8]:
        text = line["text"].strip()[:240]
        key = normalize_title(text)
        if not key or key in seen:
            continue
        seen.add(key)
        candidate = {
            "text": text,
            "confidence": round(float(line.get("confidence", 0.0)), 4),
            "source": "画像内文字",
            "matched_document": None,
        }
        best = None
        best_score = 0.0
        for reference in references:
            score = SequenceMatcher(None, key, reference["key"]).ratio()
            if key in reference["key"] or reference["key"] in key:
                score = max(score, min(len(key), len(reference["key"])) / max(len(key), len(reference["key"])))
            if score > best_score:
                best, best_score = reference, score
        if best and best_score >= 0.62:
            candidate["rag_match"] = best["text"]
            candidate["matched_document"] = best["file_name"]
            candidate["match_score"] = round(best_score, 4)
        candidates.append(candidate)
    return list(dict.fromkeys(ai_tags)), candidates


class LocalJapaneseOCR:
    def __init__(self):
        from rapidocr import RapidOCR
        self.engine = RapidOCR()

    def analyze(self, path: Path) -> list[dict]:
        output = self.engine(str(path))
        text_values = getattr(output, "txts", None)
        score_values = getattr(output, "scores", None)
        box_values = getattr(output, "boxes", None)
        texts = list(text_values) if text_values is not None else []
        scores = list(score_values) if score_values is not None else []
        boxes = list(box_values) if box_values is not None else []
        lines = []
        for index, text in enumerate(texts):
            confidence = float(scores[index]) if index < len(scores) else 0.0
            if confidence < 0.50 or not str(text).strip():
                continue
            box = boxes[index].tolist() if index < len(boxes) and hasattr(boxes[index], "tolist") else (
                boxes[index] if index < len(boxes) else []
            )
            lines.append({"text": str(text).strip(), "confidence": round(confidence, 4), "box": box})
        return lines


def analyze_image_content(db: Session, settings: Settings, force: bool = False) -> dict:
    references = build_reference_titles(db)
    ocr = LocalJapaneseOCR()
    rows = db.scalars(select(ImageAsset).where(ImageAsset.analysis_status == "completed").order_by(ImageAsset.relative_path)).all()
    result = {"total": len(rows), "analyzed": 0, "skipped": 0, "errors": 0, "text_images": 0,
              "lecture_candidates": 0, "reference_titles": len(references)}
    for row in rows:
        if not force and row.content_analysis_status == "completed":
            result["skipped"] += 1
            continue
        try:
            path = safe_image_path(settings, row.file_path)
            lines = ocr.analyze(path)
            ai_tags, candidates = infer_candidates(lines, row.relative_path, references)
            row.ocr_lines = lines
            row.ocr_text = "\n".join(line["text"] for line in lines)
            row.ai_tags = ai_tags
            row.title_candidates = candidates
            row.content_analysis_status = "completed"
            row.content_analysis_error = None
            row.content_analyzed_at = _now()
            result["analyzed"] += 1
            result["text_images"] += int(bool(lines))
            result["lecture_candidates"] += len(candidates)
        except Exception as exc:
            row.content_analysis_status = "error"
            row.content_analysis_error = type(exc).__name__
            row.content_analyzed_at = _now()
            result["errors"] += 1
        db.commit()
    return result