"""Small in-process scheduler for periodic differential indexing."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.config import get_settings
from app.db.models import IndexRun, RuntimeSetting
from app.db.session import SessionLocal
from app.services.indexer import IndexService

logger = logging.getLogger(__name__)
index_lock = asyncio.Lock()


def schedule_config(db) -> dict:
    settings = get_settings()
    row = db.get(RuntimeSetting, "auto_index")
    configured = row.value if row else {}
    return {"enabled": bool(configured.get("enabled", settings.auto_index_enabled)),
            "interval_minutes": int(configured.get("interval_minutes", settings.auto_index_interval_minutes))}


def schedule_due(db, now=None) -> bool:
    now = now or datetime.now(timezone.utc)
    config = schedule_config(db)
    if not config["enabled"]:
        return False
    latest = db.scalars(select(IndexRun).order_by(IndexRun.started_at.desc()).limit(1)).first()
    return not latest or latest.started_at + timedelta(minutes=config["interval_minutes"]) <= now


async def run_index_once() -> dict:
    if index_lock.locked():
        return {"status": "busy"}
    async with index_lock:
        def work():
            with SessionLocal() as db:
                return IndexService(db, get_settings()).scan()
        return await asyncio.to_thread(work)


async def scheduler_loop() -> None:
    while True:
        try:
            with SessionLocal() as db:
                due = schedule_due(db)
            if due:
                await run_index_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Scheduled index failed")
        await asyncio.sleep(60)