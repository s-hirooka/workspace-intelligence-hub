from pathlib import Path

from app.config import Settings
from app.services.ga4_config import resolve_ga4_config


def test_ga4_workspace_config_does_not_fall_back_to_another_customer(tmp_path: Path):
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        ga4_workspace_key="workspace",
        ga4_property_id="workspace-property",
        ga4_credentials_path=credential,
    )

    workspace = resolve_ga4_config(settings, "workspace", environ={})
    demo = resolve_ga4_config(settings, "demo", environ={})

    assert workspace.ga4_property_id == "workspace-property"
    assert workspace.configured is True
    assert demo.ga4_property_id == ""
    assert demo.configured is False


def test_ga4_workspace_config_uses_scoped_property_with_shared_credentials(tmp_path: Path):
    credential = tmp_path / "service-account.json"
    credential.write_text("{}", encoding="utf-8")
    settings = Settings(
        _env_file=None,
        ga4_workspace_key="workspace",
        ga4_property_id="workspace-property",
        ga4_credentials_path=credential,
    )

    demo = resolve_ga4_config(settings, "demo", environ={
        "GA4_DEMO_PROPERTY_ID": "demo-property",
    })

    assert demo.ga4_property_id == "demo-property"
    assert demo.ga4_credentials_path == credential.resolve()
    assert demo.configured is True
