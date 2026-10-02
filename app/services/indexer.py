import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import Chunk, IndexedFile, IndexRun
from app.loaders import load_source
from app.services.chunker import chunk_extracted
from app.services.embedder import OpenAIEmbedder
from app.services.scanner import candidate_paths, scan_files
from app.services.security import audit_item, inspect_text

logger = logging.getLogger(__name__)


def classify_changes(scanned: dict[str, str], stored: dict[str, str], candidates: set[str] | None = None):
    candidates = candidates if candidates is not None else set(scanned)
    added = {path for path in scanned if path not in stored}
    updated = {path for path, digest in scanned.items() if path in stored and stored[path] != digest}
    unchanged = {path for path, digest in scanned.items() if stored.get(path) == digest}
    deleted = {path for path in stored if path not in candidates}
    return added, updated, unchanged, deleted


class IndexService:
    def __init__(self, db: Session, settings: Settings, embedder=None):
        self.db = db
        self.settings = settings
        self.embedder = embedder or OpenAIEmbedder(settings, db, "index_embedding")

    def scan(self, only_paths: set[str] | None = None) -> dict:
        """Index everything, or only explicitly selected scanner candidates.

        In a targeted scan, unrelated indexed rows are not audited or deleted.
        """
        if only_paths is not None:
            if not only_paths:
                raise ValueError("only_paths must not be empty")
            if only_paths - candidate_paths(self.settings):
                raise ValueError("only_paths contains paths outside the scan policy")
        logger.info("Scan started")
        run = IndexRun()
        self.db.add(run)
        self.db.commit()
        failures: list[str] = []
        blocked = 0
        completed_added = 0
        completed_updated = 0
        try:
            candidates = candidate_paths(self.settings)
            if only_paths is not None:
                candidates &= only_paths
            scanned_items = [item for item in scan_files(self.settings)
                             if only_paths is None or item.path in only_paths]
            scanned = {item.path: item for item in scanned_items}
            stored_rows = self.db.scalars(select(IndexedFile)).all()
            stored = {item.file_path: item for item in stored_rows
                      if only_paths is None or item.file_path in only_paths}
            added, updated, unchanged, deleted_paths = classify_changes(
                {path: item.content_hash for path, item in scanned.items()},
                {path: item.content_hash for path, item in stored.items()}, candidates,
            )
            quarantined = 0
            for path in sorted(unchanged):
                try:
                    _chunks, decision = audit_item(scanned[path], self.settings)
                    if decision.blocked:
                        self.db.delete(stored[path])
                        self.db.commit()
                        quarantined += 1
                        blocked += 1
                    else:
                        for existing_chunk in self.db.scalars(select(Chunk).where(Chunk.indexed_file_id == stored[path].id)):
                            pii = inspect_text(existing_chunk.chunk_text).pii_types
                            existing_chunk.source_type, existing_chunk.source_id = scanned[path].source_type, scanned[path].source_id
                            existing_chunk.workspace_key = scanned[path].workspace_key
                            existing_chunk.pii_detected, existing_chunk.pii_types = bool(pii), pii
                        stored[path].workspace_key = scanned[path].workspace_key
                        self.db.commit()
                except Exception as exc:
                    failures.append(f"{scanned[path].relative_path}: {type(exc).__name__}")
                    self.db.rollback()
            for path in sorted(added | updated):
                item = scanned[path]
                try:
                    chunks, decision = audit_item(item, self.settings)
                    if decision.blocked:
                        blocked += 1
                        # Quarantine a previously indexed version rather than serve stale unsafe text.
                        if path in stored:
                            self.db.delete(stored[path])
                            self.db.commit()
                        continue
                    vectors = self.embedder.embed([chunk.text for chunk in chunks]) if chunks else []
                    row = stored.get(path)
                    if row is None:
                        row = IndexedFile(
                            project=item.project, workspace_key=item.workspace_key,
                            file_path=item.path, relative_path=item.relative_path,
                            file_name=item.file_name, extension=item.extension,
                            content_hash=item.content_hash, modified_at=item.modified_at,
                        )
                        self.db.add(row)
                        self.db.flush()
                    else:
                        self.db.execute(delete(Chunk).where(Chunk.indexed_file_id == row.id))
                        row.project, row.workspace_key, row.relative_path = item.project, item.workspace_key, item.relative_path
                        row.file_name, row.extension = item.file_name, item.extension
                        row.content_hash, row.modified_at = item.content_hash, item.modified_at
                        row.indexed_at = datetime.now(timezone.utc)
                    for index, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
                        pii = inspect_text(chunk.text).pii_types
                        self.db.add(Chunk(
                            indexed_file_id=row.id, chunk_index=index, project=item.project,
                            workspace_key=item.workspace_key,
                            file_path=item.path, relative_path=item.relative_path,
                            file_name=item.file_name, extension=item.extension,
                            language=chunk.language, symbol_name=chunk.symbol_name,
                            chunk_type=chunk.chunk_type, chunk_text=chunk.text,
                            modified_at=item.modified_at, content_hash=item.content_hash,
                            page_number=chunk.page_number, sheet_name=chunk.sheet_name,
                            cell_range=chunk.cell_range, embedding=vector,
                            source_type=item.source_type, source_id=item.source_id,
                            pii_detected=bool(pii), pii_types=pii,
                        ))
                    self.db.commit()
                    if path in added:
                        completed_added += 1
                    else:
                        completed_updated += 1
                except Exception as exc:
                    self.db.rollback()
                    failures.append(f"{item.relative_path}: {type(exc).__name__}")
                    logger.warning("Index failed for %s (%s)", item.relative_path, type(exc).__name__)
            for path in deleted_paths:
                self.db.delete(stored[path])
            self.db.commit()
            run = self.db.get(IndexRun, run.id)
            run.total, run.added, run.updated = len(candidates), completed_added, completed_updated
            run.deleted, run.skipped, run.errors = len(deleted_paths) + quarantined, len(unchanged) - quarantined, len(failures)
            run.finished_at, run.status = datetime.now(timezone.utc), "completed_with_errors" if failures else "completed"
            run.error_summary = "\n".join(failures[:100]) or None
            self.db.commit()
            result = {"run_id": run.id, "status": run.status, "total": run.total, "blocked": blocked,
                      "quarantined": quarantined, "added": run.added,
                      "updated": run.updated, "deleted": run.deleted, "skipped": run.skipped,
                      "errors": run.errors, "failures": failures}
            logger.info("Scan finished total=%d new=%d updated=%d deleted=%d skipped=%d errors=%d", run.total, run.added, run.updated, run.deleted, run.skipped, run.errors)
            return result
        except Exception:
            self.db.rollback()
            run = self.db.get(IndexRun, run.id)
            run.status, run.finished_at = "failed", datetime.now(timezone.utc)
            self.db.commit()
            logger.exception("Scan aborted")
            raise
