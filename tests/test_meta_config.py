from app.config import Settings
from app.services.meta_config import configured_meta_workspace_keys, resolve_meta_config


def base_settings(**overrides):
    return Settings(_env_file=None, **overrides)


def test_resolves_workspace_and_demo_credentials_independently():
    settings = base_settings(meta_workspaces={"workspace", "demo"})
    environ = {
        "META_WORKSPACE_PAGE_ID": "workspace-page",
        "META_WORKSPACE_INSTAGRAM_ACCOUNT_ID": "workspace-instagram",
        "META_WORKSPACE_ACCESS_TOKEN": "workspace-token",
        "META_DEMO_APP_ID": "demo-app",
        "META_DEMO_PAGE_ID": "demo-page",
        "META_DEMO_INSTAGRAM_ACCOUNT_ID": "demo-instagram",
        "META_DEMO_ACCESS_TOKEN": "demo-token",
    }

    workspace = resolve_meta_config(settings, "workspace", environ=environ)
    demo = resolve_meta_config(settings, "demo", environ=environ)

    assert workspace.meta_page_id == "workspace-page"
    assert workspace.meta_instagram_account_id == "workspace-instagram"
    assert workspace.meta_access_token == "workspace-token"
    assert demo.meta_app_id == "demo-app"
    assert demo.meta_page_id == "demo-page"
    assert demo.meta_instagram_account_id == "demo-instagram"
    assert demo.meta_access_token == "demo-token"


def test_incomplete_workspace_set_never_borrows_legacy_credentials():
    settings = base_settings(meta_workspaces={"demo"})

    config = resolve_meta_config(
        settings,
        "demo",
        environ={"META_DEMO_PAGE_ID": "demo-page"},
    )

    assert config.meta_page_id == "demo-page"
    assert config.meta_access_token == ""
    assert config.configured is False


def test_legacy_single_workspace_configuration_still_works():
    settings = base_settings(
        meta_workspace_key="demo",
        meta_page_id="legacy-page",
        meta_access_token="legacy-token",
    )

    demo = resolve_meta_config(settings, "demo", environ={})
    workspace = resolve_meta_config(settings, "workspace", environ={})

    assert demo.configured is True
    assert demo.meta_access_token == "legacy-token"
    assert workspace.configured is False


def test_legacy_mode_does_not_read_other_workspace_from_prefixed_environment():
    settings = base_settings(
        meta_workspace_key="demo",
        meta_page_id="legacy-page",
        meta_access_token="legacy-token",
    )

    workspace = resolve_meta_config(settings, "workspace", environ={
        "META_WORKSPACE_PAGE_ID": "workspace-page",
        "META_WORKSPACE_ACCESS_TOKEN": "workspace-token",
    })

    assert workspace.configured is False
    assert workspace.meta_access_token == ""


def test_scheduler_lists_all_configured_meta_workspaces():
    settings = base_settings(meta_workspaces={"workspace", "demo", "empty"})
    environ = {
        "META_WORKSPACE_PAGE_ID": "workspace-page",
        "META_WORKSPACE_ACCESS_TOKEN": "workspace-token",
        "META_DEMO_PAGE_ID": "demo-page",
        "META_DEMO_ACCESS_TOKEN": "demo-token",
        "META_EMPTY_PAGE_ID": "empty-page",
    }

    assert configured_meta_workspace_keys(settings, environ=environ) == ["demo", "workspace"]


def test_config_repr_does_not_expose_secrets():
    settings = base_settings(meta_workspaces={"demo"})
    config = resolve_meta_config(settings, "demo", environ={
        "META_DEMO_APP_SECRET": "private-app-secret",
        "META_DEMO_PAGE_ID": "page",
        "META_DEMO_ACCESS_TOKEN": "private-access-token",
    })

    rendered = repr(config)
    assert "private-app-secret" not in rendered
    assert "private-access-token" not in rendered
