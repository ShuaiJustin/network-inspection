import secrets
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.device import ActivityLog, AdminUser
from app.services.security import hash_admin_password, verify_admin_password
from app.services.time import utc_for_api


router = APIRouter()
SESSION_TOKENS: dict[str, datetime] = {}
SESSION_LOCK = Lock()
AUTH_ATTEMPT_LOCK = Lock()
SESSION_DURATION = timedelta(hours=12)
MAX_LOGIN_FAILURES = 5
LOCK_DURATION = timedelta(minutes=5)
MAX_SESSION_TOKENS = 8


class Credentials(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=256)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Username cannot be blank")
        return value


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=8, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


def require_session(request: Request) -> None:
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    now = datetime.utcnow()
    with SESSION_LOCK:
        expires_at = SESSION_TOKENS.get(token)
        valid = bool(token and expires_at and expires_at > now)
        if not valid and token:
            SESSION_TOKENS.pop(token, None)
    if not valid:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Administrator login required")


def clear_sessions() -> None:
    with SESSION_LOCK:
        SESSION_TOKENS.clear()


def _create_session(now: datetime) -> tuple[str, datetime]:
    expires_at = now + SESSION_DURATION
    with SESSION_LOCK:
        expired_tokens = [
            key for key, token_expires_at in SESSION_TOKENS.items()
            if token_expires_at <= now
        ]
        for expired_token in expired_tokens:
            SESSION_TOKENS.pop(expired_token, None)
        while len(SESSION_TOKENS) >= MAX_SESSION_TOKENS:
            oldest_token = min(SESSION_TOKENS, key=SESSION_TOKENS.get)
            SESSION_TOKENS.pop(oldest_token, None)
        token = secrets.token_urlsafe(32)
        SESSION_TOKENS[token] = expires_at
    return token, expires_at


@router.get("/status")
def auth_status(db: Session = Depends(get_db)):
    return {"initialized": db.query(AdminUser).first() is not None}


@router.post("/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap(credentials: Credentials, db: Session = Depends(get_db)):
    if db.query(AdminUser).first():
        raise HTTPException(status_code=409, detail="Administrator has already been initialized")
    admin = AdminUser(id=1, username=credentials.username.strip(), password_hash=hash_admin_password(credentials.password))
    db.add(admin)
    db.add(ActivityLog(event="admin_initialized", message="Local administrator initialized"))
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Administrator has already been initialized") from exc
    return {"ok": True}


@router.post("/login")
def login(credentials: Credentials, db: Session = Depends(get_db)):
    with AUTH_ATTEMPT_LOCK:
        admin = db.query(AdminUser).filter(AdminUser.username == credentials.username.strip()).first()
        now = datetime.utcnow()
        if not admin or (admin.locked_until and admin.locked_until > now):
            db.add(ActivityLog(level="WARNING", event="login_failed", message="Local administrator login failed"))
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid username or password")
        if not verify_admin_password(admin.password_hash, credentials.password):
            admin.failed_attempts += 1
            if admin.failed_attempts >= MAX_LOGIN_FAILURES:
                admin.failed_attempts = 0
                admin.locked_until = now + LOCK_DURATION
            db.add(ActivityLog(level="WARNING", event="login_failed", message="Local administrator login failed"))
            db.commit()
            raise HTTPException(status_code=401, detail="Invalid username or password")
        admin.failed_attempts = 0
        admin.locked_until = None
        token, expires_at = _create_session(now)
        db.add(ActivityLog(event="login_success", message="Local administrator logged in"))
        db.commit()
    return {"access_token": token, "expires_at": utc_for_api(expires_at)}


@router.get("/session", dependencies=[Depends(require_session)], status_code=status.HTTP_204_NO_CONTENT)
def verify_session() -> Response:
    """Confirm a stored token still maps to a live session in this process."""
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/logout", dependencies=[Depends(require_session)], status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request):
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    with SESSION_LOCK:
        SESSION_TOKENS.pop(token, None)


@router.post("/change-password", dependencies=[Depends(require_session)], status_code=status.HTTP_204_NO_CONTENT)
def change_password(payload: PasswordChange, db: Session = Depends(get_db)) -> Response:
    with AUTH_ATTEMPT_LOCK:
        admin = db.query(AdminUser).first()
        if not admin or not verify_admin_password(admin.password_hash, payload.current_password):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
        admin.password_hash = hash_admin_password(payload.new_password)
        admin.failed_attempts = 0
        admin.locked_until = None
        clear_sessions()
        db.add(ActivityLog(event="admin_password_changed", message="Local administrator password changed"))
        db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
