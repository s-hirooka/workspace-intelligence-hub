import json
import time
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from app.db.models import EvaluationResult, EvaluationRun
from app.services.rag import RagService

QUESTION_FILE = Path(__file__).resolve().parents[2] / "evaluation_questions.json"


def load_questions() -> list[dict]:
    questions = json.loads(QUESTION_FILE.read_text(encoding="utf-8"))
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate evaluation question IDs")
    return questions


def run_evaluation(db, settings, ids=None, label="", rag_factory=RagService):
    questions = [q for q in load_questions() if not ids or q["id"] in ids]
    if ids and set(ids) != {q["id"] for q in questions}:
        raise ValueError("Unknown evaluation question ID")
    run = EvaluationRun(label=label)
    db.add(run)
    db.flush()
    rag = rag_factory(db, settings)
    statuses, citation_count, source_match_count = [], 0, 0
    for q in questions:
        started = time.monotonic()
        try:
            response = rag.query(q["question"], q["project"], 8, "investigation")
            answer, sources = response["answer"], response["sources"]
            keywords = q.get("expected_answer", [])
            expected_sources = q.get("expected_sources", [])
            match = all(word.lower() in answer.lower() for word in keywords) if keywords else None
            source_match = all(any(path.lower() in s["file_path"].lower() for s in sources) for path in expected_sources) if expected_sources else None
            has_sources = bool(sources)
            status = "manual_review" if match is None else "pass" if match and has_sources and source_match is not False else "partial" if match else "fail"
            citation_count += has_sources
            source_match_count += source_match is True
            metrics = {"answer_generated": bool(answer), "expected_keyword_match": match,
                       "expected_source_match": source_match, "has_sources": has_sources,
                       "unsupported_claim": None, "manual_review_required": True,
                       "latency_seconds": round(time.monotonic() - started, 3),
                       "retrieved_projects": sorted({s["project"] for s in sources}),
                       "answer_text": answer,
                       "sources": [{"project": s["project"], "file_path": s["file_path"],
                                    "relative_path": s.get("relative_path", s["file_path"]),
                                    "source_type": s.get("source_type")}
                                   for s in sources]}
        except Exception as exc:
            # Infrastructure/API failures cannot be scored as answer-quality failures.
            status = "error"
            metrics = {"answer_generated": False, "expected_keyword_match": False,
                       "expected_source_match": None, "has_sources": False,
                       "unsupported_claim": None, "manual_review_required": True,
                       "latency_seconds": round(time.monotonic() - started, 3),
                       "error_type": type(exc).__name__, "retrieved_projects": []}
        statuses.append(status)
        db.add(EvaluationResult(run_id=run.id, question_id=q["id"], status=status, metrics=metrics))
        db.flush()
    count = len(questions)
    evaluated = count - statuses.count("error")
    run.summary = {"total": count, "pass": statuses.count("pass"), "partial": statuses.count("partial"),
                   "fail": statuses.count("fail"), "error": statuses.count("error"),
                   "manual_review": statuses.count("manual_review"),
                   "source_citation_rate": round(citation_count / evaluated, 3) if evaluated else None,
                   "expected_source_match_rate": round(source_match_count / sum(bool(q.get("expected_sources")) for q in questions), 3)
                   if any(q.get("expected_sources") for q in questions) else None}
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    return {"run_id": run.id, "label": label, **run.summary}


def latest_report(db):
    runs = db.scalars(select(EvaluationRun).order_by(EvaluationRun.started_at.desc()).limit(20)).all()
    return {"latest": ({"run_id": runs[0].id, "label": runs[0].label, **runs[0].summary} if runs else None),
            "history": [{"run_id": row.id, "label": row.label, "started_at": row.started_at,
                         "summary": row.summary} for row in runs]}
