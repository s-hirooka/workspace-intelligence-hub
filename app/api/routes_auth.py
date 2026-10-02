import base64
import hashlib
import hmac
import logging
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.session import get_db
from app.services.auth import (
    authenticate, consume_oauth_state, create_oauth_state, create_session,
    create_user, delete_session, google_login_enabled, google_user, users_exist,
)

router = APIRouter(prefix="/auth", tags=["認証"])
COOKIE_NAME = "workspace_session"
logger = logging.getLogger(__name__)


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=12, max_length=200)


class SetupRequest(Credentials):
    display_name: str = Field(default="管理者", max_length=200)


@router.get("/status")
def status(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    return {"setup_required": not users_exist(db), "authenticated": bool(user),
            "google_enabled": google_login_enabled(get_settings()),
            "user": ({"username": user.username, "display_name": user.display_name, "role": user.role}
                     if user else None)}


@router.post("/setup")
def setup(payload: SetupRequest, response: Response, db: Session = Depends(get_db)):
    if users_exist(db):
        raise HTTPException(status_code=409, detail="初期設定は完了しています")
    try:
        user = create_user(db, payload.username, payload.password, payload.display_name, "admin")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    token = create_session(db, user, get_settings().session_hours)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=False,
                        max_age=get_settings().session_hours * 3600)
    return {"ok": True, "user": {"username": user.username, "role": user.role}}


@router.post("/login")
def login(payload: Credentials, response: Response, db: Session = Depends(get_db)):
    user = authenticate(db, payload.username, payload.password)
    if not user:
        raise HTTPException(status_code=401, detail="利用者IDまたはパスワードが違います")
    token = create_session(db, user, get_settings().session_hours)
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", secure=False,
                        max_age=get_settings().session_hours * 3600)
    return {"ok": True, "user": {"username": user.username, "display_name": user.display_name, "role": user.role}}


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    delete_session(db, request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}

@router.get("/google/start")
def google_start(db: Session = Depends(get_db)):
    settings = get_settings()
    if not google_login_enabled(settings):
        raise HTTPException(status_code=503, detail="Googleログインはまだ設定されていません")
    state, nonce, verifier = create_oauth_state(db)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    query = urlencode({
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    })
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + query, status_code=302)


@router.get("/google/callback")
def google_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                    db: Session = Depends(get_db)):
    settings = get_settings()
    if error or not code or not state or not google_login_enabled(settings):
        return RedirectResponse("/?google_error=cancelled", status_code=302)
    saved = consume_oauth_state(db, state)
    if not saved:
        return RedirectResponse("/?google_error=state", status_code=302)
    try:
        response = httpx.post("https://oauth2.googleapis.com/token", data={
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
            "code_verifier": saved.code_verifier,
        }, timeout=20.0)
        response.raise_for_status()
        token = response.json().get("id_token")
        if not token:
            raise ValueError("ID token missing")
    except Exception as exc:
        logger.warning("Google OAuth token exchange failed: %s", type(exc).__name__)
        return RedirectResponse("/?google_error=token", status_code=302)
    try:
        claims = id_token.verify_oauth2_token(
            token, GoogleRequest(), settings.google_client_id, clock_skew_in_seconds=120
        )
    except Exception as exc:
        message = str(exc).lower()
        if "audience" in message:
            reason = "identity_audience"
        elif "expired" in message or "too early" in message or "clock" in message:
            reason = "identity_clock"
        elif "algorithm" in message or "cryptography" in message:
            reason = "identity_crypto"
        else:
            reason = "identity"
        logger.warning("Google ID token verification failed: %s (%s)", type(exc).__name__, reason)
        return RedirectResponse(f"/?google_error={reason}", status_code=302)
    if not hmac.compare_digest(str(claims.get("nonce", "")), saved.nonce):
        logger.warning("Google OAuth nonce verification failed")
        return RedirectResponse("/?google_error=nonce", status_code=302)
    try:
        user = google_user(db, settings, claims)
    except ValueError as exc:
        reason = "not_allowed" if "利用許可" in str(exc) else "account"
        logger.warning("Google account admission failed: %s", reason)
        return RedirectResponse(f"/?google_error={reason}", status_code=302)
    try:
        browser_token = create_session(db, user, settings.session_hours)
    except Exception as exc:
        logger.exception("Google browser session creation failed: %s", type(exc).__name__)
        return RedirectResponse("/?google_error=session", status_code=302)
    result = RedirectResponse("/", status_code=302)
    result.set_cookie(COOKIE_NAME, browser_token, httponly=True, samesite="strict", secure=False,
                      max_age=settings.session_hours * 3600)
    return result