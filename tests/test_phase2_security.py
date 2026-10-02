from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.routes_security import AuditRequest, dry_run
from app.config import Settings
from app.domain import ExtractedChunk, ScannedFile
from app.services.security import audit_item, inspect_text


def test_critical_secret_blocks_without_returning_value():
    marker = "PRIVATE" + " KEY"
    sample = f"-----BEGIN {marker}-----\nprivate\n-----END {marker}-----"
    decision = inspect_text(sample)
    assert decision.severity == "CRITICAL" and decision.blocked
    assert "private" not in repr(decision).lower()


def test_high_action_skip_review_or_redact():
    sample = "API_KEY=" + "superSecretValue"
    assert inspect_text(sample).blocked
    assert inspect_text(sample, "review_required").blocked
    assert not inspect_text(sample, "redact").blocked


def test_camel_case_password_with_spaces_is_blocked():
    decision = inspect_text('var wpPass = "sample words only";')
    assert decision.severity == "HIGH" and decision.blocked


def test_pii_detection_types():
    result = inspect_text("担当者名: 山田\nemail: a@example.com\n電話 03-1234-5678\n郵便 123-4567")
    assert {"email", "phone", "postal_code", "person_name_field"} <= set(result.pii_types)


def test_audit_item_skips_before_embedding(monkeypatch, tmp_path):
    source = tmp_path / "a.cs"
    source.write_text("API_KEY=" + "superSecretValue", encoding="utf-8")
    item = ScannedFile(str(source), "p", "p/a.cs", source.name, ".cs", "a" * 64, datetime.now(timezone.utc))
    monkeypatch.setattr("app.services.security.load_source", lambda *_: [ExtractedChunk(source.read_text())])
    chunks, decision = audit_item(item, Settings(source_root=tmp_path, _env_file=None))
    assert chunks == [] and decision.blocked


def test_dry_run_no_embedding_and_counts(monkeypatch, tmp_path):
    item = ScannedFile(str(tmp_path / "a.md"), "p", "p/a.md", "a.md", ".md", "x", datetime.now(timezone.utc))
    monkeypatch.setattr("app.api.routes_security._selected", lambda *_: iter([item]))
    monkeypatch.setattr("app.api.routes_security.audit_item", lambda *_: ([SimpleNamespace(text="hello world")], inspect_text("hello world")))
    monkeypatch.setattr("app.api.routes_security.candidate_paths", lambda *_: {item.path})
    db = SimpleNamespace(scalars=lambda *_: SimpleNamespace(all=lambda: []))
    result = dry_run(AuditRequest(), db)
    assert result["new"] == 1 and result["estimated_chunks"] == 1
    assert result["estimated_tokens"] > 0 and result["blocked"] == 0
