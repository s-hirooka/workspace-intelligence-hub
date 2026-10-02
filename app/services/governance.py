"""Explicit, read-only source roots and allowlisted indexing policy."""

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
import re

import yaml

from app.config import Settings


@dataclass(frozen=True)
class SourceRoot:
    id: str
    path: Path
    read_only: bool = True
    enabled: bool = True
    workspace_key: str = "workspace"


def _config_path(configured: Path | None, fallback: str, settings: Settings) -> Path | None:
    if configured:
        return configured
    # Do not accidentally apply the deployment's configuration to test roots.
    if settings.source_root == Path("sample-data") and Path(fallback).is_file():
        return Path(fallback)
    return None


def load_roots(settings: Settings) -> list[SourceRoot]:
    path = _config_path(settings.sources_config, "sources.yaml", settings)
    if not path:
        return [SourceRoot("legacy", settings.source_root)]
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    roots = []
    for item in data.get("sources", []):
        if not item.get("enabled", True):
            continue
        if item.get("read_only") is not True:
            raise ValueError("Every source root must declare read_only: true")
        root = Path(item["path"]).resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Enabled source root is not a directory: {root}")
        workspace_key = str(item.get("workspace", "workspace")).strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", workspace_key):
            raise ValueError("Source workspace must contain only letters, numbers, '-' or '_'")
        roots.append(SourceRoot(str(item["id"]), root, workspace_key=workspace_key))
    if len({root.id for root in roots}) != len(roots):
        raise ValueError("Duplicate source IDs")
    return roots


def load_policy(settings: Settings) -> dict:
    path = _config_path(settings.index_policy_config, "index-policy.yaml", settings)
    if not path:
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("default_action", "allow") not in ("allow", "deny"):
        raise ValueError("Invalid index policy default_action")
    return data


def policy_allows(relative: Path, project: str, extension: str, policy: dict) -> bool:
    if not policy:
        return True
    projects = policy.get("projects", {})
    if project in projects and not projects[project].get("enabled", False):
        return False
    if project not in projects and policy.get("default_action") == "deny":
        return False
    if extension.lower() not in {str(e).lower() for e in policy.get("extensions", [])}:
        return False
    normalized = relative.as_posix()
    # First matching rule wins; explicit doc allow must precede doc deny.
    for rule in policy.get("paths", []):
        if fnmatchcase(normalized, rule["pattern"]):
            return rule["action"] == "allow"
    return policy.get("default_action", "allow") == "allow"


def classify_source(project: str, path: Path, source_id: str, extension: str) -> str:
    if source_id == "generated" or (project == "doc" and path.name.startswith("Workspace") and extension == ".md"):
        return "generated_document"
    if extension in {
        ".cs", ".php", ".js", ".jsx", ".ts", ".tsx", ".py", ".sql",
        ".css", ".scss", ".html", ".htm",
    }:
        return "source_code"
    if extension == ".csv":
        return "csv"
    if "見積" in path.name:
        return "estimate"
    if extension in {".md", ".pdf", ".docx", ".xlsx", ".txt"}:
        return "specification"
    return "unknown"
