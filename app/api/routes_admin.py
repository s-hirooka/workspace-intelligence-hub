from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import BackupRecord, IndexRun, OperationLog, RuntimeSetting, User, Workspace
from app.db.session import get_db
from app.services.auth import ROLES, create_user, hash_password
from app.services.backup import create_backup, restore_backup
from app.services.scanner import scan_files
from app.services.scheduler import run_index_once, schedule_config
from app.services.security import audit_item
from app.services.usage import monthly_summary

router = APIRouter(prefix="/admin", tags=["管理"])
SAFE_DOCUMENT_EXTENSIONS = {".md", ".txt", ".pdf", ".docx", ".xlsx", ".csv"}


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=12, max_length=200)
    display_name: str = Field(default="", max_length=200)
    role: str = "viewer"


class UserUpdate(BaseModel):
    role: str | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=200)


class ScheduleUpdate(BaseModel):
    enabled: bool
    interval_minutes: int = Field(ge=15, le=10080)


class BackupRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class RestoreRequest(BaseModel):
    backup_id: str
    confirmation: str


def _user(request: Request):
    return request.state.user


def _user_json(user: User) -> dict:
    return {"id": user.id, "username": user.username, "display_name": user.display_name,
            "role": user.role, "active": user.active, "created_at": user.created_at,
            "last_login_at": user.last_login_at}


@router.get("/overview")
def overview(db: Session = Depends(get_db)):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    errors = db.scalar(select(func.count()).select_from(OperationLog).where(
        OperationLog.occurred_at >= since, OperationLog.status_code >= 500)) or 0
    latest = db.scalars(select(IndexRun).order_by(IndexRun.started_at.desc()).limit(1)).first()
    return {"usage": monthly_summary(db, get_settings()), "errors_24h": errors,
            "latest_index": ({"status": latest.status, "finished_at": latest.finished_at,
                              "added": latest.added, "updated": latest.updated, "errors": latest.errors}
                             if latest else None), "schedule": schedule_config(db)}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return {"users": [_user_json(row) for row in db.scalars(select(User).order_by(User.username)).all()]}


@router.post("/users")
def add_user(payload: UserCreate, db: Session = Depends(get_db)):
    try:
        return _user_json(create_user(db, payload.username, payload.password, payload.display_name, payload.role))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/users/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, db: Session = Depends(get_db)):
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status_code=404, detail="利用者が見つかりません")
    if payload.role is not None:
        if payload.role not in ROLES:
            raise HTTPException(status_code=400, detail="不正な権限です")
        row.role = payload.role
    if payload.active is not None:
        if row.id == _user(request).id and not payload.active:
            raise HTTPException(status_code=400, detail="自分自身を無効化できません")
        row.active = payload.active
    if payload.password:
        row.password_hash = hash_password(payload.password)
        row.must_change_password = False
    db.commit()
    return _user_json(row)


@router.get("/logs")
def logs(limit: int = 100, errors_only: bool = False, db: Session = Depends(get_db)):
    statement = select(OperationLog).order_by(OperationLog.occurred_at.desc()).limit(max(1, min(limit, 500)))
    if errors_only:
        statement = statement.where(OperationLog.status_code >= 400)
    rows = db.scalars(statement).all()
    return {"logs": [{"occurred_at": row.occurred_at, "username": row.username, "action": row.action,
                       "method": row.method, "path": row.path, "status_code": row.status_code,
                       "duration_ms": row.duration_ms} for row in rows]}


@router.get("/usage")
def usage(db: Session = Depends(get_db)):
    return monthly_summary(db, get_settings())


@router.get("/schedule")
def get_schedule(db: Session = Depends(get_db)):
    return schedule_config(db)


@router.put("/schedule")
def set_schedule(payload: ScheduleUpdate, request: Request, db: Session = Depends(get_db)):
    row = db.get(RuntimeSetting, "auto_index") or RuntimeSetting(key="auto_index")
    row.value = {"enabled": payload.enabled, "interval_minutes": payload.interval_minutes}
    row.updated_by = _user(request).username
    db.add(row)
    db.commit()
    return schedule_config(db)


@router.post("/index/run")
async def run_index():
    result = await run_index_once()
    if result.get("status") == "busy":
        raise HTTPException(status_code=409, detail="Index処理は実行中です")
    return result


@router.post("/documents")
async def upload_document(request: Request, file: UploadFile = File(...),
                          workspace_key: str = Form("workspace"), run_index: bool = True,
                          db: Session = Depends(get_db)):
    settings = get_settings()
    workspace = db.get(Workspace, workspace_key)
    if not workspace or not workspace.active:
        raise HTTPException(status_code=404, detail="顧客ワークスペースが見つかりません")
    original = Path(file.filename or "")
    suffix = original.suffix.lower()
    if suffix not in SAFE_DOCUMENT_EXTENSIONS:
        raise HTTPException(status_code=400, detail="登録できる形式はMD、TXT、PDF、Word、Excel、CSVです")
    safe_stem = re.sub(r"[^0-9A-Za-zぁ-んァ-ヶ一-龠々ー_. -]", "_", original.stem).strip(" .")[:120] or "document"
    payload = await file.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    if len(payload) > settings.max_upload_size_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"{settings.max_upload_size_mb}MB以下の資料を選んでください")
    workspace_upload_dir = settings.managed_upload_dir / workspace_key
    workspace_upload_dir.mkdir(parents=True, exist_ok=True)
    target = workspace_upload_dir / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}_{safe_stem}{suffix}"
    target.write_bytes(payload)
    try:
        item = next((row for row in scan_files(settings) if Path(row.path) == target.resolve()), None)
        if not item:
            raise ValueError("Index対象ポリシーに含まれていません")
        _chunks, decision = audit_item(item, settings)
        if decision.blocked:
            raise ValueError("機密情報の可能性があるため登録を中止しました")
    except Exception as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result = await run_index_once() if run_index else {"status": "not_requested"}
    return {"file_name": target.name, "workspace_key": workspace_key,
            "size": len(payload), "index": result}


@router.get("/backups")
def backups(db: Session = Depends(get_db)):
    rows = db.scalars(select(BackupRecord).order_by(BackupRecord.created_at.desc()).limit(100)).all()
    return {"backups": [{"id": row.id, "created_at": row.created_at, "created_by": row.created_by,
                          "file_name": row.file_name, "file_size": row.file_size, "sha256": row.sha256,
                          "status": row.status, "note": row.note} for row in rows]}


@router.post("/backups")
def backup(payload: BackupRequest, request: Request, db: Session = Depends(get_db)):
    try:
        row = create_backup(db, get_settings(), _user(request).username, payload.note)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"id": row.id, "file_name": row.file_name, "file_size": row.file_size, "sha256": row.sha256}


@router.post("/restore")
def restore(payload: RestoreRequest, request: Request, db: Session = Depends(get_db)):
    if payload.confirmation != "RESTORE":
        raise HTTPException(status_code=400, detail="確認欄へRESTOREと入力してください")
    try:
        safety = create_backup(db, get_settings(), _user(request).username, "復旧直前の自動バックアップ")
        restore_backup(db, get_settings(), payload.backup_id)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"ok": True, "safety_backup": safety.file_name,
            "message": "復旧しました。アプリを再起動してください。"}
