from pathlib import Path

from app.config import Settings
from app.services.scanner import is_excluded, project_for, scan_files


def test_project_detection(tmp_path: Path):
    path = tmp_path / "workspace-sysC9" / "Services" / "Test.cs"
    path.parent.mkdir(parents=True)
    path.write_text("class Test {}", encoding="utf-8")
    assert project_for(path, tmp_path) == "workspace-sysC9"


def test_excluded_folder(tmp_path: Path):
    path = tmp_path / "web" / "node_modules" / "library.js"
    path.parent.mkdir(parents=True)
    path.write_text("secret", encoding="utf-8")
    assert is_excluded(path, tmp_path, {"node_modules"})
    settings = Settings(source_root=tmp_path, excluded_dirs={"node_modules"}, _env_file=None)
    assert list(scan_files(settings)) == []


def test_office_lock_file_is_excluded(tmp_path: Path):
    path = tmp_path / "doc" / "~$estimate.xlsx"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-a-real-workbook")
    settings = Settings(source_root=tmp_path, _env_file=None)
    assert list(scan_files(settings)) == []


def test_wordpress_config_is_excluded(tmp_path: Path):
    path = tmp_path / "bukken" / "wp-config.php"
    path.parent.mkdir(parents=True)
    path.write_text("define('DB_PASSWORD', 'must-not-index');", encoding="utf-8")
    settings = Settings(source_root=tmp_path, _env_file=None)
    assert list(scan_files(settings)) == []


def test_wordpress_config_backup_is_excluded(tmp_path: Path):
    path = tmp_path / "bukken" / "wp-config bak.php"
    path.parent.mkdir(parents=True)
    path.write_text("define('DB_PASSWORD', 'must-not-index');", encoding="utf-8")
    settings = Settings(source_root=tmp_path, _env_file=None)
    assert list(scan_files(settings)) == []


def test_dated_backup_source_is_excluded(tmp_path: Path):
    path = tmp_path / "bukken" / "functions_back20251121.php"
    path.parent.mkdir(parents=True)
    path.write_text("old code", encoding="utf-8")
    settings = Settings(source_root=tmp_path, _env_file=None)
    assert list(scan_files(settings)) == []


def test_operational_private_documents_are_excluded(tmp_path: Path):
    project = tmp_path / "doc"
    project.mkdir()
    (project / "wp_member_directory_raw.csv").write_text("private", encoding="utf-8")
    (project / "パスワード変更手順.xlsx").write_bytes(b"private")
    settings = Settings(
        source_root=tmp_path,
        supported_extensions={".xlsx", ".csv"},
        _env_file=None,
    )
    assert list(scan_files(settings)) == []


def test_policy_can_disable_sqlite_snapshots(tmp_path: Path):
    database = tmp_path / "workspacedata.sqlite"
    database.write_bytes(b"sqlite placeholder")
    policy = tmp_path / "index-policy.yaml"
    policy.write_text(
        "default_action: deny\n"
        "projects:\n"
        "  sqlite-properties: {enabled: false}\n"
        "  sqlite-companies: {enabled: false}\n"
        "extensions: ['.sqlite']\n"
        "paths:\n"
        "  - {pattern: '**', action: allow}\n",
        encoding="utf-8",
    )
    settings = Settings(
        source_root=tmp_path,
        sqlite_database_path=database,
        index_policy_config=policy,
        _env_file=None,
    )
    assert list(scan_files(settings)) == []


def test_doc_project_indexes_only_curated_workspace_markdown(tmp_path: Path):
    project = tmp_path / "doc"
    project.mkdir()
    (project / "Workspace仕様書.md").write_text("仕様", encoding="utf-8")
    (project / "古い提案書.md").write_text("out of scope", encoding="utf-8")
    (project / "old.xlsx").write_bytes(b"private")
    nested = project / "history"
    nested.mkdir()
    (nested / "Workspace古い仕様書.md").write_text("old", encoding="utf-8")
    settings = Settings(source_root=tmp_path, _env_file=None)
    assert [item.file_name for item in scan_files(settings)] == ["Workspace仕様書.md"]
