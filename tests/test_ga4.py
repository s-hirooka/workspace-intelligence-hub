from pathlib import Path

from app.config import Settings
from app.services.ga4 import _report_rows, ga4_dashboard


def _settings(tmp_path: Path) -> Settings:
    credential = tmp_path / "credential.json"
    credential.write_text("{}", encoding="utf-8")
    return Settings(_env_file=None, ga4_property_id="123", ga4_credentials_path=credential)


def _report(dimensions, metrics, values):
    return {
        "dimensionHeaders": [{"name": name} for name in dimensions],
        "metricHeaders": [{"name": name} for name in metrics],
        "rows": [{
            "dimensionValues": [{"value": value} for value in row[:len(dimensions)]],
            "metricValues": [{"value": str(value)} for value in row[len(dimensions):]],
        } for row in values],
    }


def test_report_rows_maps_headers_and_numeric_values():
    report = _report(
        ["sessionSource"], ["sessions", "engagementRate"],
        [["instagram.com", 12, 0.75]],
    )
    assert _report_rows(report) == [{
        "sessionSource": "instagram.com", "sessions": 12, "engagementRate": 0.75,
    }]


def test_ga4_dashboard_returns_social_summary(tmp_path):
    responses = [
        _report(
            [],
            ["sessions", "totalUsers", "engagedSessions", "engagementRate", "averageSessionDuration", "keyEvents"],
            [[20, 15, 12, 0.6, 35.5, 2]],
        ),
        _report(
            ["sessionSource", "sessionMedium"],
            ["sessions", "totalUsers", "engagedSessions", "keyEvents"],
            [["l.facebook.com", "referral", 11, 9, 7, 1]],
        ),
        _report(
            ["landingPage", "sessionSource"],
            ["sessions", "totalUsers", "keyEvents"],
            [["/property/1/", "l.facebook.com", 8, 7, 1]],
        ),
    ]

    def reporter(_settings, _payload):
        return responses.pop(0)

    result = ga4_dashboard(_settings(tmp_path), days=90, reporter=reporter)
    assert result["summary"]["sessions"] == 20
    assert result["summary"]["engagement_rate"] == 0.6
    assert result["sources"][0]["source"] == "l.facebook.com"
    assert result["landings"][0]["page"] == "/property/1/"