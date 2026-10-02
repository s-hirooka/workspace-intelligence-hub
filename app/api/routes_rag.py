from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import RagQuery, RagResponse
from app.config import get_settings
from app.db.models import Chunk, Workspace
from app.db.session import get_db
from app.services.rag import RagService
from app.services.scanner import list_projects

router = APIRouter()


@router.post("/rag/query", response_model=RagResponse)
def rag_query(request: RagQuery, db: Session = Depends(get_db)):
    settings = get_settings()
    workspace = db.get(Workspace, request.workspace_key)
    if not workspace or not workspace.active:
        raise HTTPException(status_code=404, detail="Unknown workspace")
    indexed_projects = set(db.scalars(select(Chunk.project).where(
        Chunk.workspace_key == request.workspace_key
    ).distinct()).all())
    scanner_projects = set(list_projects(settings)) if request.workspace_key == "workspace" else set()
    allowed = {"all", *indexed_projects, *scanner_projects}
    if request.project not in allowed:
        raise HTTPException(status_code=400, detail="Unknown project")
    return RagService(db, settings).query(
        request.question, request.project, request.top_k, request.mode,
        workspace_key=request.workspace_key,
    )
