"""Local OpenAI token and estimated-cost ledger.

The API usage object is authoritative for tokens. USD values are estimates using
configurable rates and must be compared with the OpenAI billing dashboard.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import UsageEvent


def record_embedding_usage(db: Session | None, settings: Settings, model: str, tokens: int, operation: str) -> None:
    if db is None:
        return
    cost = tokens * settings.openai_embedding_input_usd_per_million / 1_000_000
    db.add(UsageEvent(operation=operation, model=model, input_tokens=tokens,
                      output_tokens=0, estimated_cost_usd=cost))
    db.flush()


def record_chat_usage(db: Session | None, settings: Settings, model: str, usage, operation: str = "rag_chat") -> None:
    if db is None or usage is None:
        return
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    cost = (input_tokens * settings.openai_chat_input_usd_per_million
            + output_tokens * settings.openai_chat_output_usd_per_million) / 1_000_000
    db.add(UsageEvent(operation=operation, model=model, input_tokens=input_tokens,
                      output_tokens=output_tokens, estimated_cost_usd=cost))
    db.flush()


def monthly_summary(db: Session, settings: Settings) -> dict:
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    row = db.execute(select(
        func.coalesce(func.sum(UsageEvent.input_tokens), 0),
        func.coalesce(func.sum(UsageEvent.output_tokens), 0),
        func.coalesce(func.sum(UsageEvent.estimated_cost_usd), 0.0),
        func.coalesce(func.sum(UsageEvent.request_count), 0),
    ).where(UsageEvent.occurred_at >= start)).one()
    cost = float(row[2] or 0)
    budget = settings.openai_monthly_budget_usd
    return {"month": start.strftime("%Y-%m"), "input_tokens": int(row[0]), "output_tokens": int(row[1]),
            "estimated_cost_usd": round(cost, 6), "request_count": int(row[3]),
            "budget_usd": budget, "budget_percent": round(cost / budget * 100, 2) if budget else None,
            "warning": bool(budget and cost >= budget * 0.8),
            "note": "概算です。最終金額はOpenAIのBilling画面で確認してください。"}