from scripts.migrate_meta_env_workspaces import migrate


def test_migration_uses_upper_workspace_and_lower_demo_without_changing_values():
    source = [
        "META_PAGE_ID=workspace-page\n",
        "META_ACCESS_TOKEN=workspace-secret\n",
        "META_WORKSPACE_KEY=demo\n",
        "META_APP_ID=demo-app\n",
        "META_PAGE_ID=demo-page\n",
        "META_ACCESS_TOKEN=demo-secret\n",
        "META_REQUEST_MAX_RETRIES=2\n",
    ]

    result, changes = migrate(source)
    text = "".join(result)

    assert "META_WORKSPACE_PAGE_ID=workspace-page" in text
    assert "META_WORKSPACE_ACCESS_TOKEN=workspace-secret" in text
    assert "META_WORKSPACES=workspace,demo" in text
    assert "META_DEMO_APP_ID=demo-app" in text
    assert "META_DEMO_PAGE_ID=demo-page" in text
    assert "META_DEMO_ACCESS_TOKEN=demo-secret" in text
    assert "META_REQUEST_MAX_RETRIES=2" in text
    assert len(changes) == 6


def test_migration_is_idempotent():
    source = [
        "META_WORKSPACES=workspace,demo\n",
        "META_WORKSPACE_PAGE_ID=page\n",
    ]

    result, changes = migrate(source)

    assert result == source
    assert changes == []
