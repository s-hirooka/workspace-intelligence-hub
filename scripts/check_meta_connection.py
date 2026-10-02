"""Read-only Meta connection check that never prints access tokens."""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings
from app.services.meta_config import configured_meta_workspace_keys, resolve_meta_config
from app.services.meta_graph import MetaApiError, MetaGraphClient


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-key", help="Customer workspace key")
    parser.add_argument("--page-id", help="Facebook Page ID (defaults to selected workspace setting)")
    parser.add_argument(
        "--instagram-account-id",
        help="Expected Instagram Business Account ID (defaults to selected workspace setting)",
    )
    args = parser.parse_args()
    settings = get_settings()
    configured_keys = configured_meta_workspace_keys(settings)
    workspace_key = args.workspace_key or (configured_keys[0] if configured_keys else settings.meta_workspace_key)
    config = resolve_meta_config(settings, workspace_key)
    config = replace(
        config,
        meta_page_id=args.page_id or config.meta_page_id,
        meta_instagram_account_id=args.instagram_account_id or config.meta_instagram_account_id,
    )
    page_id = config.meta_page_id
    expected_instagram_id = config.meta_instagram_account_id
    result = {
        "configured": config.configured,
        "workspace_key": workspace_key,
        "page_id": page_id,
        "expected_instagram_account_id": expected_instagram_id,
    }
    if not result["configured"]:
        result.update({"ok": False, "error_category": "not_configured"})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 2
    try:
        with MetaGraphClient(config) as client:
            token = client.validate_token()
            result["token"] = {
                "is_valid": token["is_valid"],
                "status": token["status"],
                "expires_at": token["expires_at"].isoformat() if token["expires_at"] else None,
                "data_access_expires_at": (
                    token["data_access_expires_at"].isoformat()
                    if token["data_access_expires_at"] else None
                ),
                "app_matches": not config.meta_app_id or token["app_id"] == config.meta_app_id,
                "scopes": sorted(token["scopes"]),
            }
            page, page_token = client.get_facebook_page(page_id)
            linked_id = str((page.get("instagram_business_account") or {}).get("id") or "")
            result["page"] = {
                "id": str(page.get("id") or ""),
                "name": str(page.get("name") or ""),
                "linked_instagram_account_id": linked_id,
                "instagram_matches": not expected_instagram_id or linked_id == expected_instagram_id,
            }
            if linked_id:
                profile = client.get_instagram_profile(linked_id, token=page_token)
                result["instagram"] = {
                    "id": str(profile.get("id") or ""),
                    "username": str(profile.get("username") or ""),
                    "followers_count": profile.get("followers_count"),
                    "media_count": profile.get("media_count"),
                }
            result["ok"] = bool(
                token["is_valid"]
                and result["token"]["app_matches"]
                and linked_id
                and result["page"]["instagram_matches"]
            )
    except MetaApiError as exc:
        result.update({
            "ok": False,
            "error_category": exc.category,
            "error_code": exc.code,
            "error_subcode": exc.subcode,
        })
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
