from datetime import date

import httpx

from app.services.gbp import GBP_METRICS, _fetch_performance, google_business_profile_dashboard
from app.services.gbp_config import GBPWorkspaceConfig


def _metric(name, values):
    return {
        "dailyMetric": name,
        "timeSeries": {
            "datedValues": [
                {"date": {"year": 2026, "month": 9, "day": day}, "value": str(value)}
                for day, value in values
            ]
        },
    }


def test_google_business_profile_dashboard_combines_search_and_maps_impressions():
    config = GBPWorkspaceConfig(
        workspace_key="demo",
        location_id="locations/123",
        client_id="client",
        client_secret="secret",
        refresh_token="refresh",
    )
    payload = {
        "multiDailyMetricTimeSeries": [{
            "dailyMetricTimeSeries": [
                _metric("BUSINESS_IMPRESSIONS_DESKTOP_SEARCH", [(25, 10), (26, 12)]),
                _metric("BUSINESS_IMPRESSIONS_MOBILE_SEARCH", [(25, 30), (26, 35)]),
                _metric("BUSINESS_IMPRESSIONS_DESKTOP_MAPS", [(25, 4), (26, 5)]),
                _metric("BUSINESS_IMPRESSIONS_MOBILE_MAPS", [(25, 20), (26, 25)]),
                _metric("WEBSITE_CLICKS", [(25, 3), (26, 4)]),
                _metric("CALL_CLICKS", [(25, 1), (26, 2)]),
                _metric("BUSINESS_DIRECTION_REQUESTS", [(25, 2), (26, 3)]),
                _metric("BUSINESS_BOOKINGS", [(25, 0), (26, 1)]),
            ],
        }],
    }

    def token_provider(_config):
        return "access-token"

    def reporter(_config, access_token, _start_date, _end_date):
        assert access_token == "access-token"
        return payload

    result = google_business_profile_dashboard(
        config,
        days=30,
        token_provider=token_provider,
        reporter=reporter,
    )

    assert "WEBSITE_CLICKS" in GBP_METRICS
    assert result["configured"] is True
    assert result["summary"]["search_impressions"] == 87
    assert result["summary"]["maps_impressions"] == 54
    assert result["summary"]["website_clicks"] == 7
    assert result["summary"]["call_clicks"] == 3
    assert result["summary"]["direction_requests"] == 5
    assert result["summary"]["bookings"] == 1
    assert result["series"][-1]["date"] == "2026-09-26"
    assert result["series"][-1]["search_impressions"] == 47
    assert result["recommendations"]


def test_google_business_profile_dashboard_does_not_call_api_when_unconfigured():
    config = GBPWorkspaceConfig(workspace_key="workspace")

    result = google_business_profile_dashboard(config, days=30)

    assert result["configured"] is False
    assert result["summary"] == {}
    assert result["series"] == []


def test_performance_request_uses_official_rest_parameter_names(monkeypatch):
    config = GBPWorkspaceConfig(workspace_key="demo", location_id="locations/123")
    captured = {}

    def fake_get(url, *, headers, params, timeout):
        captured.update(url=url, headers=headers, params=dict(params), timeout=timeout)
        return httpx.Response(200, json={})

    monkeypatch.setattr(httpx, "get", fake_get)
    _fetch_performance(config, "access", date(2026, 9, 1), date(2026, 9, 27))

    assert captured["url"].endswith("/locations/123:fetchMultiDailyMetricsTimeSeries")
    assert captured["params"]["dailyRange.start_date.year"] == 2026
    assert captured["params"]["dailyRange.end_date.day"] == 27
