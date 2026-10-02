from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.auth import google_email_allowed, hash_password, verify_password
from app.services.backup import create_backup
from app.services.usage import record_chat_usage, record_embedding_usage


class FakeDb:
    def __init__(self):
        self.added = []
    def add(self, row):
        self.added.append(row)
    def flush(self):
        pass
    def commit(self):
        pass
    def refresh(self, _row):
        pass


def test_password_is_salted_and_verifiable():
    first = hash_password("correct-horse-battery")
    second = hash_password("correct-horse-battery")
    assert first != second
    assert verify_password("correct-horse-battery", first)
    assert not verify_password("wrong-password-value", first)


def test_short_password_is_rejected():
    with pytest.raises(ValueError):
        hash_password("too-short")


def test_openai_cost_ledger_uses_configured_rates():
    db = FakeDb()
    settings = Settings(_env_file=None, openai_chat_input_usd_per_million=0.15,
                        openai_chat_output_usd_per_million=0.60,
                        openai_embedding_input_usd_per_million=0.02)
    record_embedding_usage(db, settings, "text-embedding-3-small", 1_000_000, "index")
    record_chat_usage(db, settings, "gpt-4o-mini",
                      SimpleNamespace(prompt_tokens=1_000_000, completion_tokens=1_000_000))
    assert db.added[0].estimated_cost_usd == pytest.approx(0.02)
    assert db.added[1].estimated_cost_usd == pytest.approx(0.75)


def test_backup_writes_checksum_and_record(tmp_path, monkeypatch):
    db = FakeDb()
    settings = Settings(_env_file=None, backup_dir=tmp_path)
    monkeypatch.setattr("app.services.backup._run", lambda *_args, **_kwargs: b"postgres-backup")
    row = create_backup(db, settings, "admin", "test")
    assert (tmp_path / row.file_name).read_bytes() == b"postgres-backup"
    assert len(row.sha256) == 64
    assert row.file_size == len(b"postgres-backup")

def test_google_email_allowlist_and_domain():
    settings = Settings(_env_file=None, google_allowed_emails={"owner@example.com"},
                        google_allowed_domain="workspace.example")
    assert google_email_allowed(settings, "OWNER@example.com")
    assert google_email_allowed(settings, "staff@workspace.example")
    assert not google_email_allowed(settings, "outsider@example.net")


def test_google_login_requires_both_client_values():
    from app.services.auth import google_login_enabled
    assert not google_login_enabled(Settings(_env_file=None, google_client_id="id", google_client_secret=""))
    assert google_login_enabled(Settings(_env_file=None, google_client_id="id", google_client_secret="secret"))