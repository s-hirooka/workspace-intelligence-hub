"""SNS performance collection without OpenAI analysis."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import (
    InstagramMediaInsightSnapshot,
    MetaConnection,
    SnsMetricSnapshot,
    SnsPost,
    SnsSyncRun,
)
from app.services.instagram_analytics import connection_status, meta_configured, sync_instagram_analytics
from app.services.meta_config import MetaWorkspaceConfig, resolve_meta_config

logger = logging.getLogger(__name__)
PLATFORMS = {"youtube", "facebook", "instagram", "tiktok"}
METRIC_FIELDS = (
    "impressions", "reach", "views", "clicks", "profile_visits", "link_clicks",
    "inquiries", "reservations", "likes", "comments", "shares", "saves",
    "watch_time_seconds",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any, *, integer: bool = True) -> int | float:
    if value in (None, ""):
        return 0
    cleaned = str(value).strip().replace(",", "").replace("%", "")
    try:
        number = float(cleaned)
        return int(number) if integer else number
    except (TypeError, ValueError):
        return 0


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            parsed = datetime.fromisoformat(candidate)
            return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)
        except ValueError:
            pass
    for fmt in ("%Y/%m/%d %H:%M:%S", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _upsert(db: Session, payload: dict[str, Any], workspace_key: str = "workspace") -> str:
    platform = payload["platform"]
    external_id = str(payload["external_id"])
    post = db.scalar(select(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == platform,
        SnsPost.external_id == external_id,
    ))
    action = "updated"
    if not post:
        post = SnsPost(workspace_key=workspace_key, platform=platform, external_id=external_id)
        db.add(post)
        db.flush()
        action = "added"
    for field in ("account_name", "content", "permalink", "media_type", "thumbnail_url", "published_at"):
        if field in payload and payload[field] is not None:
            setattr(post, field, payload[field])
    post.synced_at = _now()
    measured_at = _now()
    snapshot = db.scalar(select(SnsMetricSnapshot).where(
        SnsMetricSnapshot.post_id == post.id,
        SnsMetricSnapshot.measured_date == measured_at.date(),
    ))
    if not snapshot:
        snapshot = SnsMetricSnapshot(post_id=post.id, measured_date=measured_at.date())
        db.add(snapshot)
    snapshot.measured_at = measured_at
    metrics = payload.get("metrics", {})
    for field in METRIC_FIELDS:
        setattr(snapshot, field, int(_number(metrics.get(field))))
    snapshot.ctr = float(_number(metrics.get("ctr"), integer=False))
    snapshot.raw_metrics = metrics.get("raw", {})
    db.flush()
    return action


def _sync_run(db: Session, platform: str, collector: Callable[[], list[dict[str, Any]]],
              workspace_key: str = "workspace") -> dict:
    run = SnsSyncRun(workspace_key=workspace_key, platform=platform)
    db.add(run)
    db.commit()
    try:
        rows = collector()
        for row in rows:
            action = _upsert(db, row, workspace_key)
            if action == "added":
                run.added += 1
            else:
                run.updated += 1
        run.status = "completed"
        run.message = f"{len(rows)}件を取得しました"
    except Exception as exc:
        # httpx exceptions can include the full request URL. Meta access tokens
        # are passed as query parameters, so logging the exception itself would
        # leak the token into application logs.
        logger.error("SNS sync failed platform=%s error_type=%s", platform, type(exc).__name__)
        db.rollback()
        run = db.get(SnsSyncRun, run.id)
        run.status = "error"
        run.errors = 1
        run.message = f"取得できませんでした（{type(exc).__name__}）"
    run.finished_at = _now()
    db.add(run)
    db.commit()
    return {"platform": platform, "status": run.status, "added": run.added, "updated": run.updated,
            "errors": run.errors, "message": run.message}


def _get_pages(client: httpx.Client, url: str, params: dict, limit: int) -> list[dict]:
    result: list[dict] = []
    request_params = dict(params)
    while url and len(result) < limit:
        response = client.get(url, params=request_params)
        response.raise_for_status()
        body = response.json()
        result.extend(body.get("data", body.get("items", [])))
        paging = body.get("paging", {})
        after = paging.get("cursors", {}).get("after")
        if not paging.get("next") or not after:
            break
        # Rebuild Meta pagination with the original token and returned cursor.
        # Following `next` verbatim can fail with `Provide valid app ID`.
        request_params = {**params, "after": after}
    return result[:limit]


def _youtube_token(settings: Settings, client: httpx.Client) -> str:
    response = client.post("https://oauth2.googleapis.com/token", data={
        "client_id": settings.youtube_client_id,
        "client_secret": settings.youtube_client_secret,
        "refresh_token": settings.youtube_refresh_token,
        "grant_type": "refresh_token",
    })
    response.raise_for_status()
    return response.json()["access_token"]


def _youtube_rows(settings: Settings) -> list[dict[str, Any]]:
    with httpx.Client(timeout=45) as client:
        token = _youtube_token(settings, client)
        headers = {"Authorization": f"Bearer {token}"}
        channel_params = {"part": "id,snippet,contentDetails", "mine": "true"}
        if settings.youtube_channel_id:
            channel_params = {"part": "id,snippet,contentDetails", "id": settings.youtube_channel_id}
        response = client.get("https://www.googleapis.com/youtube/v3/channels", params=channel_params, headers=headers)
        response.raise_for_status()
        channel = response.json().get("items", [])[0]
        account = channel.get("snippet", {}).get("title", "")
        playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        items: list[dict] = []
        page_token = None
        while len(items) < settings.sns_max_posts_per_platform:
            params = {"part": "snippet,contentDetails", "playlistId": playlist, "maxResults": 50}
            if page_token:
                params["pageToken"] = page_token
            response = client.get("https://www.googleapis.com/youtube/v3/playlistItems", params=params, headers=headers)
            response.raise_for_status()
            body = response.json()
            items.extend(body.get("items", []))
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        ids = [item.get("contentDetails", {}).get("videoId") for item in items if item.get("contentDetails", {}).get("videoId")]
        statistics: dict[str, dict] = {}
        for start in range(0, len(ids), 50):
            response = client.get("https://www.googleapis.com/youtube/v3/videos", params={
                "part": "statistics", "id": ",".join(ids[start:start + 50]),
            }, headers=headers)
            response.raise_for_status()
            statistics.update({row["id"]: row.get("statistics", {}) for row in response.json().get("items", [])})
        analytics: dict[str, dict] = {}
        end = date.today() - timedelta(days=1)
        if ids and end >= date(2005, 1, 1):
            try:
                response = client.get("https://youtubeanalytics.googleapis.com/v2/reports", params={
                    "ids": "channel==MINE", "startDate": "2005-01-01", "endDate": end.isoformat(),
                    "metrics": "views,likes,comments,shares,estimatedMinutesWatched,averageViewDuration",
                    "dimensions": "video", "maxResults": 200,
                }, headers=headers)
                response.raise_for_status()
                body = response.json()
                names = [h["name"] for h in body.get("columnHeaders", [])]
                for values in body.get("rows", []):
                    record = dict(zip(names, values))
                    analytics[str(record.get("video"))] = record
            except httpx.HTTPError:
                logger.info("YouTube Analytics metrics are unavailable; Data API values are retained")
        rows = []
        for item in items[:settings.sns_max_posts_per_platform]:
            snippet = item.get("snippet", {})
            video_id = item.get("contentDetails", {}).get("videoId", "")
            stats = statistics.get(video_id, {})
            extra = analytics.get(video_id, {})
            thumbs = snippet.get("thumbnails", {})
            thumbnail = (thumbs.get("high") or thumbs.get("medium") or thumbs.get("default") or {}).get("url", "")
            rows.append({"platform": "youtube", "external_id": video_id, "account_name": account,
                         "content": "\n".join(x for x in (snippet.get("title", ""), snippet.get("description", "")) if x),
                         "permalink": f"https://www.youtube.com/watch?v={video_id}", "media_type": "video",
                         "thumbnail_url": thumbnail, "published_at": _datetime(snippet.get("publishedAt")),
                         "metrics": {"views": stats.get("viewCount", extra.get("views", 0)),
                                     "likes": stats.get("likeCount", extra.get("likes", 0)),
                                     "comments": stats.get("commentCount", extra.get("comments", 0)),
                                     "shares": extra.get("shares", 0),
                                     "watch_time_seconds": int(_number(extra.get("estimatedMinutesWatched"))) * 60,
                                     "raw": {"youtube_statistics": stats, "youtube_analytics": extra}}})
        return rows


def _meta_insights(client: httpx.Client, base: str, object_id: str, token: str, candidates: dict[str, list[str]]) -> tuple[dict, list[str]]:
    values: dict[str, Any] = {}
    unavailable: list[str] = []
    resolved: set[str] = set()

    # Meta accepts multiple compatible metrics in one request. This keeps a
    # 200-post sync from issuing roughly five requests per post. If a media
    # type rejects the combined set, the individual fallback below preserves
    # compatibility.
    preferred = {metric_names[0]: target for target, metric_names in candidates.items() if metric_names}
    if preferred:
        response = client.get(
            f"{base}/{object_id}/insights",
            params={"metric": ",".join(preferred), "access_token": token},
        )
        if response.status_code < 400:
            for metric in response.json().get("data", []):
                target = preferred.get(metric.get("name"))
                metric_values = metric.get("values", [])
                if target and metric_values:
                    values[target] = metric_values[-1].get("value", 0)
                    resolved.add(target)

    for target, metric_names in candidates.items():
        if target in resolved:
            continue
        for name in metric_names:
            response = client.get(f"{base}/{object_id}/insights", params={"metric": name, "access_token": token})
            if response.status_code >= 400:
                unavailable.append(name)
                continue
            data = response.json().get("data", [])
            if data and data[0].get("values"):
                values[target] = data[0]["values"][-1].get("value", 0)
                break
    return values, unavailable


def _facebook_rows(settings: Settings | MetaWorkspaceConfig) -> list[dict[str, Any]]:
    base = f"https://graph.facebook.com/{settings.meta_graph_version}"
    user_token = settings.meta_access_token
    with httpx.Client(timeout=45) as client:
        page = client.get(
            f"{base}/{settings.meta_page_id}",
            params={"fields": "name,access_token", "access_token": user_token},
        )
        page.raise_for_status()
        page_data = page.json()
        account = page_data.get("name", "")
        # The token configured for Instagram is a User access token. Facebook
        # post and post-insight edges require the Page access token derived from
        # that User token, even though basic Page fields accept the User token.
        page_token = page_data.get("access_token") or user_token
        posts = _get_pages(client, f"{base}/{settings.meta_page_id}/posts", {
            "fields": "id,message,created_time,permalink_url,full_picture,reactions.limit(0).summary(true),comments.limit(0).summary(true),shares",
            "limit": 25, "access_token": page_token,
        }, settings.sns_max_posts_per_platform)
        rows = []
        for post in posts:
            insight, unavailable = _meta_insights(client, base, post["id"], page_token, {
                "impressions": ["post_media_view"],
                "clicks": ["post_clicks"],
            })
            rows.append({"platform": "facebook", "external_id": post["id"], "account_name": account,
                         "content": post.get("message", ""), "permalink": post.get("permalink_url", ""),
                         "media_type": "post", "thumbnail_url": post.get("full_picture", ""),
                         "published_at": _datetime(post.get("created_time")),
                         "metrics": {**insight,
                                     "likes": post.get("reactions", {}).get("summary", {}).get("total_count", 0),
                                     "comments": post.get("comments", {}).get("summary", {}).get("total_count", 0),
                                     "shares": post.get("shares", {}).get("count", 0),
                                     "raw": {"unavailable_metrics": unavailable}}})
        return rows


def _instagram_rows(settings: Settings) -> list[dict[str, Any]]:
    base = f"https://graph.facebook.com/{settings.meta_graph_version}"
    token = settings.meta_access_token
    with httpx.Client(timeout=45) as client:
        account_response = client.get(f"{base}/{settings.meta_instagram_account_id}", params={"fields": "username", "access_token": token})
        account_response.raise_for_status()
        account = account_response.json().get("username", "")
        media = _get_pages(client, f"{base}/{settings.meta_instagram_account_id}/media", {
            "fields": "id,caption,media_type,permalink,timestamp,thumbnail_url,media_url,like_count,comments_count",
            "limit": 100, "access_token": token,
        }, settings.sns_max_posts_per_platform)
        rows = []
        for item in media:
            insight, unavailable = _meta_insights(client, base, item["id"], token, {
                "views": ["views", "plays"], "reach": ["reach"],
                "shares": ["shares"], "saves": ["saved"],
            })
            rows.append({"platform": "instagram", "external_id": item["id"], "account_name": account,
                         "content": item.get("caption", ""), "permalink": item.get("permalink", ""),
                         "media_type": item.get("media_type", ""),
                         "thumbnail_url": item.get("thumbnail_url") or item.get("media_url", ""),
                         "published_at": _datetime(item.get("timestamp")),
                         "metrics": {**insight, "likes": item.get("like_count", 0),
                                     "comments": item.get("comments_count", 0),
                                     "raw": {"unavailable_metrics": unavailable}}})
        return rows


def configuration_status(settings: Settings, workspace_key: str = "workspace") -> dict[str, Any]:
    from app.services.ga4_config import resolve_ga4_config
    from app.services.gbp_config import resolve_gbp_config

    return {
        "youtube": bool(
            workspace_key == settings.youtube_workspace_key
            and settings.youtube_client_id and settings.youtube_client_secret and settings.youtube_refresh_token
        ),
        "facebook": meta_configured(settings, workspace_key),
        "instagram": meta_configured(settings, workspace_key),
        "tiktok": "csv_until_api_approval",
        "openai_analysis_enabled": bool(settings.sns_openai_analysis_enabled),
        "ga4": resolve_ga4_config(settings, workspace_key).configured,
        "google_business_profile": resolve_gbp_config(settings, workspace_key).configured,
    }


def sync_all(db: Session, settings: Settings, workspace_key: str = "workspace") -> list[dict]:
    configured = configuration_status(settings, workspace_key)
    results = []
    if configured["youtube"]:
        results.append(_sync_run(db, "youtube", lambda: _youtube_rows(settings), workspace_key))
    else:
        results.append({"platform": "youtube", "status": "not_configured", "added": 0, "updated": 0,
                        "errors": 0, "message": "API認証情報が未設定です"})
    if configured["instagram"]:
        meta_config = resolve_meta_config(settings, workspace_key)
        results.append(_sync_run(
            db,
            "facebook",
            lambda: _facebook_rows(meta_config),
            workspace_key,
        ))
        instagram = sync_instagram_analytics(db, settings, workspace_key)
        results.append(instagram)
    else:
        for platform in ("facebook", "instagram"):
            results.append({"platform": platform, "status": "not_configured", "added": 0, "updated": 0,
                            "errors": 0, "message": "この顧客のMeta API認証情報が未設定です"})
    return results


def _header(value: str) -> str:
    return re.sub(r"[\s_\-（）()]+", "", (value or "").strip().lower())


CSV_ALIASES = {
    "external_id": ["videoid", "postid", "id", "動画id", "投稿id"],
    "content": ["caption", "description", "title", "posttext", "投稿内容", "キャプション", "タイトル"],
    "permalink": ["permalink", "videourl", "posturl", "url", "動画url", "投稿url"],
    "published_at": ["date", "posttime", "createdtime", "投稿日", "投稿日時", "作成日時"],
    "views": ["views", "viewcount", "videoviews", "再生数", "動画視聴数"],
    "impressions": ["impressions", "インプレッション", "表示回数"],
    "reach": ["reach", "リーチ"], "clicks": ["clicks", "クリック数"],
    "profile_visits": ["profilevisits", "プロフィールアクセス", "プロフィール訪問"],
    "link_clicks": ["linkclicks", "リンククリック", "urlクリック"],
    "inquiries": ["inquiries", "問い合わせ", "問合せ"],
    "reservations": ["reservations", "予約", "予約数"],
    "likes": ["likes", "いいね", "いいね数"], "comments": ["comments", "コメント", "コメント数"],
    "shares": ["shares", "シェア", "シェア数"], "saves": ["saves", "保存", "保存数"],
}


def parse_tiktok_csv(payload: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    text = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            text = payload.decode(encoding)
            break
        except UnicodeDecodeError:
            pass
    if text is None:
        raise ValueError("CSVはUTF-8またはShift_JISで保存してください")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("CSVの見出し行がありません")
    normalized = {_header(name): name for name in reader.fieldnames}
    mapping = {field: next((normalized[_header(alias)] for alias in aliases if _header(alias) in normalized), None)
               for field, aliases in CSV_ALIASES.items()}
    if not any(mapping.get(field) for field in ("external_id", "content", "permalink")):
        raise ValueError("投稿ID、投稿内容、投稿URLのいずれかの列が必要です")
    rows = []
    for source in reader:
        def value(field: str) -> str:
            key = mapping.get(field)
            return (source.get(key, "") if key else "").strip()
        external_id = value("external_id")
        seed = value("permalink") or f"{value('content')}|{value('published_at')}"
        external_id = external_id or hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        metrics = {field: _number(value(field)) for field in METRIC_FIELDS if field in mapping}
        rows.append({"platform": "tiktok", "external_id": external_id, "content": value("content"),
                     "permalink": value("permalink"), "media_type": "video",
                     "published_at": _datetime(value("published_at")), "metrics": metrics})
    recognized = [field for field, source in mapping.items() if source]
    return rows, recognized


def import_tiktok_csv(db: Session, payload: bytes, workspace_key: str = "workspace") -> dict:
    rows, recognized = parse_tiktok_csv(payload)
    run = SnsSyncRun(workspace_key=workspace_key, platform="tiktok")
    db.add(run)
    db.flush()
    for row in rows:
        if _upsert(db, row, workspace_key) == "added":
            run.added += 1
        else:
            run.updated += 1
    run.status = "completed"
    run.finished_at = _now()
    run.message = f"CSVから{len(rows)}件を読み込みました"
    db.commit()
    return {"status": run.status, "added": run.added, "updated": run.updated,
            "recognized_columns": recognized, "message": run.message}


def dashboard(db: Session, *, days: int = 30, platform: str = "all",
              workspace_key: str = "workspace") -> dict:
    cutoff = _now() - timedelta(days=max(1, min(days, 3660)))
    statement = select(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        (SnsPost.published_at >= cutoff) | (SnsPost.published_at.is_(None)),
    )
    if platform in PLATFORMS:
        statement = statement.where(SnsPost.platform == platform)
    posts = db.scalars(statement.order_by(SnsPost.published_at.desc().nullslast())).all()
    items = []
    totals = {field: 0 for field in METRIC_FIELDS}
    totals.update({"posts": len(posts), "interactions": 0})
    by_platform: dict[str, dict] = {}
    for post in posts:
        snapshots = db.scalars(select(SnsMetricSnapshot).where(SnsMetricSnapshot.post_id == post.id)
                              .order_by(SnsMetricSnapshot.measured_at.asc())).all()
        latest = snapshots[-1] if snapshots else None
        first = snapshots[0] if snapshots else None
        metrics = {field: int(getattr(latest, field, 0) or 0) for field in METRIC_FIELDS}
        delta = {field: metrics[field] - int(getattr(first, field, 0) or 0) for field in METRIC_FIELDS}
        metrics["ctr"] = float(getattr(latest, "ctr", 0) or 0)
        interactions = metrics["likes"] + metrics["comments"] + metrics["shares"] + metrics["saves"]
        for field in METRIC_FIELDS:
            totals[field] += metrics[field]
        totals["interactions"] += interactions
        group = by_platform.setdefault(post.platform, {field: 0 for field in METRIC_FIELDS})
        group.setdefault("posts", 0); group.setdefault("interactions", 0)
        group["posts"] += 1; group["interactions"] += interactions
        for field in METRIC_FIELDS:
            group[field] += metrics[field]
        items.append({"id": post.id, "platform": post.platform, "account_name": post.account_name,
                      "content": post.content, "permalink": post.permalink, "thumbnail_url": post.thumbnail_url,
                      "published_at": post.published_at, "metrics": metrics, "delta": delta,
                      "measured_at": latest.measured_at if latest else None})
    return {"workspace_key": workspace_key, "days": days, "platform": platform,
            "totals": totals, "by_platform": by_platform, "items": items}


RANKING_METRICS = {
    "published_at", "exposure", "clicks", "profile_visits", "follows",
    "likes", "comments", "saves", "shares",
}


def post_rankings(db: Session, *, workspace_key: str = "workspace",
                  platform: str = "instagram", days: int = 30,
                  ranking_metric: str = "exposure", limit: int = 50) -> dict:
    """Return a common ranking view for Instagram and Facebook posts."""
    if platform not in {"instagram", "facebook"}:
        raise ValueError("ランキング対象はInstagramまたはFacebookを指定してください")
    days = max(0, min(days, 3650))
    limit = max(1, min(limit, 200))
    ranking_metric = ranking_metric if ranking_metric in RANKING_METRICS else "exposure"
    statement = select(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == platform,
    )
    if days:
        statement = statement.where(SnsPost.published_at >= _now() - timedelta(days=days))
    posts = list(db.scalars(
        statement.order_by(SnsPost.published_at.desc().nullslast()).limit(200)
    ).all())
    ranking = []
    for post in posts:
        metric = db.scalars(select(SnsMetricSnapshot).where(
            SnsMetricSnapshot.post_id == post.id,
        ).order_by(SnsMetricSnapshot.measured_at.desc()).limit(1)).first()
        insight = None
        if platform == "instagram":
            insight = db.scalars(select(InstagramMediaInsightSnapshot).where(
                InstagramMediaInsightSnapshot.post_id == post.id,
            ).order_by(InstagramMediaInsightSnapshot.snapshot_at.desc()).limit(1)).first()
        values = {field: int(getattr(metric, field, 0) or 0) for field in METRIC_FIELDS}
        exposure = int(values["views"] or values["impressions"] or values["reach"])
        ranking.append({
            "id": post.id,
            "platform": post.platform,
            "caption": post.content,
            "permalink": post.permalink,
            "published_at": post.published_at,
            "media_type": post.media_type,
            "media_product_type": post.media_product_type,
            "exposure": exposure,
            "clicks": values["clicks"],
            "profile_visits": values["profile_visits"],
            "follows": int(insight.follows or 0) if insight else 0,
            "likes": values["likes"],
            "comments": values["comments"],
            "saves": values["saves"],
            "shares": values["shares"],
        })

    def ranking_value(item: dict[str, Any]):
        if ranking_metric == "published_at":
            published_at = item.get("published_at")
            return published_at.timestamp() if published_at else -1
        return int(item.get(ranking_metric, 0) or 0)

    ranking.sort(
        key=lambda item: (ranking_value(item), item["published_at"].timestamp() if item["published_at"] else -1),
        reverse=True,
    )
    ranking = ranking[:limit]
    groups: dict[str, dict[str, Any]] = {}
    for item in ranking:
        media_type = item["media_product_type"] or item["media_type"] or "投稿"
        group = groups.setdefault(media_type, {
            "media_type": media_type, "posts": 0, "exposure": 0, "clicks": 0,
            "profile_visits": 0, "follows": 0,
            "likes": 0, "comments": 0, "saves": 0, "shares": 0,
        })
        group["posts"] += 1
        for field in (
            "exposure", "clicks", "profile_visits", "follows",
            "likes", "comments", "saves", "shares",
        ):
            group[field] += int(item[field] or 0)
    return {
        "workspace_key": workspace_key,
        "platform": platform,
        "days": days,
        "ranking_metric": ranking_metric,
        "ranking": ranking,
        "by_media_type": sorted(groups.values(), key=lambda item: item["posts"], reverse=True),
    }


def facebook_account_dashboard(db: Session, settings: Settings,
                               workspace_key: str = "workspace") -> dict[str, Any]:
    """Return Facebook Page identity and workspace-scoped synchronization state."""
    status = connection_status(db, settings, workspace_key)
    connection = db.scalar(select(MetaConnection).where(
        MetaConnection.workspace_key == workspace_key,
    ))
    synced_posts = int(db.scalar(select(func.count()).select_from(SnsPost).where(
        SnsPost.workspace_key == workspace_key,
        SnsPost.platform == "facebook",
    )) or 0)
    latest_run = db.scalar(select(SnsSyncRun).where(
        SnsSyncRun.workspace_key == workspace_key,
        SnsSyncRun.platform == "facebook",
    ).order_by(SnsSyncRun.started_at.desc()).limit(1))
    return {
        "workspace_key": workspace_key,
        "connection": status,
        "account": {
            "page_id": connection.facebook_page_id if connection else status.get("facebook_page_id", ""),
            "page_name": connection.facebook_page_name if connection else status.get("facebook_page_name", ""),
            "synced_posts": synced_posts,
            "last_sync_at": latest_run.finished_at if latest_run else None,
        },
    }


def latest_runs(db: Session, workspace_key: str = "workspace") -> list[dict]:
    rows = db.scalars(select(SnsSyncRun).where(
        SnsSyncRun.workspace_key == workspace_key
    ).order_by(SnsSyncRun.started_at.desc()).limit(20)).all()
    return [{"platform": row.platform, "started_at": row.started_at, "finished_at": row.finished_at,
             "status": row.status, "added": row.added, "updated": row.updated,
             "errors": row.errors, "profile_synced": row.profile_synced,
             "media_found": row.media_found, "insights_synced": row.insights_synced,
             "error_summary": row.error_summary, "message": row.message} for row in rows]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def recommendations(db: Session, *, days: int = 30, platform: str = "all",
                    workspace_key: str = "workspace") -> dict:
    data = dashboard(db, days=days, platform=platform, workspace_key=workspace_key)
    totals = data["totals"]
    exposure = int(totals["views"] or totals["impressions"] or totals["reach"])
    interactions = int(totals["interactions"])
    visits = int(totals["profile_visits"])
    clicks = int(totals["link_clicks"] or totals["clicks"])
    inquiries = int(totals["inquiries"])
    reservations = int(totals["reservations"])
    funnel = {
        "exposure": exposure,
        "profile_visits": visits,
        "link_clicks": clicks,
        "inquiries": inquiries,
        "reservations": reservations,
        "interaction_rate": _rate(interactions, exposure),
        "visit_rate": _rate(visits, exposure),
        "click_rate": _rate(clicks, visits or exposure),
        "inquiry_rate": _rate(inquiries, clicks),
        "reservation_rate": _rate(reservations, inquiries or clicks),
    }
    advice = []

    def add(category: str, priority: str, title: str, evidence: str, action: str, kpi: str):
        advice.append({"category": category, "priority": priority, "title": title,
                       "evidence": evidence, "action": action, "kpi": kpi})

    if not data["items"]:
        add("measurement", "high", "SNS実績を登録する", "対象期間の投稿実績が0件です。",
            "API同期またはCSV取込を行い、投稿ID・投稿日・表示/再生・クリックを登録してください。",
            "実績を取得できた投稿数")
    else:
        top = max(data["items"], key=lambda item: (
            item["metrics"].get("views", 0) or item["metrics"].get("impressions", 0),
            item["metrics"].get("saves", 0) + item["metrics"].get("shares", 0),
        ))
        top_exposure = top["metrics"].get("views", 0) or top["metrics"].get("impressions", 0)
        add("awareness", "medium", "成果の高い投稿形式を再利用する",
            f"最多表示/再生の投稿は {top['platform']} の「{(top['content'] or '本文なし')[:60]}」で {top_exposure:,}件です。",
            "同じテーマ・冒頭構成・媒体形式で3本の派生投稿を作り、投稿ごとに比較してください。",
            "表示/再生、保存、シェア")
        interaction_rate = funnel["interaction_rate"]
        if interaction_rate is not None and interaction_rate < 0.02:
            add("awareness", "high", "保存・シェアされる内容へ改善する",
                f"表示/再生に対する反応率は {interaction_rate * 100:.1f}% です。",
                "冒頭で対象者と得られる結果を明示し、チェックリスト、事例、比較型の投稿を検証してください。",
                "反応率、保存率、シェア率")
        if exposure > 0 and visits == 0:
            add("measurement", "high", "プロフィール遷移を計測する",
                f"表示/再生は {exposure:,}件ありますが、プロフィール訪問が未登録です。",
                "媒体インサイトのプロフィール訪問をCSVへ追加し、投稿から次の行動へ進む割合を計測してください。",
                "プロフィール訪問率")
        if exposure > 0 and clicks == 0:
            add("conversion", "high", "投稿とプロフィールのCTAを一本化する",
                f"表示/再生は {exposure:,}件ありますが、リンククリックが0件または未計測です。",
                "投稿末尾・プロフィール・固定投稿で同じ予約導線を案内し、UTM付きリンクを設定してください。",
                "リンククリック率")
        if clicks > 0 and inquiries == 0:
            add("conversion", "high", "問い合わせ到達を計測してフォームを短くする",
                f"リンククリックは {clicks:,}件ですが、問い合わせが0件または未計測です。",
                "問い合わせ完了イベントを設定し、必須項目削減とスマートフォンでの入力確認を行ってください。",
                "クリック→問い合わせ率")
        if (inquiries > 0 or clicks > 0) and reservations == 0:
            add("conversion", "high", "予約完了をコンバージョンとして接続する",
                f"問い合わせ {inquiries:,}件、リンククリック {clicks:,}件に対し予約が0件または未計測です。",
                "予約完了ページまたは予約システムのイベントをSNS投稿ID・UTMと紐付けてください。",
                "予約数、問い合わせ→予約率")

    return {"workspace_key": workspace_key, "days": days, "platform": platform,
            "funnel": funnel, "recommendations": advice,
            "measurement_complete": all(totals[field] > 0 for field in (
                "profile_visits", "link_clicks", "inquiries", "reservations"
            ))}
