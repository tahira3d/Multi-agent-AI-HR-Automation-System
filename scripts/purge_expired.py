"""Preview expired closed applications; --apply explicitly deletes their private data."""
import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import select  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.status import Status  # noqa: E402
from app.database.connection import SessionLocal  # noqa: E402
from app.database.models import Application, User  # noqa: E402
from app.services.retention import purge_application  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--admin-email", required=True)
    args = parser.parse_args()
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.retention_days)
    with SessionLocal() as db:
        admin = db.scalar(select(User).where(User.email == args.admin_email.lower(), User.role == "admin", User.is_active.is_(True)))
        if not admin:
            raise SystemExit("An active administrator account is required.")
        applications = db.scalars(select(Application).where(
            Application.current_status == Status.CLOSED, Application.updated_at < cutoff,
            Application.processing_token.is_(None))).all()
        print(f"Expired closed applications: {len(applications)}. Apply deletion: {args.apply}")
        if args.apply:
            for application in applications:
                purge_application(db, application, admin)


if __name__ == "__main__":
    main()
