"""Preview expired Phase 3 content; --apply explicitly removes consent-retained records."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.database.connection import SessionLocal  # noqa: E402
from app.services.screening_retention import delete_screening_content, expired_screenings  # noqa: E402
from app.services.audit_service import record_event  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    with SessionLocal() as db:
        sessions = expired_screenings(db)
        print(f'Expired screening content: {len(sessions)} session(s).')
        if args.apply:
            for session in sessions:
                app_id, sid = session.application_id, session.screening_session_id
                delete_screening_content(db, session)
                record_event(db, action='screening.retention_removed', application_id=app_id,
                    entity_type='screening', entity_id=str(sid), details={'content_removed': True})
            db.commit()
            print('Authorized retention cleanup completed. Pseudonymous approval/audit records are retained.')
        else:
            print('Preview only. Use --apply only after authorizing content deletion under the disclosed retention policy.')


if __name__ == '__main__':
    main()
