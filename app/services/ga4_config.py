"""Resolve GA4 Data API settings without mixing customer workspaces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from app.config import Settings


@dataclass(frozen=True)
class GA4WorkspaceConfig:
    workspace_key: str
    ga4_property_id: str = ""
    ga4_credentials_path: Path = Path("secrets/ga4-service-account.json")

    @property
    def configured(self) -> bool:
        return bool(self.ga4_property_id and self.ga4_credentials_path.is_file())


def _normalize_workspace_key(workspace_key: str) -> str:
    normalized = workspace_key.strip().lower()
    if not normalized or not all(char.isalnum() or char in "-_" for char in normalized):
        raise ValueError("Workspace key must contain only letters, numbers, '-' or '_'")
    return normalized


def _environment_values(env_file: str | Path = ".env") -> dict[str, str]:
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


def resolve_ga4_config(
    settings: Settings,
    workspace_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> GA4WorkspaceConfig:
    """Return the selected customer's GA4 property and read-only credential.

    ``GA4_<WORKSPACE>_PROPERTY_ID`` opts a workspace into GA4. A service
    account file can be set per workspace, or the existing read-only global
    credential can be shared when it has access to each configured property.
    The legacy ``GA4_PROPERTY_ID`` is only used by ``GA4_WORKSPACE_KEY``.
    """
    workspace_key = _normalize_workspace_key(workspace_key)
    values = (
        {str(key).upper(): str(value) for key, value in environ.items()}
        if environ is not None
        else _environment_values()
    )
    prefix = f"GA4_{workspace_key.replace('-', '_').upper()}_"
    property_id = values.get(prefix + "PROPERTY_ID", "").strip()
    credentials_value = values.get(prefix + "CREDENTIALS_PATH", "").strip()

    if property_id or credentials_value:
        credentials_path = (
            Path(credentials_value).expanduser().resolve()
            if credentials_value
            else settings.ga4_credentials_path
        )
        return GA4WorkspaceConfig(
            workspace_key=workspace_key,
            ga4_property_id=property_id,
            ga4_credentials_path=credentials_path,
        )

    if workspace_key == settings.ga4_workspace_key:
        return GA4WorkspaceConfig(
            workspace_key=workspace_key,
            ga4_property_id=settings.ga4_property_id,
            ga4_credentials_path=settings.ga4_credentials_path,
        )

    return GA4WorkspaceConfig(
        workspace_key=workspace_key,
        ga4_credentials_path=settings.ga4_credentials_path,
    )
