import hashlib
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from app.config import Settings
from app.domain import ScannedFile
from app.services.governance import classify_source, load_policy, load_roots, policy_allows


# Deployment configuration files commonly contain live credentials. Their
# non-secret operational facts are documented separately before indexing.
EXCLUDED_FILE_NAMES = {
    "wp-config.php", "wp-config-sample.php", "wp-activate.php",
    "wp-blog-header.php", "wp-comments-post.php", "wp-cron.php",
    "wp-links-opml.php", "wp-load.php", "wp-login.php", "wp-mail.php",
    "wp-settings.php", "wp-signup.php", "wp-trackback.php", "xmlrpc.php",
    "hello.php", "wp_member_directory_raw.csv",
}
BACKUP_FILE_NAME = re.compile(r"(?i)(?:_back(?:up)?(?:\d{8})?|\.bak)(?:\.[^.]+)?$")


def is_excluded_file_name(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered in EXCLUDED_FILE_NAMES
        or lowered.startswith("wp-config")
        or "パスワード" in lowered
        or bool(BACKUP_FILE_NAME.search(name))
    )


def is_allowed_document(path: Path, root: Path) -> bool:
    """Index curated Workspace specifications, not raw records under doc/."""
    relative = path.relative_to(root)
    if relative.parts[0].lower() != "doc":
        return True
    return len(relative.parts) == 2 and path.suffix.lower() == ".md" and path.name.startswith("Workspace")


def project_for(path: Path, source_root: Path) -> str:
    relative = path.resolve().relative_to(source_root.resolve())
    if len(relative.parts) < 2:
        raise ValueError("Files directly under SOURCE_ROOT do not belong to a project")
    return relative.parts[0]


def is_excluded(path: Path, source_root: Path, excluded_dirs: set[str]) -> bool:
    relative = path.resolve().relative_to(source_root.resolve())
    excluded = {name.lower() for name in excluded_dirs}
    return any(part.lower() in excluded for part in relative.parts[:-1])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


SQLITE_TABLE_PROJECTS = {
    "BukkenData": "sqlite-properties",
    "company_info": "sqlite-companies",
}


def _sqlite_fingerprint(path: Path, table: str) -> str:
    """Fingerprint a SQLite snapshot without reading or modifying its contents."""
    parts = [table]
    for candidate in (path, Path(f"{path}-wal")):
        if candidate.exists():
            stat = candidate.stat()
            parts.extend((str(stat.st_size), str(stat.st_mtime_ns)))
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def scan_sqlite_tables(settings: Settings) -> Iterator[ScannedFile]:
    path = settings.sqlite_database_path
    if not path:
        return
    path = path.resolve()
    if not path.is_file():
        return
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    policy = load_policy(settings)
    for table, project in SQLITE_TABLE_PROJECTS.items():
        display = Path(project) / f"{path.name}#{table}"
        if policy and not policy_allows(display, project, ".sqlite", policy):
            continue
        virtual_path = f"sqlite:{path}#{table}"
        yield ScannedFile(
            path=virtual_path,
            project=project,
            relative_path=f"database\\{path.name}#{table}",
            file_name=f"{path.name}#{table}",
            extension=".sqlite",
            content_hash=_sqlite_fingerprint(path, table),
            modified_at=modified_at,
            source_table=table,
            source_type="database_snapshot",
            source_id="sqlite",
        )


def _walk_candidates(settings: Settings):
    """One policy path for scanning, audits and deletion candidates."""
    max_bytes = settings.max_file_size_mb * 1024 * 1024
    excluded = {item.lower() for item in settings.excluded_dirs}
    extensions = {item.lower() for item in settings.supported_extensions}
    policy = load_policy(settings)
    for source in load_roots(settings):
        root = source.path.resolve()
        for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
            dirs[:] = [d for d in dirs if d.lower() not in excluded and not (Path(current) / d).is_symlink()]
            for name in files:
                if name.startswith("~$") or is_excluded_file_name(name):
                    continue
                path = Path(current) / name
                try:
                    if path.is_symlink() or path.suffix.lower() not in extensions:
                        continue
                    relative = path.resolve().relative_to(root)
                    if source.id == "generated":
                        project = "doc"
                        display = Path("doc/generated") / relative
                    else:
                        if len(relative.parts) < 2:
                            continue
                        project, display = relative.parts[0], relative
                    if not policy and source.id != "generated" and not is_allowed_document(path, root):
                        continue
                    if policy and not policy_allows(display, project, path.suffix.lower(), policy):
                        continue
                    yield source, path, display, project, max_bytes
                except (OSError, ValueError):
                    continue


def scan_files(settings: Settings) -> Iterator[ScannedFile]:
    for source, path, relative, project, max_bytes in _walk_candidates(settings):
        try:
            stat = path.stat()
            if stat.st_size > max_bytes:
                continue
            workspace_key = source.workspace_key
            # Files uploaded through the management UI are stored below
            # managed_uploads/<workspace_key>. This lets one generated-docs
            # source root safely serve multiple customer workspaces.
            relative_parts = Path(relative).parts
            if (source.id == "generated" and len(relative_parts) >= 4
                    and relative_parts[:3] == ("doc", "generated", "managed_uploads")):
                workspace_key = relative_parts[3].lower()
            yield ScannedFile(
                path=str(path.resolve()), project=project,
                relative_path=str(relative), file_name=path.name, extension=path.suffix.lower(),
                content_hash=sha256_file(path),
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                source_type=classify_source(project, path, source.id, path.suffix.lower()),
                source_id=source.id,
                workspace_key=workspace_key,
            )
        except (OSError, ValueError):
            continue
    yield from scan_sqlite_tables(settings)


def candidate_paths(settings: Settings) -> set[str]:
    """Return supported paths without reading their contents, preventing transient read errors from looking deleted."""
    found = {str(path.resolve()) for _, path, _, _, _ in _walk_candidates(settings)}
    found.update(item.path for item in scan_sqlite_tables(settings))
    return found


def list_projects(settings: Settings) -> list[str]:
    excluded = {item.lower() for item in settings.excluded_dirs}
    projects = set()
    policy = load_policy(settings)
    for source in load_roots(settings):
        if source.id == "generated":
            projects.add("doc")
        else:
            projects.update(item.name for item in source.path.iterdir()
                            if item.is_dir() and not item.is_symlink() and item.name.lower() not in excluded
                            and (not policy or policy.get("projects", {}).get(item.name, {}).get("enabled", False)))
    if settings.sqlite_database_path and settings.sqlite_database_path.is_file():
        for project in SQLITE_TABLE_PROJECTS.values():
            display = Path(project) / f"{settings.sqlite_database_path.name}#snapshot"
            if not policy or policy_allows(display, project, ".sqlite", policy):
                projects.add(project)
    return sorted(projects)
