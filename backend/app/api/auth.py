from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, require
from app.core.config import get_settings
from app.core.permissions import Permission
from app.core.security import create_access_token, hash_password, needs_rehash, verify_password
from app.database.connection import get_db
from app.database.models import RefreshToken, User
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse, UserCreate, UserOut
from app.services.audit_service import record_event

router = APIRouter(prefix="/api", tags=["Authentication"])
_DUMMY_HASH = hash_password("not-a-real-account-password")
_REFRESH_LIFETIME = timedelta(days=7)
_MAX_FAILED_ATTEMPTS = 5


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _refresh_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_refresh_token(db: Session, user: User, now: datetime) -> str:
    raw_token = secrets.token_urlsafe(48)
    db.add(RefreshToken(user_id=user.user_id, token_hash=_refresh_hash(raw_token),
                        expires_at=now + _REFRESH_LIFETIME))
    return raw_token


def _access_token(user: User) -> str:
    settings = get_settings()
    return create_access_token(user_id=str(user.user_id), role=user.role,
                               secret=settings.require_jwt_secret(), algorithm=settings.jwt_algorithm,
                               expires_minutes=settings.jwt_expire_minutes, token_version=user.token_version)


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email).with_for_update())
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    now = datetime.now(timezone.utc)
    locked = user is not None and user.locked_until is not None and _aware(user.locked_until) > now
    if user is None or not valid or not user.is_active or locked:
        if user is not None and user.is_active and not locked:
            user.failed_attempts += 1
            if user.failed_attempts >= _MAX_FAILED_ATTEMPTS:
                delay_seconds = min(30 * (2 ** (user.failed_attempts - _MAX_FAILED_ATTEMPTS)), 900)
                user.locked_until = now + timedelta(seconds=delay_seconds)
        record_event(db, action="login_failed", actor_id=user.user_id if user else None,
                     details={"reason": "credentials_or_lockout"})
        db.commit()
        raise HTTPException(401, "Incorrect e-mail or password", headers={"WWW-Authenticate": "Bearer"})
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
    user.failed_attempts = 0
    user.locked_until = None
    token = _access_token(user)
    refresh_token = _issue_refresh_token(db, user, now)
    record_event(db, action="login_succeeded", actor_id=user.user_id)
    db.commit()
    return TokenResponse(access_token=token, refresh_token=refresh_token, role=user.role)


@router.post("/auth/refresh", response_model=TokenResponse)
def refresh(body: RefreshRequest, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    stored = db.scalar(select(RefreshToken).where(
        RefreshToken.token_hash == _refresh_hash(body.refresh_token)).with_for_update())
    if stored is None or stored.revoked_at is not None or _aware(stored.expires_at) <= now:
        raise HTTPException(401, "Invalid or expired refresh token", headers={"WWW-Authenticate": "Bearer"})
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, "Invalid or expired refresh token", headers={"WWW-Authenticate": "Bearer"})
    stored.revoked_at = now
    replacement = _issue_refresh_token(db, user, now)
    access = _access_token(user)
    record_event(db, action="refresh_token_rotated", actor_id=user.user_id)
    db.commit()
    return TokenResponse(access_token=access, refresh_token=replacement, role=user.role)


@router.post("/auth/logout", status_code=204)
def logout(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Revoke every currently issued token for this account."""
    db.execute(update(User).where(User.user_id == user.user_id).values(token_version=User.token_version + 1))
    db.execute(update(RefreshToken).where(RefreshToken.user_id == user.user_id,
        RefreshToken.revoked_at.is_(None)).values(revoked_at=datetime.now(timezone.utc)))
    record_event(db, action="logout", actor_id=user.user_id)
    db.commit()


@router.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


@router.post("/users", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, db: Session = Depends(get_db),
                admin: User = Depends(require(Permission.MANAGE_USERS))):
    if db.scalar(select(User).where(User.email == body.email)):
        raise HTTPException(409, "A user with this e-mail already exists")
    user = User(name=body.name, email=body.email, password_hash=hash_password(body.password), role=body.role)
    db.add(user)
    try:
        db.flush()
        record_event(db, action="user_created", actor_id=admin.user_id, entity_type="user",
                     entity_id=str(user.user_id), details={"role": body.role})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "A user with this e-mail already exists") from None
    return user
