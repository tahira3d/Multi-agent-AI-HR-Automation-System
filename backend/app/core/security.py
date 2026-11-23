"""Argon2id passwords, legacy scrypt verification, and expiring revocable JWTs."""
from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, stored: str) -> bool:
    try:
        if stored.startswith("$argon2"):
            return _hasher.verify(stored, password)
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (VerificationError, InvalidHashError, ValueError, TypeError):
        return False


def needs_rehash(stored: str) -> bool:
    return not stored.startswith("$argon2") or _hasher.check_needs_rehash(stored)


def create_access_token(*, user_id: str, role: str, secret: str, algorithm: str = "HS256",
                        expires_minutes: int = 60, token_version: int = 0) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": user_id, "role": role, "ver": token_version, "jti": str(uuid4()),
                       "iat": int(now.timestamp()),
                       "exp": int((now + timedelta(minutes=expires_minutes)).timestamp())},
                      secret, algorithm=algorithm)


def decode_access_token(token: str, *, secret: str, algorithm: str = "HS256") -> dict:
    return jwt.decode(token, secret, algorithms=[algorithm],
                      options={"require": ["exp", "sub", "iat", "jti", "ver"]})
