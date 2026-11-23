"""Token validity, role permissions and database-enforced recruiter ownership."""
from __future__ import annotations

from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.permissions import Permission, has_permission
from app.core.security import decode_access_token
from app.database.connection import get_db
from app.database.models import Application, Requisition, User

bearer = HTTPBearer(auto_error=False)


def get_current_user(creds: HTTPAuthorizationCredentials | None = Depends(bearer),
                     db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(401, "Invalid or missing credentials", headers={"WWW-Authenticate": "Bearer"})
    if creds is None:
        raise unauthorized
    settings = get_settings()
    try:
        claims = decode_access_token(creds.credentials, secret=settings.require_jwt_secret(),
                                     algorithm=settings.jwt_algorithm)
        user = db.get(User, UUID(claims["sub"]))
        if user is None or not user.is_active or claims["ver"] != user.token_version:
            raise unauthorized
    except (jwt.InvalidTokenError, ValueError, TypeError, KeyError):
        raise unauthorized from None
    return user


def require(permission: Permission):
    def checker(user: User = Depends(get_current_user)) -> User:
        if not has_permission(user.role, permission):
            raise HTTPException(403, "You do not have permission for this action")
        return user
    return checker


def requisition_scope(user: User):
    if user.role == "admin":
        return True
    if user.role != "recruiter":
        return False
    return Requisition.created_by == user.user_id


def accessible_requisition(db: Session, requisition_id: UUID, user: User) -> Requisition:
    req = db.scalar(select(Requisition).where(
        Requisition.requisition_id == requisition_id, requisition_scope(user)))
    if req is None:
        raise HTTPException(404, "Requisition not found")
    return req


def accessible_application(db: Session, application_id: UUID, user: User) -> Application:
    app = db.scalar(select(Application).join(Requisition).where(
        Application.application_id == application_id, requisition_scope(user)))
    if app is None:
        raise HTTPException(404, "Application not found")
    return app
