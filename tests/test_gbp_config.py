from app.config import Settings
from app.services.gbp_config import resolve_gbp_config


def test_gbp_workspace_config_never_uses_another_customer_location_or_token():
    settings = Settings(
        _env_file=None,
        google_client_id="shared-client",
        google_client_secret="shared-secret",
    )

    demo = resolve_gbp_config(settings, "demo", environ={
        "GBP_DEMO_LOCATION_ID": "locations/123456789",
        "GBP_DEMO_REFRESH_TOKEN": "demo-refresh-token",
    })
    workspace = resolve_gbp_config(settings, "workspace", environ={})

    assert demo.location_id == "locations/123456789"
    assert demo.refresh_token == "demo-refresh-token"
    assert demo.configured is True
    assert workspace.location_id == ""
    assert workspace.refresh_token == ""
    assert workspace.configured is False


def test_gbp_workspace_can_override_shared_oauth_client():
    settings = Settings(
        _env_file=None,
        google_client_id="shared-client",
        google_client_secret="shared-secret",
    )

    config = resolve_gbp_config(settings, "demo", environ={
        "GBP_DEMO_LOCATION_ID": "987654321",
        "GBP_DEMO_REFRESH_TOKEN": "refresh-token",
        "GBP_DEMO_CLIENT_ID": "demo-client",
        "GBP_DEMO_CLIENT_SECRET": "demo-secret",
    })

    assert config.location_id == "locations/987654321"
    assert config.client_id == "demo-client"
    assert config.client_secret == "demo-secret"
    assert config.configured is True
