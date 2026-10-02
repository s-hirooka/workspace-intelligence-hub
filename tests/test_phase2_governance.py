from pathlib import Path

import pytest

from app.config import Settings
from app.services.governance import classify_source, load_roots, policy_allows
from app.services.scanner import scan_files


def test_source_roots_must_be_read_only(tmp_path):
    config = tmp_path / "sources.yaml"
    config.write_text(f"sources:\n  - id: test\n    path: '{tmp_path}'\n    read_only: false\n", encoding="utf-8")
    with pytest.raises(ValueError, match="read_only"):
        load_roots(Settings(source_root=tmp_path, sources_config=config, _env_file=None))


def test_multiple_source_roots_and_generated_classification(tmp_path):
    first, generated = tmp_path / "source", tmp_path / "generated_docs"
    (first / "app").mkdir(parents=True)
    generated.mkdir()
    (first / "app" / "main.cs").write_text("class A {}", encoding="utf-8")
    (generated / "Workspace仕様書.md").write_text("generated", encoding="utf-8")
    config = tmp_path / "sources.yaml"
    config.write_text(f"sources:\n  - id: main\n    path: '{first}'\n    read_only: true\n  - id: generated\n    path: '{generated}'\n    read_only: true\n", encoding="utf-8")
    rows = list(scan_files(Settings(source_root=first, sources_config=config, _env_file=None)))
    assert len(rows) == 2
    assert {(r.project, r.source_type) for r in rows} == {("app", "source_code"), ("doc", "generated_document")}


def test_allowlist_denylist_and_default_deny():
    policy = {"default_action": "deny", "projects": {"doc": {"enabled": True}},
              "extensions": [".md"], "paths": [{"pattern": "doc/approved/**", "action": "allow"},
                                                  {"pattern": "doc/**", "action": "deny"}]}
    assert policy_allows(Path("doc/approved/a.md"), "doc", ".md", policy)
    assert not policy_allows(Path("doc/private/a.md"), "doc", ".md", policy)
    assert not policy_allows(Path("doc/approved/a.pdf"), "doc", ".pdf", policy)
    assert not policy_allows(Path("other/a.md"), "other", ".md", policy)


def test_first_matching_rule_can_exclude_one_document():
    policy = {
        "default_action": "deny",
        "projects": {"doc": {"enabled": True}},
        "extensions": [".md"],
        "paths": [
            {"pattern": "doc/Workspace SQLiteデータベース仕様書.md", "action": "deny"},
            {"pattern": "doc/**", "action": "allow"},
        ],
    }
    assert not policy_allows(Path("doc/Workspace SQLiteデータベース仕様書.md"), "doc", ".md", policy)
    assert policy_allows(Path("doc/Workspaceデータベース設計書.md"), "doc", ".md", policy)


def test_generated_specs_can_be_authoritative_without_source_duplicates():
    policy = {
        "default_action": "deny",
        "projects": {"doc": {"enabled": True}},
        "extensions": [".md", ".xlsx"],
        "paths": [
            {"pattern": "doc/Workspace*.md", "action": "deny"},
            {"pattern": "doc/generated/**", "action": "allow"},
            {"pattern": "doc/**", "action": "allow"},
        ],
    }
    assert not policy_allows(Path("doc/Workspace現行システム仕様書.md"), "doc", ".md", policy)
    assert policy_allows(Path("doc/generated/Workspace現行システム仕様書.md"), "doc", ".md", policy)
    assert policy_allows(Path("doc/画面設計.xlsx"), "doc", ".xlsx", policy)


def test_source_type_estimate_and_snapshot():
    assert classify_source("doc", Path("見積書.pdf"), "main", ".pdf") == "estimate"
    assert classify_source("doc", Path("Workspace仕様書.md"), "generated", ".md") == "generated_document"
