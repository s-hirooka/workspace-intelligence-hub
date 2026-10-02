import json
import re
from pathlib import Path
from typing import Any


SECRET_KEYS = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|access[_-]?token|refresh[_-]?token|"
    r"client[_-]?secret|connection[_-]?string|private[_-]?key)"
)
ASSIGNMENT = re.compile(
    r"(?im)((?:[\"']?[A-Z0-9_.-]*?(?:API[_-]?KEY|SECRET|PASSWORD|TOKEN|ACCESS[_-]?TOKEN|"
    r"REFRESH[_-]?TOKEN|CLIENT[_-]?SECRET|CONNECTION[_-]?STRING|PRIVATE[_-]?KEY)[A-Z0-9_.-]*[\"']?)"
    r"\s*[:=]\s*)(?!\[REDACTED\])([^\r\n,;}]+)"
)
PRIVATE_KEY_BLOCK = re.compile(r"(?s)-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----")
JSON_SECRET_FILE = re.compile(r"(?i)^(credentials|secrets)\.json$|^appsettings(?:\..+)?\.json$")
PHP_DEFINE_SECRET = re.compile(
    r"(?is)(define\s*\(\s*['\"](?:DB_USER|DB_PASSWORD|AUTH_KEY|SECURE_AUTH_KEY|"
    r"LOGGED_IN_KEY|NONCE_KEY|AUTH_SALT|SECURE_AUTH_SALT|LOGGED_IN_SALT|NONCE_SALT)"
    r"['\"]\s*,\s*['\"])(.*?)(['\"]\s*\))"
)
PHP_WPDB_CREDENTIALS = re.compile(
    r"(?is)new\s+wpdb\s*\(\s*(['\"])[^'\"]*\1\s*,\s*(['\"])[^'\"]*\2\s*,"
    r"\s*(['\"])[^'\"]*\3\s*,\s*(['\"])[^'\"]*\4\s*\)"
)
PHP_API_SETTING = re.compile(
    r"(?is)(acf_update_setting\s*\(\s*['\"]google_api_key['\"]\s*,\s*['\"])(.*?)(['\"]\s*\))"
)


def _redact_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if SECRET_KEYS.search(str(key)) else _redact_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    return value


def sanitize_text(text: str, file_name: str) -> str | None:
    """Mask likely secret values; return None when a sensitive JSON file is unsafe to parse."""
    if file_name.lower() == ".env":
        return ASSIGNMENT.sub(r"\1[REDACTED]", text)
    if file_name.lower().endswith(".json"):
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, UnicodeError):
            if JSON_SECRET_FILE.match(file_name):
                return None
        else:
            return json.dumps(_redact_json(parsed), ensure_ascii=False, indent=2)
    text = PRIVATE_KEY_BLOCK.sub("[REDACTED PRIVATE KEY]", ASSIGNMENT.sub(r"\1[REDACTED]", text))
    text = PHP_DEFINE_SECRET.sub(r"\1[REDACTED]\3", text)
    text = PHP_WPDB_CREDENTIALS.sub(
        "new wpdb('[REDACTED]', '[REDACTED]', '[REDACTED]', '[REDACTED]')", text
    )
    return PHP_API_SETTING.sub(r"\1[REDACTED]\3", text)


def is_sensitive_filename(path: str | Path) -> bool:
    return bool(JSON_SECRET_FILE.match(Path(path).name) or Path(path).name.lower() == ".env")
