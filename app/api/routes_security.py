"""Read-only audit and no-Embedding dry run; results never include secret values."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import IndexedFile
from app.db.session import get_db
from app.services.scanner import candidate_paths, scan_files
from app.services.security import audit_item

router = APIRouter()
_last_results: list[dict] = []


class AuditRequest(BaseModel):
    source: str | None = None
    project: str | None = None


def _selected(request: AuditRequest):
    settings = get_settings()
    for item in scan_files(settings):
        if (not request.source or item.source_id == request.source) and (not request.project or item.project == request.project):
            yield item


@router.post("/security/audit")
def audit(request: AuditRequest):
    global _last_results
    settings = get_settings()
    counts = {"files_scanned": 0, "safe": 0, "warning": 0, "blocked": 0, "errors": 0}
    results = []
    for item in _selected(request):
        counts["files_scanned"] += 1
        try:
            _chunks, decision = audit_item(item, settings)
            state = "blocked" if decision.blocked else "warning" if decision.pii_types else "safe"
            counts[state] += 1
            results.append({"project": item.project, "relative_path": item.relative_path,
                            "severity": decision.severity, "action": decision.action,
                            "pii_types": decision.pii_types, "finding_types": decision.finding_types,
                            "blocked": decision.blocked})
        except Exception:
            counts["errors"] += 1
            results.append({"project": item.project, "relative_path": item.relative_path,
                            "severity": "UNKNOWN", "action": "skip", "blocked": True,
                            "pii_types": [], "finding_types": ["read_error"]})
    _last_results = results
    return counts


@router.get("/security/audit/results")
def audit_results(blocked_only: bool = False):
    return {"results": [row for row in _last_results if not blocked_only or row["blocked"]]}


@router.post("/index/dry-run")
def dry_run(request: AuditRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    stored = {row.file_path: row.content_hash for row in db.scalars(select(IndexedFile)).all()}
    selected = list(_selected(request))
    current = {row.path: row for row in selected}
    counts = {"new": 0, "modified": 0, "deleted": 0, "skipped": 0, "blocked": 0,
              "quarantine_existing": 0,
              "estimated_chunks": 0, "estimated_characters": 0, "estimated_tokens": 0,
              "files_for_embedding": 0}
    for row in selected:
        try:
            chunks, decision = audit_item(row, settings)
        except Exception:
            counts["blocked"] += 1
            continue
        if decision.blocked:
            counts["blocked"] += 1
            counts["quarantine_existing"] += row.path in stored
            continue
        if stored.get(row.path) == row.content_hash:
            counts["skipped"] += 1
            continue
        counts["new" if row.path not in stored else "modified"] += 1
        counts["files_for_embedding"] += 1
        counts["estimated_chunks"] += len(chunks)
        counts["estimated_characters"] += sum(len(c.text) for c in chunks)
    # Only a full-scope dry run may report deletions; selected runs do not imply deletion.
    if not request.source and not request.project:
        counts["deleted"] = len(set(stored) - candidate_paths(settings))
    counts["estimated_tokens"] = (counts["estimated_characters"] + 3) // 4
    return counts
