"""Rename legacy duplicate META_* entries to workspace-specific keys.

This migration intentionally never prints values. It supports the historical
layout used by this project: entries above META_WORKSPACE_KEY belong to Workspace,
and entries below it belong to Demo.
"""

from __future__ import annotations

import argparse
import os
import re
import tempfile
from pathlib import Path


WORKSPACE_FIELDS = {
    "META_APP_ID",
    "META_APP_SECRET",
    "META_PAGE_ID",
    "META_INSTAGRAM_ACCOUNT_ID",
    "META_ACCESS_TOKEN",
    "META_GRAPH_VERSION",
}
KEY_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<key>META_[A-Z0-9_]+)(?P<separator>\s*=)")


def migrate(lines: list[str]) -> tuple[list[str], list[str]]:
    existing_keys = {
        match.group("key")
        for line in lines
        if (match := KEY_PATTERN.match(line))
    }
    if "META_WORKSPACES" in existing_keys:
        return lines, []
    if "META_WORKSPACE_KEY" not in existing_keys:
        raise ValueError("META_WORKSPACE_KEYがないため、上段・下段を安全に判定できません")

    workspace = "WORKSPACE"
    changes: list[str] = []
    migrated: list[str] = []
    for line in lines:
        match = KEY_PATTERN.match(line)
        if not match:
            migrated.append(line)
            continue
        key = match.group("key")
        if key == "META_WORKSPACE_KEY":
            migrated.append(f"{match.group('indent')}META_WORKSPACES=workspace,demo\n")
            changes.append("META_WORKSPACE_KEY -> META_WORKSPACES")
            workspace = "DEMO"
            continue
        if key not in WORKSPACE_FIELDS:
            migrated.append(line)
            continue
        new_key = f"META_{workspace}_{key.removeprefix('META_')}"
        migrated.append(
            line[:match.start("key")] + new_key + line[match.end("key"):]
        )
        changes.append(f"{key} -> {new_key}")
    return migrated, changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    path = args.env_file.resolve()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    migrated, changes = migrate(lines)
    if not changes:
        print("Meta workspace migration: already migrated or no changes")
        return 0
    print(f"Meta workspace migration: {len(changes)} key renames")
    for change in changes:
        print(f"- {change}")
    if not args.apply:
        print("Dry run only. Use --apply to update the file.")
        return 0

    fd, temporary_name = tempfile.mkstemp(prefix=".env-meta-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.writelines(migrated)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    print("Meta workspace migration: applied (values were not changed or displayed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
