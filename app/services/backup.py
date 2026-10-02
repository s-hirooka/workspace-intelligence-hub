"""PostgreSQL backup/restore through the existing Docker Compose database service."""

import hashlib
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import BackupRecord


def _run(command: list[str], *, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=input_bytes, capture_output=True, check=False, timeout=1800)
    if result.returncode:
        error = result.stderr.decode("utf-8", errors="replace")[-2000:]
        raise RuntimeError(f"バックアップ処理に失敗しました: {error}")
    return result.stdout


def create_backup(db: Session, settings: Settings, username: str | None, note: str = "") -> BackupRecord:
    settings.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file_name = f"workspace-hub-{stamp}.dump"
    target = settings.backup_dir / file_name
    payload = _run(["docker", "compose", "exec", "-T", "db", "pg_dump", "-U", "workspace", "-d", "workspace_hub", "-Fc"])
    target.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    row = BackupRecord(created_by=username, file_name=file_name, file_size=len(payload), sha256=digest,
                       status="completed", note=note[:500])
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def restore_backup(db: Session, settings: Settings, backup_id: str) -> None:
    row = db.get(BackupRecord, backup_id)
    if not row or row.status != "completed":
        raise ValueError("利用できるバックアップがありません")
    target = (settings.backup_dir / row.file_name).resolve()
    if target.parent != settings.backup_dir.resolve() or not target.is_file():
        raise ValueError("バックアップファイルが見つかりません")
    payload = target.read_bytes()
    if not hashlib.sha256(payload).hexdigest() == row.sha256:
        raise ValueError("バックアップの整合性確認に失敗しました")
    db.close()
    _run(["docker", "compose", "exec", "-T", "db", "pg_restore", "--clean", "--if-exists",
          "--no-owner", "-U", "workspace", "-d", "workspace_hub"], input_bytes=payload)