"""Provision fictional accounts locally; never reset existing passwords."""
import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import inspect, select  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database.connection import SessionLocal, engine  # noqa: E402
from app.database.models import User  # noqa: E402

USERS = [
    ("Rina Recruiter", "recruiter@demo.test", "recruiter"),
    ("Ivan Interviewer", "interviewer@demo.test", "interviewer"),
    ("Ada Admin", "admin@demo.test", "admin"),
]


def main():
    get_settings().require_jwt_secret()
    if "alembic_version" not in inspect(engine).get_table_names():
        raise SystemExit("Run python -m alembic -c backend/alembic.ini upgrade head first.")
    password = os.getenv("DEMO_PASSWORD") or secrets.token_urlsafe(24)
    if len(password) < 12:
        raise SystemExit("DEMO_PASSWORD must contain at least 12 characters.")
    created = 0
    with SessionLocal() as db:
        for name, email, role in USERS:
            if db.scalar(select(User).where(User.email == email)):
                print(f"exists: {email} (existing password preserved)")
                continue
            db.add(User(name=name, email=email, role=role, password_hash=hash_password(password)))
            print(f"created: {email} ({role})")
            created += 1
        db.commit()
    if created:
        print("Local demo password (new accounts only):", password)
    else:
        print("No passwords changed. Use credentials from your original seeding run.")


if __name__ == "__main__":
    main()
