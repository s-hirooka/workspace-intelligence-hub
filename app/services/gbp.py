"""Read-only Google Business Profile performance reporting."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

import httpx

from app.services.gbp_config import GBPWorkspaceConfig

TOKEN_URL = "https://oauth2.googleapis.com/token"
PERFORMANCE_BASE_URL = "https://businessprofileperformance.googleapis.com/v1"
GBP_METRICS = (
    "BUSINESS_IMPRESSIONS_DESKTOP_MAPS",
    "BUSINESS_IMPRESSIONS_MOBILE_MAPS",
    "BUSINESS_IMPRESSIONS_DESKTOP_SEARCH",
    "BUSINESS_IMPRESSIONS_MOBILE_SEARCH",
    "BUSINESS_CONVERSATIONS",
    "BUSINESS_DIRECTION_REQUESTS",
    "CALL_CLICKS",
    "WEBSITE_CLICKS",
    "BUSINESS_BOOKINGS",
    "BUSINESS_FOOD_MENU_CLICKS",
)


class GBPReportError(RuntimeError):
    """Raised when GBP reporting fails without leaking credentials."""


def _access_token(config: GBPWorkspaceConfig) -> str:
    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": config.refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        raise GBPReportError("Google認証サーバーへ接続できませんでした") from exc
    if response.status_code >= 400:
        raise GBPReportError("Googleビジネスプロフィールの再認証が必要です")
    token = str(response.json().get("access_token", ""))
    if not token:
        raise GBPReportError("Googleからアクセストークンを取得できませんでした")
    return token


def _fetch_performance(
    config: GBPWorkspaceConfig,
    access_token: str,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    params: list[tuple[str, str | int]] = [
        ("dailyMetrics", metric) for metric in GBP_METRICS
    ]
    params.extend([
        ("dailyRange.start_date.year", start_date.year),
        ("dailyRange.start_date.month", start_date.month),
        ("dailyRange.start_date.day", start_date.day),
        ("dailyRange.end_date.year", end_date.year),
        ("dailyRange.end_date.month", end_date.month),
        ("dailyRange.end_date.day", end_date.day),
    ])
    try:
        response = httpx.get(
            f"{PERFORMANCE_BASE_URL}/{config.location_id}:fetchMultiDailyMetricsTimeSeries",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
            timeout=45,
        )
    except httpx.HTTPError as exc:
        raise GBPReportError("GoogleビジネスプロフィールAPIへ接続できませんでした") from exc
    if response.status_code in {401, 403}:
        raise GBPReportError("Googleビジネスプロフィールの認証またはAPI権限を確認してください")
    if response.status_code == 404:
        raise GBPReportError("この顧客のGoogleビジネスプロフィール所在地が見つかりません")
    if response.status_code == 429:
        raise GBPReportError("Google APIの一時的な利用上限に達しました。時間を置いて再表示してください")
    if response.status_code >= 400:
        raise GBPReportError(f"GoogleビジネスプロフィールAPIエラー: {response.status_code}")
    try:
        return response.json()
    except ValueError as exc:
        raise GBPReportError("Googleビジネスプロフィールから不正な応答を受信しました") from exc


def _number(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _metric_series(payload: dict[str, Any]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for group in payload.get("multiDailyMetricTimeSeries", []):
        for item in group.get("dailyMetricTimeSeries", []):
            metric = str(item.get("dailyMetric", ""))
            if not metric:
                continue
            values: dict[str, int] = {}
            for dated in item.get("timeSeries", {}).get("datedValues", []):
                parts = dated.get("date", {})
                try:
                    key = date(
                        int(parts.get("year", 0)),
                        int(parts.get("month", 0)),
                        int(parts.get("day", 0)),
                    ).isoformat()
                except (TypeError, ValueError):
                    continue
                values[key] = _number(dated.get("value"))
            result[metric] = values
    return result


def _recommendations(summary: dict[str, int]) -> list[dict[str, str]]:
    visibility = summary["search_impressions"] + summary["maps_impressions"]
    actions = (
        summary["website_clicks"]
        + summary["call_clicks"]
        + summary["direction_requests"]
        + summary["bookings"]
    )
    recommendations: list[dict[str, str]] = []
    if visibility and actions / visibility < 0.02:
        recommendations.append({
            "priority": "high",
            "title": "閲覧から予約・問い合わせへの導線を改善",
            "evidence": f"検索・マップ表示 {visibility:,} 回に対し、主要行動は {actions:,} 回です。",
            "action": "カテゴリ、営業時間、説明文、写真、Webサイト・予約リンクを最新状態にし、予約方法を明確にしてください。",
            "kpi": "Webサイトクリック、電話、経路案内、予約の合計",
        })
    if visibility and not summary["website_clicks"]:
        recommendations.append({
            "priority": "medium",
            "title": "Webサイトへの遷移を確認",
            "evidence": "表示はありますが、Webサイトクリックが記録されていません。",
            "action": "プロフィールのWebサイトURLと予約ページを確認し、SNS・サイトと同じ訴求にそろえてください。",
            "kpi": "Webサイトクリック数",
        })
    if not summary["bookings"]:
        recommendations.append({
            "priority": "medium",
            "title": "予約計測を整備",
            "evidence": "この期間の予約数は0件です。予約機能未使用の場合も0件になります。",
            "action": "利用可能な業種では予約リンクを設定し、予約完了をGA4のキーイベントでも確認できるようにしてください。",
            "kpi": "GBP予約数とGA4予約完了数",
        })
    if not recommendations:
        recommendations.append({
            "priority": "medium",
            "title": "反応が多い導線を継続して比較",
            "evidence": f"主要行動が {actions:,} 回記録されています。",
            "action": "30日・90日を切り替え、電話・サイト・経路案内の伸びた施策を継続してください。",
            "kpi": "主要行動数と表示あたりの行動率",
        })
    return recommendations


def google_business_profile_dashboard(
    config: GBPWorkspaceConfig,
    *,
    days: int = 30,
    token_provider: Callable[[GBPWorkspaceConfig], str] | None = None,
    reporter: Callable[[GBPWorkspaceConfig, str, date, date], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    days = max(1, min(days, 366))
    if not config.configured:
        return {
            "configured": False,
            "workspace_key": config.workspace_key,
            "days": days,
            "summary": {},
            "series": [],
            "recommendations": [],
        }

    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    token = (token_provider or _access_token)(config)
    payload = (reporter or _fetch_performance)(config, token, start_date, end_date)
    metrics = _metric_series(payload)
    dates = sorted({day for values in metrics.values() for day in values})

    def metric_total(name: str) -> int:
        return sum(metrics.get(name, {}).values())

    summary = {
        "search_impressions": metric_total("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH")
        + metric_total("BUSINESS_IMPRESSIONS_MOBILE_SEARCH"),
        "maps_impressions": metric_total("BUSINESS_IMPRESSIONS_DESKTOP_MAPS")
        + metric_total("BUSINESS_IMPRESSIONS_MOBILE_MAPS"),
        "website_clicks": metric_total("WEBSITE_CLICKS"),
        "call_clicks": metric_total("CALL_CLICKS"),
        "direction_requests": metric_total("BUSINESS_DIRECTION_REQUESTS"),
        "conversations": metric_total("BUSINESS_CONVERSATIONS"),
        "bookings": metric_total("BUSINESS_BOOKINGS"),
        "menu_clicks": metric_total("BUSINESS_FOOD_MENU_CLICKS"),
    }
    series = []
    for day in dates:
        series.append({
            "date": day,
            "search_impressions": metrics.get("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", {}).get(day, 0)
            + metrics.get("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", {}).get(day, 0),
            "maps_impressions": metrics.get("BUSINESS_IMPRESSIONS_DESKTOP_MAPS", {}).get(day, 0)
            + metrics.get("BUSINESS_IMPRESSIONS_MOBILE_MAPS", {}).get(day, 0),
            "website_clicks": metrics.get("WEBSITE_CLICKS", {}).get(day, 0),
            "call_clicks": metrics.get("CALL_CLICKS", {}).get(day, 0),
            "direction_requests": metrics.get("BUSINESS_DIRECTION_REQUESTS", {}).get(day, 0),
            "bookings": metrics.get("BUSINESS_BOOKINGS", {}).get(day, 0),
        })
    return {
        "configured": True,
        "workspace_key": config.workspace_key,
        "days": days,
        "summary": summary,
        "series": series,
        "recommendations": _recommendations(summary),
        "notice": "Google公式のBusiness Profile Performance APIから無料で読み取り専用取得しています。",
    }
