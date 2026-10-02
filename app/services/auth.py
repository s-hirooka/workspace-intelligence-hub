"""Password hashing and opaque database-backed browser sessions."""

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import AuthSession, OAuthLoginState, User

ROLES = {"admin", "operator", "viewer"}
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,100}$")
_ITERATIONS = 600_000


def _now():
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("パスワードは12文字以上にしてください")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations))
        return hmac.compare_digest(actual.hex(), expected)
    except (TypeError, ValueError):
        return False


def create_user(db: Session, username: str, password: str, display_name: str, role: str) -> User:
    username = username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        raise ValueError("利用者IDは半角英数字・ピリオド・ハイフン・下線で3文字以上にしてください")
    if role not in ROLES:
        raise ValueError("不正な権限です")
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError("同じ利用者IDが登録されています")
    user = User(username=username, display_name=display_name.strip()[:200], password_hash=hash_password(password), role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def users_exist(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(User)))


def authenticate(db: Session, username: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.username == username.strip().lower()))
    if not user or not user.active or not verify_password(password, user.password_hash):
        return None
    user.last_login_at = _now()
    db.commit()
    return user


def create_session(db: Session, user: User, hours: int) -> str:
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(user_id=user.id, token_hash=hashlib.sha256(token.encode()).hexdigest(),
                       expires_at=_now() + timedelta(hours=max(1, min(hours, 168)))))
    db.commit()
    return token


def resolve_session(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    now = _now()
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == digest))
    if not row or row.expires_at < now:
        if row:
            db.delete(row)
            db.commit()
        return None
    user = db.get(User, row.user_id)
    if not user or not user.active:
        return None
    row.last_seen_at = now
    db.commit()
    return user


def delete_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hashlib.sha256(token.encode()).hexdigest()))
        db.commit()

def google_login_enabled(settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def google_email_allowed(settings, email: str) -> bool:
    email = email.strip().lower()
    if email in settings.google_allowed_emails:
        return True
    domain = settings.google_allowed_domain.strip().lower().lstrip("@")
    return bool(domain and email.endswith("@" + domain))


def create_oauth_state(db: Session) -> tuple[str, str, str]:
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    db.add(OAuthLoginState(state_hash=hashlib.sha256(state.encode()).hexdigest(), nonce=nonce,
                           code_verifier=verifier, expires_at=_now() + timedelta(minutes=10)))
    db.commit()
    return state, nonce, verifier


def consume_oauth_state(db: Session, state: str) -> OAuthLoginState | None:
    digest = hashlib.sha256(state.encode()).hexdigest()
    row = db.get(OAuthLoginState, digest)
    if not row or row.expires_at < _now():
        if row:
            db.delete(row)
            db.commit()
        return None
    db.delete(row)
    db.commit()
    return row


def validated_google_identity(claims: dict) -> tuple[str, str]:
    """Return a verified Google subject and normalized email."""
    subject = str(claims.get("sub", "")).strip()
    email = str(claims.get("email", "")).strip().lower()
    verified = claims.get("email_verified")
    if verified is not True and str(verified).strip().lower() != "true":
        raise ValueError("Googleで確認済みのメールアドレスを取得できませんでした")
    if not subject or not email:
        raise ValueError("Googleで確認済みのメールアドレスを取得できませんでした")
    return subject, email


def google_user(db: Session, settings, claims: dict) -> User:
    subject, email = validated_google_identity(claims)
    existing = db.scalar(select(User).where(User.google_subject == subject))
    if existing:
        if not existing.active:
            raise ValueError("この利用者は停止されています")
        return existing
    by_email = db.scalar(select(User).where(User.email == email))
    if by_email:
        if not by_email.active:
            raise ValueError("この利用者は停止されています")
        by_email.google_subject = subject
        by_email.auth_provider = "google"
        db.commit()
        return by_email
    first_user = not users_exist(db)
    restrictions_configured = bool(settings.google_allowed_emails or settings.google_allowed_domain.strip())
    if not google_email_allowed(settings, email) and (restrictions_configured or not first_user):
        raise ValueError("このGoogleアカウントは利用許可されていません")
    base = re.sub(r"[^a-z0-9_.-]", "-", email.split("@", 1)[0].lower()).strip("-.") or "google-user"
    username, suffix = base[:90], 1
    while db.scalar(select(User).where(User.username == username)):
        suffix += 1
        username = f"{base[:85]}-{suffix}"
    user = User(username=username, display_name=str(claims.get("name", ""))[:200], password_hash="",
                email=email, auth_provider="google", google_subject=subject,
                role="admin" if first_user else "viewer")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user