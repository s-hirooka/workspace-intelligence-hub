from datetime import date, datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.db.models import SnsMetricSnapshot, SnsPost
from app.services import sns as sns_service
from app.services.sns import (
    _facebook_rows,
    _get_pages,
    configuration_status,
    facebook_account_dashboard,
    parse_tiktok_csv,
    post_rankings,
)


def test_sns_openai_analysis_is_disabled_by_default():
    settings = Settings(_env_file=None)
    assert settings.sns_openai_analysis_enabled is False
    assert configuration_status(settings)["openai_analysis_enabled"] is False


def test_configuration_status_does_not_expose_secrets():
    settings = Settings(_env_file=None, youtube_client_id="id", youtube_client_secret="secret",
                        youtube_refresh_token="refresh", meta_access_token="token",
                        meta_page_id="page", meta_instagram_account_id="ig")
    status = configuration_status(settings)
    assert status["youtube"] is True
    assert status["facebook"] is True
    assert status["instagram"] is True
    assert "secret" not in str(status)
    assert "token" not in str(status)


def test_meta_configuration_is_scoped_to_one_workspace():
    settings = Settings(
        _env_file=None,
        meta_workspace_key="demo",
        meta_access_token="token",
        meta_page_id="page",
        meta_instagram_account_id="ig",
    )

    assert configuration_status(settings, "demo")["instagram"] is True
    assert configuration_status(settings, "workspace")["instagram"] is False
    assert configuration_status(settings, "workspace")["youtube"] is False


def test_youtube_and_meta_can_belong_to_different_workspaces():
    settings = Settings(
        _env_file=None,
        meta_workspace_key="demo",
        meta_access_token="token",
        meta_page_id="page",
        youtube_workspace_key="workspace",
        youtube_client_id="client",
        youtube_client_secret="secret",
        youtube_refresh_token="refresh",
    )

    assert configuration_status(settings, "demo")["instagram"] is True
    assert configuration_status(settings, "demo")["youtube"] is False
    assert configuration_status(settings, "workspace")["instagram"] is False
    assert configuration_status(settings, "workspace")["youtube"] is True


def test_tiktok_csv_accepts_japanese_headers():
    payload = ("投稿ID,投稿日時,投稿内容,投稿URL,再生数,インプレッション,リーチ,クリック数,いいね,コメント,シェア,保存\n"
               "v1,2026/09/17,デモ地域の物件紹介,https://example.test/v1,1200,1500,900,40,55,3,4,8\n").encode("utf-8-sig")
    rows, recognized = parse_tiktok_csv(payload)
    assert len(rows) == 1
    assert rows[0]["external_id"] == "v1"
    assert rows[0]["metrics"]["views"] == 1200
    assert rows[0]["metrics"]["clicks"] == 40
    assert {"external_id", "content", "views", "clicks"} <= set(recognized)


def test_tiktok_csv_generates_stable_id_without_post_id():
    payload = b"caption,url,views\nhello,https://example.test/post,10\n"
    first, _ = parse_tiktok_csv(payload)
    second, _ = parse_tiktok_csv(payload)
    assert first[0]["external_id"] == second[0]["external_id"]
    assert len(first[0]["external_id"]) == 32


def test_tiktok_csv_rejects_unusable_headers():
    try:
        parse_tiktok_csv("不明,列\n1,2\n".encode("utf-8"))
    except ValueError as exc:
        assert "投稿ID" in str(exc)
    else:
        raise AssertionError("ValueError was not raised")

def test_meta_pagination_reuses_original_token_with_cursor():
    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, params):
            self.calls.append((url, dict(params)))
            if len(self.calls) == 1:
                return Response({"data": [{"id": "1"}], "paging": {
                    "cursors": {"after": "cursor-1"},
                    "next": "https://example.test/next?access_token=bad",
                }})
            return Response({"data": [{"id": "2"}]})

    client = Client()
    rows = _get_pages(client, "https://example.test/media", {
        "fields": "id", "limit": 100, "access_token": "secret-token",
    }, 200)

    assert [row["id"] for row in rows] == ["1", "2"]
    assert client.calls[1][0] == "https://example.test/media"
    assert client.calls[1][1]["access_token"] == "secret-token"
    assert client.calls[1][1]["after"] == "cursor-1"


def test_facebook_collection_uses_derived_page_token(monkeypatch):
    class Response:
        def __init__(self, payload, status_code=200):
            self.payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            if self.status_code >= 400:
                raise AssertionError(f"unexpected HTTP status: {self.status_code}")

        def json(self):
            return self.payload

    class Client:
        def __init__(self, *args, **kwargs):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            self.calls.append((url, dict(params)))
            if url.endswith("/page-1"):
                assert params == {"fields": "name,access_token", "access_token": "user-token"}
                return Response({"id": "page-1", "name": "Test Page", "access_token": "page-token"})
            if url.endswith("/page-1/posts"):
                assert params["access_token"] == "page-token"
                return Response({"data": [{
                    "id": "post-1", "message": "hello", "created_time": "2026-09-25T00:00:00+0000",
                    "permalink_url": "https://example.test/post-1",
                }]})
            if url.endswith("/post-1/insights"):
                assert params["access_token"] == "page-token"
                assert params["metric"] == "post_media_view,post_clicks"
                return Response({"data": [
                    {"name": "post_media_view", "values": [{"value": 120}]},
                    {"name": "post_clicks", "values": [{"value": 8}]},
                ]})
            raise AssertionError(f"unexpected URL: {url}")

    client = Client()
    monkeypatch.setattr("app.services.sns.httpx.Client", lambda *args, **kwargs: client)
    settings = Settings(_env_file=None, meta_access_token="user-token", meta_page_id="page-1",
                        sns_max_posts_per_platform=1)

    rows = _facebook_rows(settings)

    assert len(rows) == 1
    assert rows[0]["account_name"] == "Test Page"
    assert rows[0]["metrics"]["impressions"] == 120
    assert rows[0]["metrics"]["clicks"] == 8
    assert all(call[1]["access_token"] == "page-token" for call in client.calls[1:])


def test_sync_all_collects_facebook_posts_for_selected_workspace(monkeypatch):
    settings = Settings(
        _env_file=None,
        meta_workspace_key="demo",
        meta_access_token="user-token",
        meta_page_id="demo-page",
        meta_instagram_account_id="demo-instagram",
    )
    collected = {}

    def fake_facebook_rows(config):
        collected["page_id"] = config.meta_page_id
        return [{"external_id": "facebook-post-1"}]

    def fake_sync_run(_db, platform, collector, workspace_key):
        rows = collector()
        collected["platform"] = platform
        collected["workspace_key"] = workspace_key
        return {
            "platform": platform,
            "status": "success",
            "added": len(rows),
            "updated": 0,
            "errors": 0,
            "message": f"{platform} {len(rows)}件を同期しました",
        }

    monkeypatch.setattr(sns_service, "_facebook_rows", fake_facebook_rows)
    monkeypatch.setattr(sns_service, "_sync_run", fake_sync_run)
    monkeypatch.setattr(sns_service, "sync_instagram_analytics", lambda *_args: {
        "platform": "instagram", "status": "success", "added": 0, "updated": 1,
        "errors": 0, "message": "Instagram 1件を同期しました", "profile_synced": True,
    })

    result = sns_service.sync_all(object(), settings, "demo")

    assert collected == {
        "page_id": "demo-page",
        "platform": "facebook",
        "workspace_key": "demo",
    }
    assert result[1]["platform"] == "facebook"
    assert result[1]["added"] == 1


def test_post_rankings_support_facebook_and_published_date_order():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    SnsPost.__table__.create(engine)
    SnsMetricSnapshot.__table__.create(engine)
    db = Session(engine)
    now = datetime.now(timezone.utc)
    posts = [
        SnsPost(
            id="fb-old", workspace_key="demo", platform="facebook", external_id="fb-old",
            content="old", media_type="post", published_at=now - timedelta(days=2),
        ),
        SnsPost(
            id="fb-new", workspace_key="demo", platform="facebook", external_id="fb-new",
            content="new", media_type="post", published_at=now - timedelta(days=1),
        ),
        SnsPost(
            id="ig-newest", workspace_key="demo", platform="instagram", external_id="ig-newest",
            content="instagram", media_type="IMAGE", published_at=now,
        ),
        SnsPost(
            id="other-customer", workspace_key="workspace", platform="facebook", external_id="other",
            content="other", media_type="post", published_at=now,
        ),
    ]
    db.add_all(posts)
    db.add_all([
        SnsMetricSnapshot(
            post_id="fb-old", measured_at=now, measured_date=date.today(),
            impressions=500, clicks=20, likes=10,
        ),
        SnsMetricSnapshot(
            post_id="fb-new", measured_at=now, measured_date=date.today(),
            impressions=100, clicks=5, likes=4,
        ),
    ])
    db.commit()

    newest = post_rankings(
        db, workspace_key="demo", platform="facebook", days=30,
        ranking_metric="published_at",
    )
    most_exposure = post_rankings(
        db, workspace_key="demo", platform="facebook", days=30,
        ranking_metric="exposure",
    )

    assert [item["id"] for item in newest["ranking"]] == ["fb-new", "fb-old"]
    assert [item["id"] for item in most_exposure["ranking"]] == ["fb-old", "fb-new"]
    assert newest["platform"] == "facebook"
    assert newest["by_media_type"][0]["posts"] == 2
    db.close()


def test_facebook_account_dashboard_is_scoped_to_selected_workspace():
    from app.db.models import MetaConnection, SnsSyncRun

    engine = create_engine("sqlite+pysqlite:///:memory:")
    for model in (MetaConnection, SnsPost, SnsMetricSnapshot, SnsSyncRun):
        model.__table__.create(engine)
    db = Session(engine)
    finished_at = datetime.now(timezone.utc)
    db.add(MetaConnection(
        workspace_key="demo",
        facebook_page_id="demo-page",
        facebook_page_name="デモサロン",
        connection_status="active",
    ))
    db.add_all([
        SnsPost(workspace_key="demo", platform="facebook", external_id="demo-1"),
        SnsPost(workspace_key="demo", platform="facebook", external_id="demo-2"),
        SnsPost(workspace_key="workspace", platform="facebook", external_id="workspace-1"),
        SnsSyncRun(
            workspace_key="demo", platform="facebook", status="completed",
            finished_at=finished_at,
        ),
    ])
    db.commit()
    settings = Settings(
        _env_file=None,
        meta_workspace_key="demo",
        meta_access_token="token",
        meta_page_id="demo-page",
        meta_instagram_account_id="demo-instagram",
    )

    result = facebook_account_dashboard(db, settings, "demo")

    assert result["account"]["page_id"] == "demo-page"
    assert result["account"]["page_name"] == "デモサロン"
    assert result["account"]["synced_posts"] == 2
    assert result["account"]["last_sync_at"].replace(tzinfo=timezone.utc) == finished_at
    assert result["connection"]["status"] == "active"
    db.close()
