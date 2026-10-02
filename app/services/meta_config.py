"""Resolve Meta credentials without mixing customer workspaces."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from app.config import Settings


@dataclass(frozen=True)
class MetaWorkspaceConfig:
    """Settings-like object consumed by ``MetaGraphClient`` for one workspace."""

    workspace_key: str
    meta_app_id: str = ""
    meta_app_secret: str = field(default="", repr=False)
    meta_page_id: str = ""
    meta_instagram_account_id: str = ""
    meta_access_token: str = field(default="", repr=False)
    meta_graph_version: str = "v26.0"
    meta_request_max_retries: int = 2
    sns_max_posts_per_platform: int = 200
    sns_incremental_posts_per_platform: int = 50

    @property
    def configured(self) -> bool:
        return bool(self.meta_page_id and self.meta_access_token)


def _normalize_workspace_key(workspace_key: str) -> str:
    normalized = workspace_key.strip().lower()
    if not normalized or not all(char.isalnum() or char in "-_" for char in normalized):
        raise ValueError("Workspace key must contain only letters, numbers, '-' or '_'")
    return normalized


def _workspace_prefix(workspace_key: str) -> str:
    return _normalize_workspace_key(workspace_key).replace("-", "_").upper()


def _environment_values(env_file: str | Path = ".env") -> dict[str, str]:
    """Read arbitrary prefixed variables; process environment takes precedence."""
    values: dict[str, str] = {}
    path = Path(env_file)
    if path.is_file():
        values.update({
            str(key).upper(): str(value or "")
            for key, value in dotenv_values(path).items()
            if key
        })
    values.update({str(key).upper(): str(value) for key, value in os.environ.items()})
    return values


def resolve_meta_config(
    settings: Settings,
    workspace_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> MetaWorkspaceConfig:
    """Return only the selected workspace's Meta credentials.

    Workspace-prefixed variables are an atomic credential set. Missing values
    are never filled from another workspace's legacy settings.
    """
    workspace_key = _normalize_workspace_key(workspace_key)
    legacy_configured = bool(settings.meta_page_id or settings.meta_access_token)
    if legacy_configured:
        if workspace_key != settings.meta_workspace_key:
            return MetaWorkspaceConfig(
                workspace_key=workspace_key,
                meta_graph_version=settings.meta_graph_version,
                meta_request_max_retries=settings.meta_request_max_retries,
                sns_max_posts_per_platform=settings.sns_max_posts_per_platform,
                sns_incremental_posts_per_platform=settings.sns_incremental_posts_per_platform,
            )
        return MetaWorkspaceConfig(
            workspace_key=workspace_key,
            meta_app_id=settings.meta_app_id,
            meta_app_secret=settings.meta_app_secret,
            meta_page_id=settings.meta_page_id,
            meta_instagram_account_id=settings.meta_instagram_account_id,
            meta_access_token=settings.meta_access_token,
            meta_graph_version=settings.meta_graph_version,
            meta_request_max_retries=settings.meta_request_max_retries,
            sns_max_posts_per_platform=settings.sns_max_posts_per_platform,
            sns_incremental_posts_per_platform=settings.sns_incremental_posts_per_platform,
        )

    values = (
        {str(key).upper(): str(value) for key, value in environ.items()}
        if environ is not None
        else _environment_values()
    )
    prefix = f"META_{_workspace_prefix(workspace_key)}_"
    names = {
        "meta_app_id": "APP_ID",
        "meta_app_secret": "APP_SECRET",
        "meta_page_id": "PAGE_ID",
        "meta_instagram_account_id": "INSTAGRAM_ACCOUNT_ID",
        "meta_access_token": "ACCESS_TOKEN",
        "meta_graph_version": "GRAPH_VERSION",
    }
    specific = {field_name: values.get(prefix + suffix, "").strip()
                for field_name, suffix in names.items()}
    if any(specific.values()):
        return MetaWorkspaceConfig(
            workspace_key=workspace_key,
            meta_app_id=specific["meta_app_id"],
            meta_app_secret=specific["meta_app_secret"],
            meta_page_id=specific["meta_page_id"],
            meta_instagram_account_id=specific["meta_instagram_account_id"],
            meta_access_token=specific["meta_access_token"],
            meta_graph_version=specific["meta_graph_version"] or settings.meta_graph_version,
            meta_request_max_retries=settings.meta_request_max_retries,
            sns_max_posts_per_platform=settings.sns_max_posts_per_platform,
            sns_incremental_posts_per_platform=settings.sns_incremental_posts_per_platform,
        )

    return MetaWorkspaceConfig(
        workspace_key=workspace_key,
        meta_graph_version=settings.meta_graph_version,
        meta_request_max_retries=settings.meta_request_max_retries,
        sns_max_posts_per_platform=settings.sns_max_posts_per_platform,
        sns_incremental_posts_per_platform=settings.sns_incremental_posts_per_platform,
    )


def configured_meta_workspace_keys(
    settings: Settings,
    *,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """List configured Meta workspaces for scheduled synchronization."""
    if settings.meta_page_id or settings.meta_access_token:
        candidates = {settings.meta_workspace_key}
    else:
        candidates = set(settings.meta_workspaces)
    return sorted(
        workspace_key
        for workspace_key in candidates
        if resolve_meta_config(settings, workspace_key, environ=environ).configured
    )
