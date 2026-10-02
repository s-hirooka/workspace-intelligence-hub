"""Read-only Meta Graph API client with secret-safe errors and bounded paging."""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

PROFILE_FIELDS = "id,username,name,followers_count,media_count"
MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,"
    "like_count,comments_count"
)
BASE_INSIGHT_METRICS = ("reach", "saved", "shares")
VIDEO_INSIGHT_METRICS = ("views",)
FEED_FUNNEL_METRICS = ("profile_visits", "profile_activity", "follows")
AUDIENCE_METRICS = (
    "follower_demographics",
    "engaged_audience_demographics",
    "reached_audience_demographics",
)
AUDIENCE_DIMENSIONS = ("age", "gender", "city", "country")


class MetaApiError(RuntimeError):
    """A sanitized Meta failure that never contains a request URL or token."""

    def __init__(self, category: str, *, status_code: int | None = None,
                 code: int | None = None, subcode: int | None = None):
        self.category = category
        self.status_code = status_code
        self.code = code
        self.subcode = subcode
        details = [category]
        if code is not None:
            details.append(f"code={code}")
        if subcode is not None:
            details.append(f"subcode={subcode}")
        super().__init__("Meta API error (" + ", ".join(details) + ")")


def _error_category(status_code: int, error: dict[str, Any]) -> str:
    code = error.get("code")
    subcode = error.get("error_subcode")
    message = str(error.get("message", "")).lower()
    if status_code == 429 or code in {4, 17, 32, 613}:
        return "rate_limit"
    if code == 190:
        if subcode in {458, 463, 464, 467} or "expired" in message:
            return "expired"
        return "revoked"
    if code in {10, 200, 298} or "permission" in message:
        return "permission_error"
    if code in {100, 2500} and ("metric" in message or "insight" in message):
        return "unsupported_metric"
    if code in {33, 803}:
        return "unavailable_media"
    if status_code >= 500:
        return "server_error"
    return "api_error"


def token_lifecycle(expires_at: datetime | None, *, valid: bool = True,
                    error_category: str | None = None,
                    now: datetime | None = None) -> dict[str, Any]:
    """Return UI-safe token state and whole remaining days."""
    now = now or datetime.now(timezone.utc)
    if not valid:
        status = "expired" if error_category == "expired" else "revoked"
        return {"status": status, "days_remaining": 0, "level": "danger"}
    if not expires_at:
        return {"status": "valid", "days_remaining": None, "level": "ok"}
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    seconds = (expires_at - now).total_seconds()
    days = max(0, int(seconds // 86400))
    if seconds <= 0:
        return {"status": "expired", "days_remaining": 0, "level": "danger"}
    if days <= 7:
        return {"status": "expiring_soon", "days_remaining": days, "level": "danger"}
    if days <= 14:
        return {"status": "expiring_soon", "days_remaining": days, "level": "warning"}
    return {"status": "valid", "days_remaining": days, "level": "ok"}


class MetaGraphClient:
    def __init__(self, settings: Settings, *, client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.settings = settings
        self.base_url = f"https://graph.facebook.com/{settings.meta_graph_version}"
        self.user_token = settings.meta_access_token
        self.client = client or httpx.Client(timeout=45)
        self._owns_client = client is None
        self.sleep = sleep
        self.max_retries = max(0, min(settings.meta_request_max_retries, 5))

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _usage_headers(response: httpx.Response) -> None:
        usage = {}
        for name in ("x-app-usage", "x-page-usage", "x-business-use-case-usage"):
            if response.headers.get(name):
                try:
                    usage[name] = json.loads(response.headers[name])
                except (TypeError, ValueError):
                    usage[name] = "present"
        if usage:
            logger.info("Meta API usage headers=%s", usage)

    def _get(self, path: str, *, params: dict[str, Any] | None = None,
             token: str | None = None) -> dict[str, Any]:
        if not self.user_token:
            raise MetaApiError("not_configured")
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Authorization": f"Bearer {token or self.user_token}"}
        for attempt in range(self.max_retries + 1):
            try:
                response = self.client.get(url, params=params or {}, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    self.sleep(2 ** attempt)
                    continue
                raise MetaApiError("timeout") from exc
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    self.sleep(2 ** attempt)
                    continue
                raise MetaApiError("network_error") from exc
            self._usage_headers(response)
            if response.status_code < 400:
                try:
                    return response.json()
                except ValueError as exc:
                    raise MetaApiError("invalid_response", status_code=response.status_code) from exc
            try:
                error = response.json().get("error", {})
            except ValueError:
                error = {}
            category = _error_category(response.status_code, error)
            if category in {"rate_limit", "server_error"} and attempt < self.max_retries:
                self.sleep(2 ** attempt)
                continue
            raise MetaApiError(
                category,
                status_code=response.status_code,
                code=error.get("code"),
                subcode=error.get("error_subcode"),
            )
        raise MetaApiError("unknown_error")

    def validate_token(self) -> dict[str, Any]:
        debug_token = (
            f"{self.settings.meta_app_id}|{self.settings.meta_app_secret}"
            if self.settings.meta_app_id and self.settings.meta_app_secret
            else self.user_token
        )
        body = self._get(
            "debug_token",
            params={"input_token": self.user_token},
            token=debug_token,
        )
        data = body.get("data", {})
        expires_at = _unix_datetime(data.get("expires_at"))
        data_access_expires_at = _unix_datetime(data.get("data_access_expires_at"))
        valid = bool(data.get("is_valid"))
        invalid_category = (
            "expired"
            if expires_at and expires_at <= datetime.now(timezone.utc)
            else "revoked"
        )
        lifecycle = token_lifecycle(
            expires_at, valid=valid, error_category=invalid_category
        )
        return {
            "is_valid": valid,
            "app_id": str(data.get("app_id", "")),
            "type": str(data.get("type", "")),
            "scopes": list(data.get("scopes", [])),
            "expires_at": expires_at,
            "data_access_expires_at": data_access_expires_at,
            **lifecycle,
        }

    def get_facebook_page(self, page_id: str) -> tuple[dict[str, Any], str]:
        page = self._get(page_id, params={
            "fields": "id,name,instagram_business_account,access_token",
        })
        return page, str(page.get("access_token") or self.user_token)

    def get_instagram_profile(self, account_id: str, *, token: str) -> dict[str, Any]:
        return self._get(account_id, params={"fields": PROFILE_FIELDS}, token=token)

    def get_instagram_media(self, account_id: str, *, token: str, limit: int) -> list[dict[str, Any]]:
        return self.get_pages(
            f"{account_id}/media",
            params={"fields": MEDIA_FIELDS, "limit": min(100, max(1, limit))},
            token=token,
            limit=limit,
        )

    def get_pages(self, path: str, *, params: dict[str, Any], token: str,
                  limit: int, max_pages: int = 50) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        request_params = dict(params)
        seen_cursors: set[str] = set()
        for _page_number in range(max_pages):
            body = self._get(path, params=request_params, token=token)
            result.extend(body.get("data", []))
            if len(result) >= limit:
                break
            paging = body.get("paging", {})
            next_url = str(paging.get("next") or "")
            after = str(paging.get("cursors", {}).get("after") or "")
            if not after and next_url:
                after = str(parse_qs(urlparse(next_url).query).get("after", [""])[0])
            if not next_url or not after or after in seen_cursors:
                break
            seen_cursors.add(after)
            request_params = {**params, "after": after}
        return result[:limit]

    def get_instagram_media_insights(self, media: dict[str, Any], *, token: str) -> dict[str, Any]:
        media_type = str(media.get("media_type", "")).upper()
        product_type = str(media.get("media_product_type", "")).upper()
        metrics = list(BASE_INSIGHT_METRICS)
        if media_type == "VIDEO" or product_type == "REELS":
            metrics.extend(VIDEO_INSIGHT_METRICS)
        # Meta currently exposes these conversion metrics for feed posts, but
        # rejects them for Reels. Avoid a known-invalid request for every Reel.
        if product_type == "FEED":
            metrics.extend(FEED_FUNNEL_METRICS)
        values: dict[str, int] = {}
        unavailable: list[str] = []
        errors: dict[str, str] = {}
        raw: dict[str, Any] = {}
        metrics_to_retry = list(metrics)
        try:
            body = self._get(
                f"{media['id']}/insights",
                params={"metric": ",".join(metrics)},
                token=token,
            )
            returned = {
                str(metric.get("name")): metric
                for metric in body.get("data", [])
                if metric.get("name")
            }
            metrics_to_retry = []
            for metric_name in metrics:
                metric = returned.get(metric_name)
                if metric is None:
                    metrics_to_retry.append(metric_name)
                    continue
                raw[metric_name] = metric
                number = _insight_metric_number(metric)
                if number is None:
                    unavailable.append(metric_name)
                else:
                    values[metric_name] = number
        except MetaApiError as exc:
            if exc.category not in {"unsupported_metric", "unavailable_media"}:
                return {
                    "values": values,
                    "unavailable": unavailable,
                    "errors": {"batch": exc.category},
                    "raw": raw,
                }

        # A combined request is much faster for the normal case. If Meta omits
        # or rejects one metric, retry only those metrics so one unavailable
        # field never discards the valid values for the post.
        for metric_name in metrics_to_retry:
            try:
                body = self._get(
                    f"{media['id']}/insights",
                    params={"metric": metric_name},
                    token=token,
                )
                data = body.get("data", [])
                if not data:
                    unavailable.append(metric_name)
                    continue
                metric = data[0]
                raw[metric_name] = metric
                number = _insight_metric_number(metric)
                if number is None:
                    unavailable.append(metric_name)
                else:
                    values[metric_name] = number
            except MetaApiError as exc:
                if exc.category in {"unsupported_metric", "unavailable_media"}:
                    unavailable.append(metric_name)
                else:
                    errors[metric_name] = exc.category
        return {"values": values, "unavailable": unavailable, "errors": errors, "raw": raw}

    def get_instagram_account_insights(
        self,
        account_id: str,
        *,
        token: str,
        days: int = 30,
    ) -> dict[str, Any]:
        """Return account-level link taps and privacy-safe audience aggregates."""
        days = max(1, min(days, 90))
        now = datetime.now(timezone.utc)
        since = int((now - timedelta(days=days)).timestamp())
        until = int(now.timestamp())
        result: dict[str, Any] = {
            "profile_links_taps": None,
            "accounts_engaged_this_month": None,
            "reach_this_month": None,
            "period_days": days,
            "demographics": {},
            "unavailable": [],
            "errors": {},
        }
        try:
            body = self._get(
                f"{account_id}/insights",
                params={
                    "metric": "profile_links_taps",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": since,
                    "until": until,
                },
                token=token,
            )
            data = body.get("data", [])
            if data:
                result["profile_links_taps"] = _metric_total(data[0])
            else:
                result["unavailable"].append("profile_links_taps")
        except MetaApiError as exc:
            if exc.category in {"unsupported_metric", "unavailable_media"}:
                result["unavailable"].append("profile_links_taps")
            else:
                result["errors"]["profile_links_taps"] = exc.category

        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        try:
            body = self._get(
                f"{account_id}/insights",
                params={
                    "metric": "accounts_engaged,reach",
                    "period": "day",
                    "metric_type": "total_value",
                    "since": int(month_start.timestamp()),
                    "until": until,
                },
                token=token,
            )
            returned = {str(item.get("name")): item for item in body.get("data", [])}
            for metric_name, result_name in (
                ("accounts_engaged", "accounts_engaged_this_month"),
                ("reach", "reach_this_month"),
            ):
                if metric_name in returned:
                    result[result_name] = _metric_total(returned[metric_name])
                else:
                    result["unavailable"].append(result_name)
        except MetaApiError as exc:
            result["errors"]["audience_totals_this_month"] = exc.category

        for metric_name in AUDIENCE_METRICS:
            dimensions: dict[str, dict[str, int]] = {}
            for dimension in AUDIENCE_DIMENSIONS:
                key = f"{metric_name}.{dimension}"
                try:
                    body = self._get(
                        f"{account_id}/insights",
                        params={
                            "metric": metric_name,
                            "period": "lifetime",
                            "metric_type": "total_value",
                            "timeframe": "this_month",
                            "breakdown": dimension,
                        },
                        token=token,
                    )
                    data = body.get("data", [])
                    values = _metric_breakdown(data[0]) if data else {}
                    if values:
                        dimensions[dimension] = values
                    else:
                        result["unavailable"].append(key)
                except MetaApiError as exc:
                    if exc.category in {"unsupported_metric", "unavailable_media"}:
                        result["unavailable"].append(key)
                    else:
                        result["errors"][key] = exc.category
            result["demographics"][metric_name] = dimensions
        return result


def _unix_datetime(value: Any) -> datetime | None:
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc) if seconds > 0 else None


def _metric_number(value: Any) -> int:
    if isinstance(value, dict):
        return int(sum(number for number in value.values() if isinstance(number, (int, float))))
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _metric_total(metric: dict[str, Any]) -> int:
    total = metric.get("total_value")
    if isinstance(total, dict) and "value" in total:
        return _metric_number(total.get("value"))
    values = metric.get("values", [])
    if values:
        return _metric_number(values[-1].get("value"))
    return _metric_number(metric.get("value"))


def _insight_metric_number(metric: dict[str, Any]) -> int | None:
    metric_values = metric.get("values", [])
    if metric_values:
        return _metric_number(metric_values[-1].get("value"))
    if "value" in metric:
        return _metric_number(metric.get("value"))
    if "total_value" in metric:
        return _metric_total(metric)
    return None


def _metric_breakdown(metric: dict[str, Any]) -> dict[str, int]:
    """Normalize Meta's aggregate breakdown response without user-level data."""
    normalized: dict[str, int] = {}
    total = metric.get("total_value") or {}
    for breakdown in total.get("breakdowns", []) if isinstance(total, dict) else []:
        for row in breakdown.get("results", []):
            labels = [str(value) for value in row.get("dimension_values", []) if value is not None]
            if not labels:
                continue
            label = " / ".join(labels)
            normalized[label] = normalized.get(label, 0) + _metric_number(row.get("value"))
    if normalized:
        return normalized
    value = total.get("value") if isinstance(total, dict) else None
    if isinstance(value, dict):
        return {str(key): _metric_number(number) for key, number in value.items()}
    return normalized
