"""Import aggregate LINE Official Account friend analytics exports.

LINE's ``friend_overview`` export contains cumulative totals.  Persist the
aggregate timeline as-is and calculate period changes from boundary values;
adding every row would substantially overstate friend additions and blocks.
"""

from __future__ import annotations

import csv
import io
import re
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import RuntimeSetting


def _decode(payload: bytes) -> str:
    if not payload:
        raise ValueError("LINE友だち推移CSVが空です")
    for encoding in ("utf-8-sig", "cp932", "utf-16"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("LINE CSVはUTF-8、Shift_JISまたはUTF-16で保存してください")


def _normalize(value: str) -> str:
    return re.sub(r"[\s_\-（）()・/\\]", "", str(value or "")).lower()


def _find_header(headers: list[str], aliases: tuple[str, ...], *, required: bool = False) -> str | None:
    normalized = {_normalize(header): header for header in headers}
    for alias in aliases:
        target = _normalize(alias)
        if target in normalized:
            return normalized[target]
    for key, original in normalized.items():
        if any(_normalize(alias) in key for alias in aliases):
            return original
    if required:
        raise ValueError(f"LINE友だち推移CSVに列がありません：{'／'.join(aliases[:3])}")
    return None


def _parse_date(value: str) -> date | None:
    value = str(value or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y年%m月%d日"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def _number(value: str) -> int:
    normalized = re.sub(r"[^0-9-]", "", str(value or ""))
    try:
        return max(0, int(normalized))
    except ValueError:
        return 0


def parse_line_friends_csv(payload: bytes, filename: str = "line-friends.csv") -> dict[str, Any]:
    text = _decode(payload).replace("\x00", "")
    first = text.splitlines()[0] if text.splitlines() else ""
    delimiter = "\t" if first.count("\t") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headers = [str(header or "").lstrip("\ufeff").strip() for header in (reader.fieldnames or [])]
    if not headers:
        raise ValueError("LINE友だち推移CSVの見出し行がありません")
    date_column = _find_header(headers, ("日付", "date", "集計日"), required=True)
    contacts_column = _find_header(
        headers,
        ("contacts", "友だち追加数", "友だち追加", "総友だち数", "friendsadded", "addedfriends"),
        required=True,
    )
    blocked_column = _find_header(
        headers,
        ("blocks", "ブロック数", "ブロック", "blocked", "followerslost"),
    )
    reach_column = _find_header(
        headers,
        ("targetReaches", "ターゲットリーチ", "有効友だち数", "targetreach", "targetedreaches"),
        required=True,
    )
    series: list[dict[str, Any]] = []
    for raw in reader:
        row = {str(key or "").lstrip("\ufeff").strip(): str(value or "").strip() for key, value in raw.items()}
        measured_on = _parse_date(row.get(date_column, ""))
        if not measured_on:
            continue
        series.append({
            "date": measured_on.isoformat(),
            "contacts": _number(row.get(contacts_column, "")),
            "blocks": _number(row.get(blocked_column, "")) if blocked_column else 0,
            "target_reach": _number(row.get(reach_column, "")),
        })
    if not series:
        raise ValueError("LINE友だち推移CSVに日次データがありません")
    series.sort(key=lambda item: item["date"])
    return {
        "version": 2,
        "imported_at": datetime.now(timezone.utc).isoformat(),
        "file": {"filename": Path(filename).name, "rows": len(series)},
        "series": series,
        "privacy": {"aggregate_only": True, "pii_stored": False},
    }


def import_line_friends_csv(
    db: Session,
    payload: bytes,
    *,
    workspace_key: str,
    filename: str,
) -> dict[str, Any]:
    snapshot = parse_line_friends_csv(payload, filename)
    snapshot["workspace_key"] = workspace_key
    key = f"line_friends:{workspace_key}"
    row = db.get(RuntimeSetting, key) or RuntimeSetting(key=key)
    row.value = snapshot
    db.add(row)
    db.commit()
    return {
        "workspace_key": workspace_key,
        "message": f"LINE友だち推移を{len(snapshot['series'])}日分取り込みました。",
        "file": snapshot["file"],
        "imported_at": snapshot["imported_at"],
    }


def _months_ago(value: date, months: int) -> date:
    total = value.year * 12 + value.month - 1 - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    return date(year, month, min(value.day, monthrange(year, month)[1]))


def line_friends_analytics(db: Session, workspace_key: str, months: int = 3) -> dict[str, Any]:
    months = max(1, min(months, 24))
    row = db.get(RuntimeSetting, f"line_friends:{workspace_key}")
    if not row or not row.value:
        return {
            "configured": False,
            "workspace_key": workspace_key,
            "notice": "LINE公式アカウントの『友だち > 概要（日次）』CSVを取り込んでください。",
            "series": [],
        }
    snapshot = row.value
    source = sorted(snapshot.get("series", []), key=lambda item: item["date"])
    end = max(date.fromisoformat(item["date"]) for item in source)
    start = _months_ago(end, months)
    selected = [item for item in source if start <= date.fromisoformat(item["date"]) <= end]
    earlier = [item for item in source if date.fromisoformat(item["date"]) < start]
    baseline = earlier[-1] if earlier else selected[0]
    latest = selected[-1]

    def delta(field: str, current: dict[str, Any], previous: dict[str, Any]) -> int:
        return max(0, int(current.get(field) or 0) - int(previous.get(field) or 0))

    timeline: list[dict[str, Any]] = []
    previous = baseline
    for item in selected:
        timeline.append({
            "date": item["date"],
            "contacts": int(item.get("contacts") or 0),
            "target_reach": int(item.get("target_reach") or 0),
            "blocks": int(item.get("blocks") or 0),
            "added": delta("contacts", item, previous),
            "blocked": delta("blocks", item, previous),
            "net_growth": int(item.get("target_reach") or 0) - int(previous.get("target_reach") or 0),
        })
        previous = item

    added = delta("contacts", latest, baseline)
    blocked = delta("blocks", latest, baseline)
    net_growth = int(latest.get("target_reach") or 0) - int(baseline.get("target_reach") or 0)
    month_rows: list[dict[str, Any]] = []
    for month in sorted({item["date"][:7] for item in timeline}):
        rows = [item for item in timeline if item["date"].startswith(month)]
        month_rows.append({
            "month": month,
            "added": sum(int(item["added"]) for item in rows),
            "blocked": sum(int(item["blocked"]) for item in rows),
            "net_growth": sum(int(item["net_growth"]) for item in rows),
        })
    return {
        "configured": True,
        "workspace_key": workspace_key,
        "imported_at": snapshot.get("imported_at"),
        "file": snapshot.get("file", {}),
        "period": {"start": start.isoformat(), "end": end.isoformat(), "months": months},
        "summary": {
            "added": added,
            "blocked": blocked,
            "net_growth": net_growth,
            "contacts": int(latest.get("contacts") or 0),
            "latest_target_reach": int(latest.get("target_reach") or 0),
        },
        "series": timeline,
        "by_month": month_rows,
        "notice": "LINE公式アカウントの累計値から期間差分を算出しています。個人を特定する情報は含みません。",
    }
