from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Chunk, IndexedFile, SnsPost, Workspace
from app.db.session import get_db
from app.services.rag import RagService
from app.services.sns import recommendations as sns_recommendations


router = APIRouter(tags=["顧客ワークスペース"])


class WorkspaceCreate(BaseModel):
    key: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=200)
    industry: str = Field(default="", max_length=200)
    business_summary: str = Field(default="", max_length=4000)
    goals: list[str] = Field(default_factory=list, max_length=30)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    industry: str | None = Field(default=None, max_length=200)
    business_summary: str | None = Field(default=None, max_length=4000)
    goals: list[str] | None = Field(default=None, max_length=30)
    active: bool | None = None


class OpportunityRequest(BaseModel):
    question: str = Field(
        default="登録資料から、今後提案できる仕事を優先順位付きで提案してください。",
        min_length=1,
        max_length=4000,
    )
    project: str = "all"
    top_k: int = Field(default=10, ge=1, le=30)


def _workspace_json(db: Session, row: Workspace) -> dict:
    projects = list(db.scalars(
        select(Chunk.project).where(Chunk.workspace_key == row.key).distinct().order_by(Chunk.project)
    ).all())
    files = db.scalar(select(func.count()).select_from(IndexedFile).where(
        IndexedFile.workspace_key == row.key
    )) or 0
    sns_posts = db.scalar(select(func.count()).select_from(SnsPost).where(
        SnsPost.workspace_key == row.key
    )) or 0
    return {
        "key": row.key,
        "name": row.name,
        "industry": row.industry,
        "business_summary": row.business_summary,
        "goals": row.goals or [],
        "active": row.active,
        "projects": projects,
        "registered_files": files,
        "sns_posts": sns_posts,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def require_workspace(db: Session, key: str, *, active: bool = True) -> Workspace:
    row = db.get(Workspace, key)
    if not row or (active and not row.active):
        raise HTTPException(status_code=404, detail="顧客ワークスペースが見つかりません")
    return row


@router.get("/workspaces")
def list_workspaces(db: Session = Depends(get_db)):
    rows = db.scalars(select(Workspace).where(Workspace.active.is_(True)).order_by(Workspace.name)).all()
    return {"workspaces": [_workspace_json(db, row) for row in rows]}


@router.post("/admin/workspaces")
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    key = payload.key.lower()
    if db.get(Workspace, key):
        raise HTTPException(status_code=409, detail="同じ顧客キーが登録されています")
    if db.scalar(select(Workspace).where(Workspace.name == payload.name)):
        raise HTTPException(status_code=409, detail="同じ顧客名が登録されています")
    row = Workspace(
        key=key,
        name=payload.name.strip(),
        industry=payload.industry.strip(),
        business_summary=payload.business_summary.strip(),
        goals=[goal.strip() for goal in payload.goals if goal.strip()],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _workspace_json(db, row)


@router.patch("/admin/workspaces/{workspace_key}")
def update_workspace(workspace_key: str, payload: WorkspaceUpdate, db: Session = Depends(get_db)):
    row = require_workspace(db, workspace_key, active=False)
    values = payload.model_dump(exclude_unset=True)
    if "name" in values:
        duplicate = db.scalar(select(Workspace).where(
            Workspace.name == values["name"], Workspace.key != workspace_key
        ))
        if duplicate:
            raise HTTPException(status_code=409, detail="同じ顧客名が登録されています")
    for field, value in values.items():
        if isinstance(value, str):
            value = value.strip()
        if field == "goals" and value is not None:
            value = [goal.strip() for goal in value if goal.strip()]
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return _workspace_json(db, row)


@router.post("/workspaces/{workspace_key}/opportunities")
def generate_opportunities(workspace_key: str, payload: OpportunityRequest, db: Session = Depends(get_db)):
    workspace = require_workspace(db, workspace_key)
    projects = set(db.scalars(select(Chunk.project).where(
        Chunk.workspace_key == workspace_key
    ).distinct()).all())
    if payload.project != "all" and payload.project not in projects:
        raise HTTPException(status_code=400, detail="この顧客に登録されていないprojectです")
    sns = sns_recommendations(db, days=90, workspace_key=workspace_key)
    funnel = sns["funnel"]
    sns_summary = (
        f"SNS直近90日: 表示/再生={funnel['exposure']}、プロフィール訪問={funnel['profile_visits']}、"
        f"リンククリック={funnel['link_clicks']}、問い合わせ={funnel['inquiries']}、"
        f"予約={funnel['reservations']}。"
    )
    context = (
        f"顧客名: {workspace.name}\n業種: {workspace.industry or '未登録'}\n"
        f"事業概要: {workspace.business_summary or '未登録'}\n"
        f"目標: {'、'.join(workspace.goals or []) or '未登録'}\n\n{payload.question}"
        f"\n\n{sns_summary}\nSNS数値が0の場合は未計測の可能性と実績0件を区別してください。"
    )
    result = RagService(db, get_settings()).query(
        context, payload.project, payload.top_k, "proposal", workspace_key=workspace_key
    )
    result["sns"] = sns
    if sns["recommendations"]:
        lines = ["", "SNS改善候補:"]
        for item in sns["recommendations"][:5]:
            lines.append(
                f"- {item['title']}：{item['action']}（KPI: {item['kpi']}）"
            )
        result["answer"] += "\n".join(lines)
    return result
