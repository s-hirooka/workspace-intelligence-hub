"""Static evidence extraction. Never return or persist credential literals."""

import json
import re
import subprocess
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import delete, select

from app.db.models import CodeSymbol, ExternalResource, SystemRelation
from app.services.scanner import scan_files
from app.services.security import audit_item

ANALYZER = Path(__file__).resolve().parents[2] / "tools/csharp-analyzer/bin/Release/net9.0/WorkspaceAnalyzer.dll"
CODE_EXTENSIONS = {".cs", ".php", ".js", ".jsx", ".ts", ".tsx", ".sql"}


def _symbol(kind, name, line, parent=None, metadata=None):
    return {"symbol_type": kind, "symbol_name": name, "parent_symbol": parent,
            "start_line": line, "end_line": line, "metadata_json": metadata or {}}


def _matches(text, pattern, kind, group=1):
    for match in re.finditer(pattern, text, re.I | re.M):
        yield _symbol(kind, match.group(group), text.count("\n", 0, match.start()) + 1)


def mask_endpoint(url: str) -> str:
    """Keep only static domain/path evidence; no query, fragment or userinfo."""
    url = url.strip("'\"` ")
    if url.startswith("/"):
        path = url.split("?", 1)[0].split("#", 1)[0]
        return re.sub(r"(?i)\b(?:[0-9a-f]{24,}|sk-[\w-]{10,})\b", "[REDACTED]", path)[:300]
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    path = re.sub(r"(?i)\b(?:[0-9a-f]{24,}|sk-[\w-]{10,})\b", "[REDACTED]", parsed.path)
    return f"{parsed.scheme}://{parsed.hostname.lower()}{path}"[:300]


def extract_structure(path: Path, text: str):
    ext = path.suffix.lower()
    symbols, resources = [], []
    if ext == ".cs":
        if ANALYZER.is_file():
            result = subprocess.run(["dotnet", str(ANALYZER), str(path)], capture_output=True,
                                    text=True, timeout=30, check=True)
            symbols = json.loads(result.stdout)["symbols"]
        else:
            symbols = list(_matches(text, r"\b(?:class|interface|record|enum)\s+(\w+)", "type"))
            symbols += list(_matches(text, r"\b(?:public|private|protected)\s+(?:async\s+)?[\w<>?]+\s+(\w+)\s*\(", "method"))
    elif ext == ".php":
        for kind, pattern in [
            ("namespace", r"\bnamespace\s+([\w\\]+)"), ("class", r"\bclass\s+(\w+)"),
            ("function", r"\bfunction\s+(\w+)"), ("include", r"\b(?:include|require)(?:_once)?\s*\(?\s*['\"]([^'\"]+)"),
            ("wordpress_hook", r"\b(?:add_action|add_filter)\s*\(\s*['\"]([^'\"]+)"),
            ("shortcode", r"\badd_shortcode\s*\(\s*['\"]([^'\"]+)"),
            ("post_type", r"\bregister_post_type\s*\(\s*['\"]([^'\"]+)"),
            ("taxonomy", r"\bregister_taxonomy\s*\(\s*['\"]([^'\"]+)"),
        ]:
            symbols += list(_matches(text, pattern, kind))
        for match in re.finditer(r"register_rest_route\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]", text, re.I):
            route = f"{match.group(1).strip('/')}/{match.group(2).lstrip('/')}"
            tail = text[match.end():match.end() + 600].split(");", 1)[0]
            methods = re.search(r"['\"]methods['\"]\s*=>\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", tail, re.I)
            callback = re.search(r"['\"]callback['\"]\s*=>\s*['\"]([\w:]+)['\"]", tail, re.I)
            symbols.append(_symbol("rest_route", route, text.count("\n", 0, match.start()) + 1,
                                   metadata={"namespace": match.group(1), "route": match.group(2),
                                             "methods": methods.group(1).upper() if methods else None,
                                             "callback": callback.group(1) if callback else None}))
            resources.append({"resource_type": "wordpress", "resource_name": "REST route",
                              "endpoint_masked": mask_endpoint("/wp-json/" + route)})
        for match in re.finditer(r"\b(?:get_post_meta|update_post_meta|get_option|update_option|wp_remote_get|wp_remote_post|wpdb)\b", text, re.I):
            symbols.append(_symbol("wordpress_api", match.group(), text.count("\n", 0, match.start()) + 1))
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        for kind, pattern in [
            ("function", r"\b(?:export\s+)?(?:async\s+)?function\s+(\w+)"),
            ("class", r"\bclass\s+(\w+)"), ("import", r"\bimport\s+.*?\bfrom\s+['\"]([^'\"]+)"),
            ("export", r"\bexport\s+(?:default\s+)?(?:const|let|function|class)\s+(\w+)"),
            ("component", r"\b(?:const|function)\s+([A-Z]\w+)\s*(?:=|\()"),
            ("next_route", r"\bexport\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH)\s*\("),
            ("environment_variable", r"\b(?:process\.env|import\.meta\.env)\.([A-Z][A-Z0-9_]+)"),
        ]:
            symbols += list(_matches(text, pattern, kind))
        for match in re.finditer(r"\b(?:fetch|axios\.(?:get|post|put|delete))\s*\(", text, re.I):
            symbols.append(_symbol("http_call", match.group().split("(")[0], text.count("\n", 0, match.start()) + 1))
    elif ext == ".sql":
        for match in re.finditer(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\[`\"]?([\w.]+)[\]`\"]?\s*\((.*?)\)\s*;", text, re.I | re.S):
            columns = []
            for part in match.group(2).split(","):
                name = part.strip().split(" ", 1)[0].strip('`"[]')
                if re.fullmatch(r"[\w]+", name) and name.upper() not in {"PRIMARY", "FOREIGN", "CONSTRAINT", "KEY", "UNIQUE"}:
                    columns.append(name)
            symbols.append(_symbol("table", match.group(1), text.count("\n", 0, match.start()) + 1,
                                   metadata={"columns": columns[:100]}))
        for kind, pattern in [
            ("alter_table", r"\bALTER\s+TABLE\s+[\[`\"]?([\w.]+)"),
            ("view", r"\bCREATE\s+VIEW\s+[\[`\"]?([\w.]+)"),
            ("select_table", r"\b(?:FROM|JOIN)\s+[\[`\"]?([\w.]+)"),
            ("insert_table", r"\bINSERT\s+INTO\s+[\[`\"]?([\w.]+)"),
            ("update_table", r"\bUPDATE\s+[\[`\"]?([\w.]+)"),
            ("delete_table", r"\bDELETE\s+FROM\s+[\[`\"]?([\w.]+)"),
        ]:
            symbols += list(_matches(text, pattern, kind))
    for match in re.finditer(r"https?://[^\s'\"<>]+", text, re.I):
        endpoint = mask_endpoint(match.group())
        if endpoint:
            host = urlsplit(endpoint).hostname or ""
            kind = "wordpress" if "wp-json" in endpoint else "http_api"
            resources.append({"resource_type": kind, "resource_name": host,
                              "endpoint_masked": endpoint})
    for kind, pattern in [("sqlite", r"\b(?:SqliteConnection|Microsoft\.Data\.Sqlite|sqlite3)\b"),
                          ("database", r"\b(?:SqlConnection|MySqlConnection|wpdb)\b"),
                          ("filesystem", r"\b(?:File\.(?:Read|Write|Copy)|FileStream|file_get_contents)\b"),
                          ("http_api", r"\b(?:HttpClient|WebRequest|RestClient|wp_remote_get|wp_remote_post)\b"),
                          ("smtp", r"\b(?:SmtpClient|wp_mail|PHPMailer)\b"),
                          ("google", r"\b(?:GoogleCredential|GoogleClient|googleapis)\b"),
                          ("openai", r"\b(?:OpenAIClient|OpenAI\(|openai\.chat)\b")]:
        if re.search(pattern, text, re.I):
            resources.append({"resource_type": kind, "resource_name": kind, "endpoint_masked": None})
    return symbols, list({(r["resource_type"], r["endpoint_masked"], r["resource_name"]): r for r in resources}.values())


def relation_candidates(resources):
    """Only exact normalized endpoint matches create cross-project relations."""
    by_endpoint = defaultdict(list)
    for resource in resources:
        endpoint = resource.endpoint_masked
        if endpoint and endpoint != "/" and "[REDACTED]" not in endpoint:
            path = urlsplit(endpoint).path or endpoint
            if path.startswith("/wp-json/"):
                key = path.rstrip("/").lower()
            else:
                key = endpoint.rstrip("/").lower()
            by_endpoint[key].append(resource)
    output = []
    seen = set()
    for key, group in by_endpoint.items():
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if left.project == right.project:
                    continue
                identity = (left.project, right.project, left.file_path, right.file_path, key)
                if identity in seen:
                    continue
                seen.add(identity)
                output.append({"source_project": left.project, "target_project": right.project,
                               "relation_type": "wordpress_rest" if key.startswith("/wp-json/") else "shared_endpoint",
                               "relation_key": key, "confidence": 0.8,
                               "source_file": left.file_path, "target_file": right.file_path,
                               "evidence_json": {"basis": "exact_endpoint_match", "status": "candidate"}})
    return output


def reanalyze(db, settings):
    stats = {"files": 0, "symbols": 0, "external_resources": 0, "relations": 0, "errors": 0}
    for item in scan_files(settings):
        if item.extension not in CODE_EXTENSIONS:
            continue
        try:
            _chunks, decision = audit_item(item, settings)
            if decision.blocked:
                db.execute(delete(CodeSymbol).where(CodeSymbol.file_path == item.path))
                db.execute(delete(ExternalResource).where(ExternalResource.file_path == item.path))
                db.commit()
                continue
            # No source file is ever opened for write; never persist code literals.
            text = Path(item.path).read_text(encoding="utf-8", errors="replace")
            symbols, resources = extract_structure(Path(item.path), text)
            db.execute(delete(CodeSymbol).where(CodeSymbol.file_path == item.path))
            db.execute(delete(ExternalResource).where(ExternalResource.file_path == item.path))
            for symbol in symbols:
                db.add(CodeSymbol(project=item.project, file_path=item.path, language=item.extension[1:],
                                  content_hash=item.content_hash, **symbol))
            for resource in resources:
                db.add(ExternalResource(project=item.project, file_path=item.path, **resource))
            db.commit()
            stats["files"] += 1
            stats["symbols"] += len(symbols)
            stats["external_resources"] += len(resources)
        except Exception:
            db.rollback()
            stats["errors"] += 1
    # Clear stale structure from files no longer eligible; keep unreadable eligible files.
    db.execute(delete(SystemRelation))
    resources = db.scalars(select(ExternalResource)).all()
    relations = relation_candidates(resources)
    tables = defaultdict(list)
    for symbol in db.scalars(select(CodeSymbol).where(CodeSymbol.symbol_type.in_(["table", "select_table", "insert_table", "update_table"])) ).all():
        key = symbol.symbol_name.lower().strip('`"[]')
        if key and key not in {"wp_posts", "wp_options", "users", "posts"}:
            tables[key].append(symbol)
    for key, group in tables.items():
        if len(group) > 20:
            continue
        for i, left in enumerate(group):
            for right in group[i + 1:]:
                if left.project != right.project and len(relations) < 300:
                    relations.append({"source_project": left.project, "target_project": right.project,
                                      "relation_type": "shared_table", "relation_key": key, "confidence": 0.6,
                                      "source_file": left.file_path, "target_file": right.file_path,
                                      "evidence_json": {"basis": "shared_table_name", "status": "candidate"}})
    for relation in relations:
        db.add(SystemRelation(**relation))
    db.commit()
    stats["relations"] = len(relations)
    return stats
