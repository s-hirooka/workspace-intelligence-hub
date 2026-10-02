"""Value-free security classification performed before outbound Embeddings calls."""

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.domain import ScannedFile
from app.loaders import load_source
from app.services.chunker import chunk_extracted
from app.services.governance import load_policy

CRITICAL = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", re.I),
    re.compile(r"\b(?:sk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r'"private_key"\s*:\s*"-----BEGIN', re.I),
)
HIGH = (
    # Catch camelCase credential variables and application passwords containing spaces.
    re.compile(r"(?i)\b[A-Za-z_][A-Za-z0-9_]*(?:pass(?:word)?|secret|token|api[_-]?key)\b\s*[:=]\s*['\"](?!\[REDACTED\])[^'\"\r\n]{4,}['\"]"),
    re.compile(r"(?i)\b(?:api[_-]?key|client[_-]?secret|password|access[_-]?token|refresh[_-]?token|connection[_-]?string|authorization|cookie|basic[_ ]auth|aws_secret_access_key|google_api_key)\b\s*[:=]\s*['\"]?(?!\[REDACTED\])[^\s,'\";}\]]{4,}"),
    re.compile(r"(?i)\b(?:DB_PASSWORD|AUTH_KEY|SECRET_KEY)\b\s*[,=:]\s*['\"](?!\[REDACTED\])[^'\"]{4,}"),
)
PII = {
    "email": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I),
    "phone": re.compile(r"(?<!\d)(?:0\d{1,4}[-ー]?\d{1,4}[-ー]?\d{3,4})(?!\d)"),
    "postal_code": re.compile(r"(?<!\d)\d{3}-\d{4}(?!\d)"),
    "ip_address": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    "person_name_field": re.compile(r"(?i)(?:氏名|担当者名|customer_name|contact_name)\s*[:=]"),
    "address_field": re.compile(r"(?i)(?:住所|所在地|address)\s*[:=]"),
    "customer_id": re.compile(r"(?i)(?:顧客ID|customer[_-]?id)\s*[:=]"),
    "account_number": re.compile(r"(?i)(?:口座番号|account[_-]?number)\s*[:=]\s*\d{5,}"),
    "corporate_contact": re.compile(r"(?:法人担当者|会社担当者)\s*[:=]"),
}


@dataclass
class SecurityDecision:
    severity: str
    action: str
    pii_types: list[str]
    finding_types: list[str]

    @property
    def blocked(self):
        return self.action not in {"allow", "redact"}


def inspect_text(text: str, high_action: str = "skip") -> SecurityDecision:
    pii = [name for name, pattern in PII.items() if pattern.search(text)]
    if any(pattern.search(text) for pattern in CRITICAL):
        return SecurityDecision("CRITICAL", "skip", pii, ["credential_material"])
    if any(pattern.search(text) for pattern in HIGH):
        if high_action not in {"skip", "redact", "review_required"}:
            raise ValueError("Invalid high_action")
        return SecurityDecision("HIGH", high_action, pii, ["credential_assignment"])
    return SecurityDecision("MEDIUM" if pii else "LOW", "allow", pii, ["pii"] if pii else [])


def audit_item(item: ScannedFile, settings: Settings):
    """Return (safe chunks, decision); never retain or log a detected secret."""
    extracted = load_source(item, settings)
    policy = load_policy(settings)
    action = policy.get("security", {}).get("high_action", "skip")
    decision = inspect_text("\n".join(part.text for part in extracted), action)
    if decision.severity == "CRITICAL" or decision.action in {"skip", "review_required"}:
        return [], decision
    chunks = chunk_extracted(extracted, Path(item.file_name), settings)
    # Redaction must remove ALL detectable high/critical patterns, or fail closed.
    for chunk in chunks:
        residual = inspect_text(chunk.text)
        if residual.severity in {"HIGH", "CRITICAL"}:
            return [], SecurityDecision("CRITICAL", "skip", decision.pii_types, ["unsafe_after_redaction"])
    return chunks, decision
