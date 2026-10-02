from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    InstagramAccountSnapshot,
    InstagramMediaInsightSnapshot,
    MetaConnection,
    SnsMetricSnapshot,
    SnsPost,
    SnsSyncRun,
)
from app.config import Settings
from app.services.instagram_analytics import (
    REQUIRED_SCOPES,
    _account_snapshot,
    _by_media_type,
    _follower_summary,
    _post_and_snapshots,
    sync_instagram_analytics,
)


def analytics_session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (
        MetaConnection,
        InstagramAccountSnapshot,
        SnsPost,
        SnsMetricSnapshot,
        InstagramMediaInsightSnapshot,
        SnsSyncRun,
    ):
        model.__table__.create(engine)
    return Session(engine)


def test_daily_account_and_media_snapshots_are_idempotent():
    db = analytics_session()
    measured_at = datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)
    connection = MetaConnection(workspace_key="demo", instagram_account_id="ig-1")
    db.add(connection)
    db.flush()

    _account_snapshot(db, connection, {
        "id": "ig-1", "followers_count": 100, "media_count": 10,
    }, measured_at)
    _account_snapshot(db, connection, {
        "id": "ig-1", "followers_count": 101, "media_count": 11,
    }, measured_at)
    media = {
        "id": "post-1", "caption": "test", "permalink": "https://example.test/post-1",
        "media_type": "IMAGE", "media_product_type": "FEED",
        "timestamp": "2026-09-25T00:00:00+00:00",
        "like_count": 5, "comments_count": 2,
    }
    _post_and_snapshots(db, "demo", media, {
        "values": {"reach": 80, "saved": 4, "shares": 1},
        "raw": {}, "unavailable": [], "errors": {},
    }, measured_at)
    _post_and_snapshots(db, "demo", media, {
        "values": {"reach": 90, "shares": 2},
        "raw": {}, "unavailable": ["saved"], "errors": {},
    }, measured_at)
    db.commit()

    assert db.scalar(select(func.count()).select_from(InstagramAccountSnapshot)) == 1
    account = db.scalar(select(InstagramAccountSnapshot))
    assert account.followers_count == 101
    assert account.media_count == 11
    assert db.scalar(select(func.count()).select_from(SnsPost)) == 1
    assert db.scalar(select(func.count()).select_from(SnsMetricSnapshot)) == 1
    assert db.scalar(select(func.count()).select_from(InstagramMediaInsightSnapshot)) == 1
    history = db.scalar(select(InstagramMediaInsightSnapshot))
    assert history.reach == 90
    assert history.saved is None
    assert history.unavailable_metrics == ["saved"]
    db.close()


class FakeMetaClient:
    insight_errors = {}

    def __init__(self, _settings):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def validate_token(self):
        return {
            "is_valid": True,
            "app_id": "",
            "scopes": list(REQUIRED_SCOPES),
            "expires_at": datetime(2026, 11, 1, tzinfo=timezone.utc),
            "data_access_expires_at": datetime(2026, 12, 1, tzinfo=timezone.utc),
            "status": "valid",
        }

    def get_facebook_page(self, _page_id):
        return ({
            "id": "page-1", "name": "Demo",
            "instagram_business_account": {"id": "ig-1"},
        }, "page-token")

    def get_instagram_profile(self, _account_id, *, token):
        assert token == "page-token"
        return {
            "id": "ig-1", "username": "estheticdemo", "name": "デモサロン",
            "followers_count": 101, "media_count": 4,
        }

    def get_instagram_account_insights(self, _account_id, *, token, days):
        assert token == "page-token"
        assert days == 30
        return {
            "profile_links_taps": 7,
            "accounts_engaged_this_month": 120,
            "reach_this_month": 1085,
            "demographics": {
                "follower_demographics": {
                    "age": {"35-44": 40, "45-54": 30},
                    "gender": {"F": 60, "M": 10},
                },
                "engaged_audience_demographics": {"age": {"35-44": 20}},
                "reached_audience_demographics": {"age": {"35-44": 50}},
            },
            "unavailable": [],
            "errors": {},
        }

    def get_instagram_media(self, _account_id, *, token, limit):
        assert token == "page-token"
        assert limit > 0
        return [{
            "id": "post-1", "caption": "test", "permalink": "https://example.test/post-1",
            "media_type": "IMAGE", "media_product_type": "FEED",
            "timestamp": "2026-09-25T00:00:00+00:00",
            "like_count": 5, "comments_count": 2,
        }]

    def get_instagram_media_insights(self, _media, *, token):
        assert token == "page-token"
        return {
            "values": {
                "reach": 90, "saved": 3, "shares": 2,
                "profile_visits": 6, "profile_activity": 4, "follows": 2,
            },
            "raw": {}, "unavailable": [], "errors": dict(self.insight_errors),
        }


def sync_settings():
    return Settings(
        _env_file=None,
        meta_workspace_key="demo",
        meta_access_token="token",
        meta_page_id="page-1",
        meta_instagram_account_id="ig-1",
    )


def test_sync_completes_and_saves_profile_media_and_insights(monkeypatch):
    db = analytics_session()
    monkeypatch.setattr("app.services.instagram_analytics.MetaGraphClient", FakeMetaClient)

    result = sync_instagram_analytics(db, sync_settings(), "demo")

    assert result["status"] == "success"
    assert result["profile_synced"] is True
    assert result["media_found"] == 1
    assert result["insights_synced"] == 1
    connection = db.scalar(select(MetaConnection))
    assert connection.facebook_page_name == "Demo"
    assert connection.instagram_username == "estheticdemo"
    assert connection.instagram_name == "デモサロン"
    assert db.scalar(select(func.count()).select_from(SnsPost)) == 1
    assert db.scalar(select(func.count()).select_from(InstagramMediaInsightSnapshot)) == 1
    account = db.scalar(select(InstagramAccountSnapshot))
    assert account.profile_links_taps_30d == 7
    assert account.accounts_engaged_this_month == 120
    assert account.reach_this_month == 1085
    assert account.follower_demographics["age"]["35-44"] == 40
    insight = db.scalar(select(InstagramMediaInsightSnapshot))
    assert insight.profile_visits == 6
    assert insight.follows == 2
    metric = db.scalar(select(SnsMetricSnapshot))
    assert metric.profile_visits == 6
    run = db.scalar(select(SnsSyncRun))
    assert run.status == "success"
    db.close()


def test_sync_records_partial_success_without_losing_post(monkeypatch):
    class PartialMetaClient(FakeMetaClient):
        insight_errors = {"saved": "timeout"}

    db = analytics_session()
    monkeypatch.setattr("app.services.instagram_analytics.MetaGraphClient", PartialMetaClient)

    result = sync_instagram_analytics(db, sync_settings(), "demo")

    assert result["status"] == "partial_success"
    assert result["errors"] == 1
    assert db.scalar(select(func.count()).select_from(SnsPost)) == 1
    run = db.scalar(select(SnsSyncRun))
    assert run.error_summary == {"timeout": 1}
    db.close()


def test_follower_summary_uses_prior_daily_snapshots():
    rows = [
        InstagramAccountSnapshot(
            connection_id="c", instagram_account_id="ig", snapshot_date=datetime(2026, 8, 27).date(),
            followers_count=70, media_count=1,
        ),
        InstagramAccountSnapshot(
            connection_id="c", instagram_account_id="ig", snapshot_date=datetime(2026, 9, 19).date(),
            followers_count=90, media_count=2,
        ),
        InstagramAccountSnapshot(
            connection_id="c", instagram_account_id="ig", snapshot_date=datetime(2026, 9, 25).date(),
            followers_count=99, media_count=3,
        ),
        InstagramAccountSnapshot(
            connection_id="c", instagram_account_id="ig", snapshot_date=datetime(2026, 9, 26).date(),
            followers_count=101, media_count=4,
        ),
    ]

    result = _follower_summary(rows)

    assert result["current"] == 101
    assert result["previous_day"] == 2
    assert result["seven_days"] == 11
    assert result["thirty_days"] == 31
    assert len(result["series"]) == 4
    assert result["series"][-1]["media_count"] == 4


def test_media_type_summary_preserves_unavailable_metrics():
    result = _by_media_type([
        {"media_product_type": "REELS", "media_type": "VIDEO", "likes": 4, "comments": 1,
         "reach": 100, "saved": None, "shares": 2},
        {"media_product_type": "REELS", "media_type": "VIDEO", "likes": 3, "comments": 0,
         "reach": 80, "saved": None, "shares": 1},
    ])

    assert result == [{
        "media_type": "REELS", "posts": 2, "likes": 7, "comments": 1,
        "reach": 180, "saved": None, "shares": 3,
        "profile_visits": None, "profile_activity": None, "follows": None,
    }]
