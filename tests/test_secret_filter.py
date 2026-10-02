from app.services.secret_filter import sanitize_text


def test_secret_mask_assignment_and_json():
    assert "sk-test" not in sanitize_text("OPENAI_API_KEY=sk-test", ".env")
    result = sanitize_text('{"name":"demo","client_secret":"hidden"}', "config.json")
    assert "hidden" not in result


def test_php_wordpress_credentials_are_redacted():
    source = """define('DB_PASSWORD', 'db-secret');
new wpdb('db-user', 'db-pass', 'db-name', 'localhost');
acf_update_setting('google_api_key', 'maps-secret');"""
    result = sanitize_text(source, "plugin.php")
    assert "db-secret" not in result
    assert "db-user" not in result
    assert "db-pass" not in result
    assert "db-name" not in result
    assert "maps-secret" not in result
    assert "[REDACTED]" in result


def test_unsafe_sensitive_json_is_skipped():
    assert sanitize_text("not-json SECRET=x", "credentials.json") is None
