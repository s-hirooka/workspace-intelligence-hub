from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Chunk, IndexedFile, IndexRun
from app.db.session import get_db
from app.services.scanner import list_projects
from app.services.scheduler import run_index_once

router = APIRouter()


@router.post("/index/scan")
async def scan_index():
    result = await run_index_once()
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail="An index scan is already running")
    return result


@router.get("/index/status")
def index_status(db: Session = Depends(get_db)):
    files = db.scalar(select(func.count()).select_from(IndexedFile)) or 0
    chunks = db.scalar(select(func.count()).select_from(Chunk)) or 0
    projects = dict(db.execute(select(IndexedFile.project, func.count()).group_by(IndexedFile.project)).all())
    latest = db.scalars(select(IndexRun).order_by(IndexRun.started_at.desc()).limit(1)).first()
    return {"registered_files": files, "chunks": chunks, "by_project": projects,
            "last_index_at": latest.finished_at if latest else None,
            "last_run_status": latest.status if latest else None}


@router.get("/projects")
def projects(workspace_key: str = "workspace", db: Session = Depends(get_db)):
    indexed = set(db.scalars(select(Chunk.project).where(
        Chunk.workspace_key == workspace_key
    ).distinct()).all())
    if workspace_key == "workspace":
        indexed.update(list_projects(get_settings()))
    return {"workspace_key": workspace_key, "projects": sorted(indexed)}
