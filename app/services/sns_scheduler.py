"""Periodic SNS metric synchronization."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.models import RuntimeSetting, SnsSyncRun
from app.db.session import SessionLocal
from app.services.meta_config import configured_meta_workspace_keys
from app.services.sns import sync_all

logger = logging.getLogger(__name__)
sns_lock = asyncio.Lock()


def _configured_workspace_keys() -> list[str]:
    settings = get_settings()
    keys = []
    if settings.youtube_client_id and settings.youtube_client_secret and settings.youtube_refresh_token:
        keys.append(settings.youtube_workspace_key)
    keys.extend(configured_meta_workspace_keys(settings))
    return list(dict.fromkeys(keys))


def sns_schedule_config(db) -> dict:
    settings = get_settings()
    row = db.get(RuntimeSetting, "sns_sync")
    value = row.value if row else {}
    return {"enabled": bool(value.get("enabled", settings.sns_auto_sync_enabled)),
            "interval_minutes": int(value.get("interval_minutes", settings.sns_auto_sync_interval_minutes))}


def sns_schedule_due(db, now=None, workspace_key: str | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    workspace_key = workspace_key or _default_workspace_key()
    config = sns_schedule_config(db)
    if not config["enabled"]:
        return False
    latest = db.scalars(select(SnsSyncRun).where(
        SnsSyncRun.platform == "scheduler", SnsSyncRun.workspace_key == workspace_key)
                        .order_by(SnsSyncRun.started_at.desc()).limit(1)).first()
    return not latest or latest.started_at + timedelta(minutes=config["interval_minutes"]) <= now


async def run_sns_sync_once(workspace_key: str | None = None) -> dict:
    workspace_key = workspace_key or _default_workspace_key()
    if sns_lock.locked():
        return {"status": "busy", "results": []}
    async with sns_lock:
        def work():
            with SessionLocal() as db:
                results = sync_all(db, get_settings(), workspace_key)
                marker = SnsSyncRun(workspace_key=workspace_key, platform="scheduler", status="completed", finished_at=datetime.now(timezone.utc),
                                    message="定期同期判定を実行しました")
                db.add(marker)
                db.commit()
                return {"status": "completed", "results": results}
        return await asyncio.to_thread(work)


def _default_workspace_key() -> str:
    settings = get_settings()
    configured = configured_meta_workspace_keys(settings)
    return configured[0] if configured else settings.meta_workspace_key


async def sns_scheduler_loop() -> None:
    while True:
        try:
            for workspace_key in _configured_workspace_keys():
                with SessionLocal() as db:
                    due = sns_schedule_due(db, workspace_key=workspace_key)
                if due:
                    await run_sns_sync_once(workspace_key)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled SNS sync failed")
        await asyncio.sleep(60)
