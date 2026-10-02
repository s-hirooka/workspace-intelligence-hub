"""Instagram account, media and insight history synchronized through Meta Graph API."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    InstagramAccountSnapshot,
    InstagramMediaInsightSnapshot,
    MetaConnection,
    SnsMetricSnapshot,
    SnsPost,
    SnsSyncRun,
)
from app.services.meta_config import MetaWorkspaceConfig, resolve_meta_config
from app.services.meta_graph import MetaApiError, MetaGraphClient, token_lifecycle

REQUIRED_SCOPES = {
    "read_insights", "pages_show_list", "instagram_basic",
    "instagram_manage_insights", "pages_read_engagement", "pages_read_user_content",
}
RANKING_METRICS = {
    "reach", "profile_visits", "follows", "likes", "comments", "saved", "shares",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def meta_configured(settings: Settings, workspace_key: str) -> bool:
    return resolve_meta_config(settings, workspace_key).configured


def _connection(db: Session, config: MetaWorkspaceConfig, workspace_key: str) -> MetaConnection:
    row = db.scalar(select(MetaConnection).where(MetaConnection.workspace_key == workspace_key))
    if row:
        return row
    row = MetaConnection(
        workspace_key=workspace_key,
        facebook_page_id=config.meta_page_id,
        instagram_account_id=config.meta_instagram_account_id,
    )
    db.add(row)
    db.flush()
    return row


def _safe_error_code(exc: MetaApiError) -> str | None:
    if exc.code is None:
        return None
    return str(exc.code) + (f"/{exc.subcode}" if exc.subcode is not None else "")


def _connection_error_status(category: str) -> str:
    if category == "expired":
        return "expired"
    if category in {"revoked", "app_mismatch"}:
        return "auth_error"
    if category == "permission_error":
        return "permission_error"
    return "api_error"


def _account_snapshot(db: Session, connection: MetaConnection, profile: dict[str, Any],
                      measured_at: datetime,
                      account_insights: dict[str, Any] | None = None) -> InstagramAccountSnapshot:
    snapshot = db.scalar(select(InstagramAccountSnapshot).where(
        InstagramAccountSnapshot.connection_id == connection.id,
        InstagramAccountSnapshot.snapshot_date == measured_at.date(),
    ))
    if not snapshot:
        snapshot = InstagramAccountSnapshot(
            connection_id=connection.id,
            instagram_account_id=str(profile.get("id", "")),
            snapshot_date=measured_at.date(),
        )
        db.add(snapshot)
    snapshot.followers_count = int(profile.get("followers_count") or 0)
    snapshot.media_count = int(profile.get("media_count") or 0)
    if account_insights is not None:
        demographics = account_insights.get("demographics", {})
        if account_insights.get("profile_links_taps") is not None:
            snapshot.profile_links_taps_30d = int(account_insights["profile_links_taps"])
        if account_insights.get("accounts_engaged_this_month") is not None:
            snapshot.accounts_engaged_this_month = int(
                account_insights["accounts_engaged_this_month"]
            )
        if account_insights.get("reach_this_month") is not None:
            snapshot.reach_this_month = int(account_insights["reach_this_month"])
        snapshot.follower_demographics = demographics.get("follower_demographics", {})
        snapshot.engaged_audience_demographics = demographics.get(
            "engaged_audience_demographics", {}
        )
        snapshot.reached_audience_demographics = demographics.get(
            "reached_audience_demographics", {}
        )
        snapshot.account_insights_unavailable = account_insights.get("unavailable", [])
        snapshot.account_insight_errors = account_insights.get("errors", {})
    snapshot.captured_at = measured_at
    return snapshot


def _post_and_snapshots(db: Session, workspace_key: str, media: dict[str, Any],
                        insights: dict[str, Any], measured_at: datetime) -> str:
    external_id = str(media["id"])
    post = db.scalar(select(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == "instagram",
        SnsPost.external_id == external_id,
    ))
    action = "updated"
    if not post:
        post = SnsPost(workspace_key=workspace_key, platform="instagram", external_id=external_id)
        db.add(post)
        db.flush()
        action = "added"
    post.account_name = str(media.get("account_name", ""))
    post.content = str(media.get("caption") or "")
    post.permalink = str(media.get("permalink") or "")
    post.media_type = str(media.get("media_type") or "")
    post.media_product_type = str(media.get("media_product_type") or "")
    post.published_at = _as_datetime(media.get("timestamp"))
    post.synced_at = measured_at
    db.flush()

    values = insights.get("values", {})
    daily = db.scalar(select(SnsMetricSnapshot).where(
        SnsMetricSnapshot.post_id == post.id,
        SnsMetricSnapshot.measured_date == measured_at.date(),
    ))
    if not daily:
        daily = SnsMetricSnapshot(post_id=post.id, measured_date=measured_at.date())
        db.add(daily)
    daily.measured_at = measured_at
    daily.likes = int(media.get("like_count") or 0)
    daily.comments = int(media.get("comments_count") or 0)
    daily.reach = int(values.get("reach") or 0)
    daily.saves = int(values.get("saved") or 0)
    daily.shares = int(values.get("shares") or 0)
    daily.views = int(values.get("views") or 0)
    daily.profile_visits = int(values.get("profile_visits") or 0)
    daily.raw_metrics = {
        "meta": insights.get("raw", {}),
        "unavailable_metrics": insights.get("unavailable", []),
        "metric_errors": insights.get("errors", {}),
    }

    history = db.scalar(select(InstagramMediaInsightSnapshot).where(
        InstagramMediaInsightSnapshot.post_id == post.id,
        InstagramMediaInsightSnapshot.snapshot_date == measured_at.date(),
    ))
    if not history:
        history = InstagramMediaInsightSnapshot(post_id=post.id, snapshot_date=measured_at.date())
        db.add(history)
    history.snapshot_at = measured_at
    history.reach = values.get("reach")
    history.saved = values.get("saved")
    history.shares = values.get("shares")
    history.views = values.get("views")
    history.profile_visits = values.get("profile_visits")
    history.profile_activity = values.get("profile_activity")
    history.follows = values.get("follows")
    history.raw_metrics = insights.get("raw", {})
    history.unavailable_metrics = insights.get("unavailable", [])
    history.metric_errors = insights.get("errors", {})
    return action


def sync_instagram_analytics(db: Session, settings: Settings,
                             workspace_key: str) -> dict[str, Any]:
    run = SnsSyncRun(workspace_key=workspace_key, platform="instagram")
    db.add(run)
    db.commit()
    run_id = run.id
    errors: Counter[str] = Counter()
    added = updated = insights_synced = media_found = 0
    profile_synced = False
    config = resolve_meta_config(settings, workspace_key)
    connection = _connection(db, config, workspace_key)
    previous_sync_at = connection.last_sync_at
    db.commit()
    connection_id = connection.id
    try:
        if not config.configured:
            raise MetaApiError("not_configured")
        with MetaGraphClient(config) as client:
            token = client.validate_token()
            connection = db.get(MetaConnection, connection_id)
            connection.token_last_verified_at = _now()
            connection.token_expires_at = token["expires_at"]
            connection.data_access_expires_at = token["data_access_expires_at"]
            connection.connection_status = "expiring" if token["status"] == "expiring_soon" else "active"
            if not token["is_valid"]:
                raise MetaApiError(
                    "expired" if token["status"] == "expired" else "revoked"
                )
            if config.meta_app_id and token.get("app_id") != config.meta_app_id:
                raise MetaApiError("app_mismatch")
            missing_scopes = REQUIRED_SCOPES - set(token.get("scopes", []))
            if missing_scopes:
                raise MetaApiError("permission_error")
            page, page_token = client.get_facebook_page(config.meta_page_id)
            linked = page.get("instagram_business_account") or {}
            linked_id = str(linked.get("id") or "")
            if not linked_id:
                raise MetaApiError("instagram_not_linked")
            if config.meta_instagram_account_id and linked_id != config.meta_instagram_account_id:
                raise MetaApiError("connection_mismatch")
            profile = client.get_instagram_profile(linked_id, token=page_token)
            measured_at = _now()
            connection.facebook_page_id = str(page.get("id") or config.meta_page_id)
            connection.facebook_page_name = str(page.get("name") or "")
            connection.instagram_account_id = linked_id
            connection.instagram_username = str(profile.get("username") or "")
            connection.instagram_name = str(profile.get("name") or "")
            connection.last_error = None
            connection.last_error_category = None
            connection.last_error_code = None
            account_insights = client.get_instagram_account_insights(
                linked_id, token=page_token, days=30
            )
            for category in account_insights.get("errors", {}).values():
                errors[category] += 1
            _account_snapshot(db, connection, profile, measured_at, account_insights)
            db.commit()
            profile_synced = True

            media_rows = client.get_instagram_media(
                linked_id,
                token=page_token,
                limit=(
                    config.sns_max_posts_per_platform
                    if previous_sync_at is None
                    else min(
                        config.sns_max_posts_per_platform,
                        config.sns_incremental_posts_per_platform,
                    )
                ),
            )
            media_found = len(media_rows)
            for media in media_rows:
                media["account_name"] = connection.instagram_username
                try:
                    insights = client.get_instagram_media_insights(media, token=page_token)
                    for category in insights.get("errors", {}).values():
                        errors[category] += 1
                    action = _post_and_snapshots(db, workspace_key, media, insights, _now())
                    db.commit()
                    if action == "added":
                        added += 1
                    else:
                        updated += 1
                    insights_synced += 1
                except MetaApiError as exc:
                    db.rollback()
                    errors[exc.category] += 1
                except Exception:
                    db.rollback()
                    errors["db_error"] += 1

        connection = db.get(MetaConnection, connection_id)
        connection.last_sync_at = _now()
        if errors:
            if errors["expired"]:
                connection.connection_status = "expired"
            elif errors["revoked"]:
                connection.connection_status = "auth_error"
            elif errors["permission_error"]:
                connection.connection_status = "permission_error"
            elif connection.connection_status == "active":
                connection.connection_status = "api_error"
            connection.last_error_category = next(iter(errors))
            connection.last_error = "一部投稿の指標を取得できませんでした"
        run = db.get(SnsSyncRun, run_id)
        run.status = "partial_success" if errors else "success"
        run.message = f"Instagram {media_found}件を同期しました" + (f"（指標エラー{sum(errors.values())}件）" if errors else "")
    except MetaApiError as exc:
        db.rollback()
        connection = db.get(MetaConnection, connection_id)
        connection.connection_status = _connection_error_status(exc.category)
        connection.last_error_category = exc.category
        connection.last_error_code = _safe_error_code(exc)
        connection.last_error = str(exc)
        run = db.get(SnsSyncRun, run_id)
        run.status = "failed"
        run.message = _user_message(exc.category)
        errors[exc.category] += 1
    except Exception:
        db.rollback()
        connection = db.get(MetaConnection, connection_id)
        connection.connection_status = "api_error"
        connection.last_error_category = "unknown_error"
        connection.last_error = "同期処理で内部エラーが発生しました"
        run = db.get(SnsSyncRun, run_id)
        run.status = "failed"
        run.message = "Instagram同期で内部エラーが発生しました"
        errors["unknown_error"] += 1

    run.finished_at = _now()
    run.added = added
    run.updated = updated
    run.errors = sum(errors.values())
    run.profile_synced = profile_synced
    run.media_found = media_found
    run.insights_synced = insights_synced
    run.error_summary = dict(errors)
    db.add(connection)
    db.add(run)
    db.commit()
    return {
        "platform": "instagram", "status": run.status,
        "added": added, "updated": updated, "errors": run.errors,
        "profile_synced": profile_synced, "media_found": media_found,
        "insights_synced": insights_synced, "message": run.message,
    }


def connection_status(db: Session, settings: Settings, workspace_key: str) -> dict[str, Any]:
    configured = meta_configured(settings, workspace_key)
    row = db.scalar(select(MetaConnection).where(MetaConnection.workspace_key == workspace_key))
    if not row:
        return {"configured": configured, "status": "not_synced" if configured else "not_configured",
                "token": {"status": "unknown", "days_remaining": None, "level": "warning"}}
    expirations = [value for value in (row.token_expires_at, row.data_access_expires_at) if value]
    effective_expiration = min(expirations) if expirations else None
    token = token_lifecycle(effective_expiration, valid=row.connection_status not in {"expired", "auth_error"},
                            error_category="expired" if row.connection_status == "expired" else "revoked")
    return {
        "configured": configured,
        "status": row.connection_status,
        "facebook_page_id": row.facebook_page_id,
        "facebook_page_name": row.facebook_page_name,
        "instagram_account_id": row.instagram_account_id,
        "instagram_username": row.instagram_username,
        "instagram_name": row.instagram_name,
        "last_sync_at": row.last_sync_at,
        "last_error_category": row.last_error_category,
        "token": token,
    }


def instagram_dashboard(db: Session, settings: Settings, workspace_key: str,
                        *, days: int = 30, ranking_metric: str = "reach",
                        limit: int = 50) -> dict[str, Any]:
    days = max(0, min(days, 3650))
    ranking_metric = ranking_metric if ranking_metric in RANKING_METRICS else "reach"
    limit = max(1, min(limit, 200))
    status = connection_status(db, settings, workspace_key)
    connection = db.scalar(select(MetaConnection).where(MetaConnection.workspace_key == workspace_key))
    if not connection:
        return {"connection": status, "account": None, "followers": _empty_followers(),
                "posts": [], "ranking": [], "by_media_type": [],
                "ranking_metric": ranking_metric, "funnel": _empty_funnel(),
                "audience": _empty_audience()}

    snapshots = list(db.scalars(select(InstagramAccountSnapshot).where(
        InstagramAccountSnapshot.connection_id == connection.id,
    ).order_by(InstagramAccountSnapshot.snapshot_date)).all())
    latest_account = snapshots[-1] if snapshots else None
    followers = _follower_summary(snapshots)

    statement = select(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == "instagram",
    )
    if days:
        statement = statement.where(SnsPost.published_at >= _now() - timedelta(days=days))
    posts = list(db.scalars(statement.order_by(SnsPost.published_at.desc()).limit(limit)).all())
    post_items = _post_items(db, posts)
    ranking = sorted(
        post_items,
        key=lambda item: (-1 if item[ranking_metric] is None else item[ranking_metric]),
        reverse=True,
    )
    funnel = _funnel_summary(post_items, latest_account)
    audience = _audience_summary(latest_account)
    return {
        "connection": status,
        "account": ({
            "username": connection.instagram_username,
            "name": connection.instagram_name,
            "followers_count": latest_account.followers_count if latest_account else None,
            "media_count": latest_account.media_count if latest_account else None,
            "last_sync_at": connection.last_sync_at,
        } if connection else None),
        "followers": followers,
        "posts": post_items,
        "ranking": ranking,
        "by_media_type": _by_media_type(post_items),
        "ranking_metric": ranking_metric,
        "funnel": funnel,
        "audience": audience,
    }


def instagram_post_detail(db: Session, workspace_key: str, post_id: str) -> dict[str, Any] | None:
    post = db.scalar(select(SnsPost).where(
        SnsPost.id == post_id,
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == "instagram",
    ))
    if not post:
        return None
    history = list(db.scalars(select(InstagramMediaInsightSnapshot).where(
        InstagramMediaInsightSnapshot.post_id == post.id,
    ).order_by(InstagramMediaInsightSnapshot.snapshot_at)).all())
    latest_metrics = db.scalars(select(SnsMetricSnapshot).where(
        SnsMetricSnapshot.post_id == post.id,
    ).order_by(SnsMetricSnapshot.measured_at.desc()).limit(1)).first()
    latest_insight = history[-1] if history else None
    return {
        "id": post.id, "external_id": post.external_id, "caption": post.content,
        "permalink": post.permalink, "published_at": post.published_at,
        "media_type": post.media_type, "media_product_type": post.media_product_type,
        "metrics": {
            "likes": latest_metrics.likes if latest_metrics else None,
            "comments": latest_metrics.comments if latest_metrics else None,
            "reach": latest_insight.reach if latest_insight else None,
            "saved": latest_insight.saved if latest_insight else None,
            "shares": latest_insight.shares if latest_insight else None,
            "views": latest_insight.views if latest_insight else None,
            "profile_visits": latest_insight.profile_visits if latest_insight else None,
            "profile_activity": latest_insight.profile_activity if latest_insight else None,
            "follows": latest_insight.follows if latest_insight else None,
        },
        "history": [{
            "snapshot_at": row.snapshot_at, "reach": row.reach, "saved": row.saved,
            "shares": row.shares, "views": row.views,
            "profile_visits": row.profile_visits,
            "profile_activity": row.profile_activity,
            "follows": row.follows,
            "unavailable_metrics": row.unavailable_metrics,
            "metric_errors": row.metric_errors,
        } for row in history],
    }


def _post_items(db: Session, posts: list[SnsPost]) -> list[dict[str, Any]]:
    if not posts:
        return []
    ids = [post.id for post in posts]
    insight_rows = list(db.scalars(select(InstagramMediaInsightSnapshot).where(
        InstagramMediaInsightSnapshot.post_id.in_(ids),
    ).order_by(InstagramMediaInsightSnapshot.snapshot_at.desc())).all())
    metric_rows = list(db.scalars(select(SnsMetricSnapshot).where(
        SnsMetricSnapshot.post_id.in_(ids),
    ).order_by(SnsMetricSnapshot.measured_at.desc())).all())
    insight_by_post = {}
    metrics_by_post = {}
    for row in insight_rows:
        insight_by_post.setdefault(row.post_id, row)
    for row in metric_rows:
        metrics_by_post.setdefault(row.post_id, row)
    items = []
    for post in posts:
        insight = insight_by_post.get(post.id)
        metrics = metrics_by_post.get(post.id)
        items.append({
            "id": post.id, "caption": post.content, "permalink": post.permalink,
            "published_at": post.published_at, "media_type": post.media_type,
            "media_product_type": post.media_product_type,
            "likes": metrics.likes if metrics else None,
            "comments": metrics.comments if metrics else None,
            "reach": insight.reach if insight else None,
            "saved": insight.saved if insight else None,
            "shares": insight.shares if insight else None,
            "views": insight.views if insight else None,
            "profile_visits": insight.profile_visits if insight else None,
            "profile_activity": insight.profile_activity if insight else None,
            "follows": insight.follows if insight else None,
            "unavailable_metrics": insight.unavailable_metrics if insight else [],
        })
    return items


def _follower_summary(rows: list[InstagramAccountSnapshot]) -> dict[str, Any]:
    if not rows:
        return _empty_followers()
    latest = rows[-1]
    prior_date = latest.snapshot_date - timedelta(days=1)
    prior = next((row for row in reversed(rows[:-1]) if row.snapshot_date == prior_date), None)

    def delta(days: int):
        target = latest.snapshot_date - timedelta(days=days)
        candidates = [row for row in rows if row.snapshot_date <= target]
        return latest.followers_count - candidates[-1].followers_count if candidates else None

    return {
        "current": latest.followers_count,
        "previous_day": latest.followers_count - prior.followers_count if prior else None,
        "seven_days": delta(7),
        "thirty_days": delta(30),
        "series": [{
            "date": row.snapshot_date,
            "followers": row.followers_count,
            "media_count": row.media_count,
        } for row in rows[-90:]],
    }


def _empty_followers() -> dict[str, Any]:
    return {"current": None, "previous_day": None, "seven_days": None,
            "thirty_days": None, "series": []}


def _by_media_type(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        label = str(item.get("media_product_type") or item.get("media_type") or "UNKNOWN")
        groups.setdefault(label, []).append(item)
    result = []
    for label, rows in groups.items():
        summary: dict[str, Any] = {"media_type": label, "posts": len(rows)}
        for metric in (
            "likes", "comments", "reach", "saved", "shares",
            "profile_visits", "profile_activity", "follows",
        ):
            values = [int(row[metric]) for row in rows if row.get(metric) is not None]
            summary[metric] = sum(values) if values else None
        result.append(summary)
    return sorted(result, key=lambda row: (-row["posts"], row["media_type"]))


def _funnel_summary(items: list[dict[str, Any]],
                    account: InstagramAccountSnapshot | None) -> dict[str, Any]:
    def total(metric: str) -> int:
        return sum(int(item.get(metric) or 0) for item in items)

    reach = total("reach")
    visits = total("profile_visits")
    activity = total("profile_activity")
    follows = total("follows")
    link_taps = account.profile_links_taps_30d if account else None
    supported_posts = sum(1 for item in items if item.get("profile_visits") is not None)
    return {
        "reach": reach,
        "profile_visits": visits,
        "profile_activity": activity,
        "follows": follows,
        "profile_links_taps_30d": link_taps,
        "feed_posts_with_funnel_metrics": supported_posts,
        "profile_visit_rate": round(visits / reach, 4) if reach else None,
        "note": (
            "フィード投稿は投稿別プロフィール遷移、リンクタップはアカウント全体の直近30日です。"
            "同一利用者を投稿から予約まで追跡する値ではありません。"
        ),
    }


def _empty_funnel() -> dict[str, Any]:
    return {
        "reach": 0, "profile_visits": 0, "profile_activity": 0,
        "follows": 0, "profile_links_taps_30d": None,
        "feed_posts_with_funnel_metrics": 0, "profile_visit_rate": None,
        "note": "同期後に表示されます。",
    }


def _audience_summary(account: InstagramAccountSnapshot | None) -> dict[str, Any]:
    if not account:
        return _empty_audience()
    return {
        "accounts_engaged_this_month": account.accounts_engaged_this_month,
        "reach_this_month": account.reach_this_month,
        "follower": account.follower_demographics or {},
        "engaged": account.engaged_audience_demographics or {},
        "reached": account.reached_audience_demographics or {},
        "unavailable_metrics": account.account_insights_unavailable or [],
        "metric_errors": account.account_insight_errors or {},
        "captured_at": account.captured_at,
        "privacy_note": (
            "Metaが提供する集計値のみを表示します。個人名や閲覧者一覧、"
            "通常投稿ごとの年齢・性別は取得できません。"
        ),
    }


def _empty_audience() -> dict[str, Any]:
    return {
        "accounts_engaged_this_month": None, "reach_this_month": None,
        "follower": {}, "engaged": {}, "reached": {},
        "unavailable_metrics": [], "metric_errors": {}, "captured_at": None,
        "privacy_note": "同期後にMetaの集計オーディエンスを表示します。",
    }


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
    except ValueError:
        return None


def _user_message(category: str) -> str:
    return {
        "not_configured": "Meta API認証情報が未設定です",
        "expired": "Meta接続の有効期限が切れています。再認証してください",
        "revoked": "Meta接続が無効です。再認証してください",
        "permission_error": "Meta APIの読取権限が不足しています",
        "instagram_not_linked": "FacebookページにInstagramプロアカウントが接続されていません",
        "connection_mismatch": "設定したInstagramとFacebookページの接続先が一致しません",
        "app_mismatch": "アクセストークンの発行元アプリが設定と一致しません",
        "rate_limit": "Meta APIの利用上限に達しました。時間を置いて再実行してください",
        "timeout": "Meta APIがタイムアウトしました",
        "network_error": "Meta APIへ接続できませんでした",
    }.get(category, "Meta APIからデータを取得できませんでした")
