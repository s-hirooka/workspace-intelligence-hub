"""Resolve Google Business Profile settings without mixing workspaces."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

from app.config import Settings


@dataclass(frozen=True)
class GBPWorkspaceConfig:
    workspace_key: str
    location_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    refresh_token: str = ""

    @property
    def configured(self) -> bool:
        return bool(
            self.location_id
            and self.client_id
            and self.client_secret
            and self.refresh_token
        )


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


def _location_name(value: str) -> str:
    value = value.strip().strip("/")
    if not value:
        return ""
    return value if value.startswith("locations/") else f"locations/{value}"


def resolve_gbp_config(
    settings: Settings,
    workspace_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> GBPWorkspaceConfig:
    """Return GBP credentials for exactly one customer workspace.

    The OAuth client may be shared, but a location ID and refresh token are
    always workspace-scoped. This prevents an unconfigured customer from
    displaying another customer's Business Profile figures.
    """
    workspace_key = _normalize_workspace_key(workspace_key)
    values = (
        {str(key).upper(): str(value) for key, value in environ.items()}
        if environ is not None
        else _environment_values()
    )
    prefix = f"GBP_{workspace_key.replace('-', '_').upper()}_"
    location_id = _location_name(values.get(prefix + "LOCATION_ID", ""))
    refresh_token = values.get(prefix + "REFRESH_TOKEN", "").strip()
    client_id = (
        values.get(prefix + "CLIENT_ID", "").strip()
        or values.get("GBP_CLIENT_ID", "").strip()
        or settings.google_client_id
    )
    client_secret = (
        values.get(prefix + "CLIENT_SECRET", "").strip()
        or values.get("GBP_CLIENT_SECRET", "").strip()
        or settings.google_client_secret
    )
    return GBPWorkspaceConfig(
        workspace_key=workspace_key,
        location_id=location_id,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )
