from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.config import Settings
from app.services.meta_graph import MetaApiError, MetaGraphClient, token_lifecycle


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self.payload


class QueueClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params, headers):
        self.calls.append((url, dict(params), dict(headers)))
        return self.responses.pop(0)


def meta_settings(**kwargs):
    options = dict(
        _env_file=None,
        meta_access_token="secret-user-token",
        meta_page_id="page-1",
        meta_instagram_account_id="ig-1",
        meta_request_max_retries=0,
    )
    options.update(kwargs)
    return Settings(**options)


def test_meta_requests_keep_token_in_authorization_header():
    client = QueueClient([
        FakeResponse({"id": "page-1", "name": "Page", "access_token": "page-token"}),
        FakeResponse({"id": "ig-1", "username": "salon", "followers_count": 12, "media_count": 3}),
    ])
    api = MetaGraphClient(meta_settings(), client=client)

    page, page_token = api.get_facebook_page("page-1")
    profile = api.get_instagram_profile("ig-1", token=page_token)

    assert page["name"] == "Page"
    assert profile["username"] == "salon"
    assert client.calls[0][2]["Authorization"] == "Bearer secret-user-token"
    assert client.calls[1][2]["Authorization"] == "Bearer page-token"
    assert all("access_token" not in params for _, params, _ in client.calls)


def test_token_debug_uses_app_credentials_only_in_authorization_header():
    expires_at = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp())
    client = QueueClient([
        FakeResponse({"data": {
            "is_valid": True, "app_id": "app-1", "scopes": ["instagram_basic"],
            "expires_at": expires_at,
        }})
    ])
    settings = meta_settings(meta_app_id="app-1", meta_app_secret="app-secret")

    token = MetaGraphClient(settings, client=client).validate_token()

    assert token["is_valid"] is True
    assert client.calls[0][2]["Authorization"] == "Bearer app-1|app-secret"
    assert client.calls[0][1]["input_token"] == "secret-user-token"


def test_meta_paging_is_bounded_when_cursor_repeats():
    client = QueueClient([
        FakeResponse({"data": [{"id": "1"}], "paging": {
            "cursors": {"after": "same"}, "next": "https://example.test/next",
        }}),
        FakeResponse({"data": [{"id": "2"}], "paging": {
            "cursors": {"after": "same"}, "next": "https://example.test/next",
        }}),
    ])
    api = MetaGraphClient(meta_settings(), client=client)

    rows = api.get_pages("ig-1/media", params={"fields": "id"}, token="page-token", limit=20)

    assert [row["id"] for row in rows] == ["1", "2"]
    assert len(client.calls) == 2
    assert client.calls[1][1]["after"] == "same"


def test_meta_paging_extracts_cursor_from_next_without_reusing_next_url():
    client = QueueClient([
        FakeResponse({"data": [{"id": "1"}], "paging": {
            "next": "https://graph.facebook.com/v26.0/media?after=cursor-2&access_token=do-not-use",
        }}),
        FakeResponse({"data": [{"id": "2"}]}),
    ])
    api = MetaGraphClient(meta_settings(), client=client)

    rows = api.get_pages("ig-1/media", params={"fields": "id"}, token="page-token", limit=20)

    assert [row["id"] for row in rows] == ["1", "2"]
    assert client.calls[1][0].endswith("/ig-1/media")
    assert client.calls[1][1] == {"fields": "id", "after": "cursor-2"}
    assert client.calls[1][2]["Authorization"] == "Bearer page-token"


def test_media_insights_preserve_unavailable_metrics_without_failing_post():
    client = QueueClient([
        FakeResponse({"data": [{"name": "reach", "values": [{"value": 80}]}]}),
        FakeResponse({"error": {"code": 100, "message": "Unsupported metric"}}, status_code=400),
        FakeResponse({"data": [{"name": "shares", "values": [{"value": 3}]}]}),
        FakeResponse({"data": [{"name": "views", "values": [{"value": 140}]}]}),
    ])
    api = MetaGraphClient(meta_settings(), client=client)

    result = api.get_instagram_media_insights(
        {"id": "post-1", "media_type": "VIDEO", "media_product_type": "REELS"},
        token="page-token",
    )

    assert result["values"] == {"reach": 80, "shares": 3, "views": 140}
    assert result["unavailable"] == ["saved"]
    assert result["errors"] == {}


@pytest.mark.parametrize(
    ("media", "expected_metrics"),
    [
        ({"id": "1", "media_type": "IMAGE", "media_product_type": "FEED"}, 6),
        ({"id": "2", "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED"}, 6),
        ({"id": "3", "media_type": "VIDEO", "media_product_type": "FEED"}, 7),
        ({"id": "4", "media_type": "IMAGE", "media_product_type": "REELS"}, 4),
    ],
)
def test_media_type_uses_supported_metric_set(media, expected_metrics):
    expected = ["reach", "saved", "shares"]
    if media["media_type"] == "VIDEO" or media["media_product_type"] == "REELS":
        expected.append("views")
    if media["media_product_type"] == "FEED":
        expected.extend(["profile_visits", "profile_activity", "follows"])
    client = QueueClient([FakeResponse({"data": [
        {"name": name, "values": [{"value": 1}]} for name in expected
    ]})])

    MetaGraphClient(meta_settings(), client=client).get_instagram_media_insights(
        media, token="page-token"
    )

    assert len(expected) == expected_metrics
    assert len(client.calls) == 1
    requested = client.calls[0][1]["metric"].split(",")
    assert ("views" in requested) is (
        media["media_type"] == "VIDEO" or media["media_product_type"] == "REELS"
    )
    assert ("profile_visits" in requested) is (media["media_product_type"] == "FEED")
    assert ("follows" in requested) is (media["media_product_type"] == "FEED")


def test_account_insights_normalize_profile_link_taps_and_demographics():
    demographic = {
        "data": [{
            "total_value": {"breakdowns": [{
                "dimension_keys": ["age"],
                "results": [
                    {"dimension_values": ["25-34"], "value": 12},
                    {"dimension_values": ["35-44"], "value": 8},
                ],
            }]},
        }]
    }
    client = QueueClient([
        FakeResponse({"data": [{"total_value": {"value": 9}}]}),
        FakeResponse({"data": [
            {"name": "accounts_engaged", "total_value": {"value": 120}},
            {"name": "reach", "total_value": {"value": 1085}},
        ]}),
        *[FakeResponse(demographic) for _ in range(12)],
    ])

    result = MetaGraphClient(meta_settings(), client=client).get_instagram_account_insights(
        "ig-1", token="page-token", days=30
    )

    assert result["profile_links_taps"] == 9
    assert result["accounts_engaged_this_month"] == 120
    assert result["reach_this_month"] == 1085
    assert result["demographics"]["follower_demographics"]["age"] == {
        "25-34": 12, "35-44": 8,
    }
    assert result["errors"] == {}
    assert client.calls[0][1]["metric"] == "profile_links_taps"
    assert client.calls[1][1]["metric"] == "accounts_engaged,reach"
    assert client.calls[2][1]["metric"] == "follower_demographics"
    assert client.calls[2][1]["breakdown"] == "age"


def test_rate_limit_is_retried_with_backoff():
    sleeps = []
    client = QueueClient([
        FakeResponse({"error": {"code": 4, "message": "rate limited"}}, status_code=429),
        FakeResponse({"id": "ok"}),
    ])
    api = MetaGraphClient(
        meta_settings(meta_request_max_retries=1),
        client=client,
        sleep=sleeps.append,
    )

    assert api._get("me")["id"] == "ok"
    assert sleeps == [1]
    assert len(client.calls) == 2


def test_timeout_is_retried_then_classified():
    class TimeoutClient:
        def __init__(self):
            self.calls = 0

        def get(self, url, params, headers):
            self.calls += 1
            raise httpx.ReadTimeout("timeout", request=httpx.Request("GET", url))

    client = TimeoutClient()
    try:
        MetaGraphClient(
            meta_settings(meta_request_max_retries=1),
            client=client,
            sleep=lambda _seconds: None,
        )._get("me")
    except MetaApiError as exc:
        assert exc.category == "timeout"
        assert client.calls == 2
    else:
        raise AssertionError("MetaApiError was not raised")


def test_permission_error_is_distinguished():
    client = QueueClient([
        FakeResponse(
            {"error": {"code": 200, "message": "Permissions error"}},
            status_code=403,
        )
    ])

    try:
        MetaGraphClient(meta_settings(), client=client)._get("me")
    except MetaApiError as exc:
        assert exc.category == "permission_error"
    else:
        raise AssertionError("MetaApiError was not raised")


def test_invalid_debug_token_is_reported_as_revoked():
    client = QueueClient([FakeResponse({"data": {"is_valid": False}})])

    result = MetaGraphClient(meta_settings(), client=client).validate_token()

    assert result["is_valid"] is False
    assert result["status"] == "revoked"


def test_invalid_debug_token_past_expiration_is_reported_as_expired():
    expired_at = int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())
    client = QueueClient([FakeResponse({"data": {
        "is_valid": False, "expires_at": expired_at,
    }})])

    result = MetaGraphClient(meta_settings(), client=client).validate_token()

    assert result["is_valid"] is False
    assert result["status"] == "expired"


def test_meta_error_never_contains_token_or_remote_message():
    token = "sensitive-token-value"
    settings = Settings(
        _env_file=None,
        meta_access_token=token,
        meta_request_max_retries=0,
    )
    client = QueueClient([
        FakeResponse(
            {"error": {"code": 190, "error_subcode": 463,
                       "message": f"expired {token}"}},
            status_code=400,
        )
    ])

    try:
        MetaGraphClient(settings, client=client)._get("me")
    except MetaApiError as exc:
        assert exc.category == "expired"
        assert token not in str(exc)
        assert "expired sensitive" not in str(exc)
    else:
        raise AssertionError("MetaApiError was not raised")


def test_token_lifecycle_levels_and_expiration():
    now = datetime(2026, 9, 26, tzinfo=timezone.utc)

    assert token_lifecycle(now + timedelta(days=30), now=now)["level"] == "ok"
    assert token_lifecycle(now + timedelta(days=10), now=now)["level"] == "warning"
    soon = token_lifecycle(now + timedelta(days=3), now=now)
    assert soon["status"] == "expiring_soon"
    assert soon["level"] == "danger"
    assert token_lifecycle(now - timedelta(seconds=1), now=now)["status"] == "expired"
    assert token_lifecycle(None, valid=False, error_category="revoked", now=now)["status"] == "revoked"
