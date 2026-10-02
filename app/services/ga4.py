"""Read-only GA4 reporting for SNS-originated site traffic."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

import httpx
from google.auth.transport.requests import Request
from google.oauth2 import service_account

ANALYTICS_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
SOCIAL_SOURCE_PATTERN = ".*(facebook|instagram).*"


class GA4ReportError(RuntimeError):
    """Raised when GA4 cannot return a report without exposing credentials."""


class GA4Config(Protocol):
    ga4_property_id: str
    ga4_credentials_path: Path


def ga4_configured(settings: GA4Config) -> bool:
    return bool(settings.ga4_property_id and Path(settings.ga4_credentials_path).is_file())


def _run_report(settings: GA4Config, payload: dict[str, Any]) -> dict[str, Any]:
    credentials = service_account.Credentials.from_service_account_file(
        str(settings.ga4_credentials_path), scopes=[ANALYTICS_SCOPE]
    )
    credentials.refresh(Request())
    response = httpx.post(
        f"https://analyticsdata.googleapis.com/v1beta/properties/{settings.ga4_property_id}:runReport",
        headers={"Authorization": f"Bearer {credentials.token}"},
        json=payload,
        timeout=45,
    )
    if response.status_code >= 400:
        try:
            status = response.json().get("error", {}).get("status", response.status_code)
        except ValueError:
            status = response.status_code
        raise GA4ReportError(f"GA4 Data API error: {status}")
    return response.json()


def _value(text: str | None) -> int | float:
    if not text:
        return 0
    try:
        number = float(text)
        return int(number) if number.is_integer() else number
    except ValueError:
        return 0


def _report_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    dimensions = [item["name"] for item in report.get("dimensionHeaders", [])]
    metrics = [item["name"] for item in report.get("metricHeaders", [])]
    rows = []
    for row in report.get("rows", []):
        item = {
            name: value.get("value", "")
            for name, value in zip(dimensions, row.get("dimensionValues", []))
        }
        item.update({
            name: _value(value.get("value"))
            for name, value in zip(metrics, row.get("metricValues", []))
        })
        rows.append(item)
    return rows


def _request(days: int, dimensions: list[str], metrics: list[str], *, limit: int = 100) -> dict[str, Any]:
    return {
        "dateRanges": [{"startDate": f"{days}daysAgo", "endDate": "today"}],
        "dimensions": [{"name": name} for name in dimensions],
        "metrics": [{"name": name} for name in metrics],
        "dimensionFilter": {"filter": {"fieldName": "sessionSource", "stringFilter": {
            "matchType": "FULL_REGEXP",
            "value": SOCIAL_SOURCE_PATTERN,
            "caseSensitive": False,
        }}},
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "limit": limit,
    }


def ga4_dashboard(
    settings: GA4Config,
    *,
    days: int = 30,
    reporter: Callable[[GA4Config, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    days = max(1, min(days, 3660))
    if not ga4_configured(settings):
        return {"configured": False, "days": days, "summary": {}, "sources": [], "landings": []}

    run = reporter or _run_report
    summary_metrics = [
        "sessions", "totalUsers", "engagedSessions", "engagementRate",
        "averageSessionDuration", "keyEvents",
    ]
    summary_rows = _report_rows(run(settings, _request(days, [], summary_metrics, limit=1)))
    source_rows = _report_rows(run(settings, _request(
        days,
        ["sessionSource", "sessionMedium"],
        ["sessions", "totalUsers", "engagedSessions", "keyEvents"],
        limit=50,
    )))
    landing_rows = _report_rows(run(settings, _request(
        days,
        ["landingPage", "sessionSource"],
        ["sessions", "totalUsers", "keyEvents"],
        limit=20,
    )))
    summary = summary_rows[0] if summary_rows else {name: 0 for name in summary_metrics}
    return {
        "configured": True,
        "days": days,
        "summary": {
            "sessions": int(summary.get("sessions", 0)),
            "users": int(summary.get("totalUsers", 0)),
            "engaged_sessions": int(summary.get("engagedSessions", 0)),
            "engagement_rate": float(summary.get("engagementRate", 0)),
            "average_session_seconds": float(summary.get("averageSessionDuration", 0)),
            "key_events": float(summary.get("keyEvents", 0)),
        },
        "sources": [{
            "source": row.get("sessionSource", ""),
            "medium": row.get("sessionMedium", ""),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("totalUsers", 0)),
            "engaged_sessions": int(row.get("engagedSessions", 0)),
            "key_events": float(row.get("keyEvents", 0)),
        } for row in source_rows],
        "landings": [{
            "page": row.get("landingPage", ""),
            "source": row.get("sessionSource", ""),
            "sessions": int(row.get("sessions", 0)),
            "users": int(row.get("totalUsers", 0)),
            "key_events": float(row.get("keyEvents", 0)),
        } for row in landing_rows],
        "notice": "UTMのない流入は、参照元が保持されたFacebook・Instagramセッションのみ集計します。",
    }
