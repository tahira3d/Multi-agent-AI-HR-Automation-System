"""Private, per-file transactional multi-resume intake."""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import accessible_requisition, require
from app.core.config import get_settings
from app.core.permissions import Permission
from app.core.status import Status
from app.database.connection import get_db
from app.database.models import Application, Candidate, Requisition, ResumeFile, User
from app.schemas.application import UploadItemResult, UploadResponse
from app.services.audit_service import record_event
from app.services.contact_extractor import extract_contact, name_from_filename
from app.services.resume_parser import parse_resume

router = APIRouter(prefix="/api/requisitions", tags=["Private resume intake"])
MIME_TYPES = {
    "pdf": {"application/pdf", "application/octet-stream"},
    "docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/octet-stream", "application/zip"},
}


@router.post("/{requisition_id}/resumes", response_model=UploadResponse, status_code=201)
async def upload_resumes(requisition_id: UUID, files: list[UploadFile] = File(...),
                         db: Session = Depends(get_db), user: User = Depends(require(Permission.UPLOAD_RESUMES))):
    settings = get_settings()
    req = accessible_requisition(db, requisition_id, user)
    if req.status != "open":
        raise HTTPException(409, "Resume intake requires an open requisition")
    if not 1 <= len(files) <= 50:
        raise HTTPException(413, "Supply between 1 and 50 resumes per upload")
    results = []
    storage_root = settings.storage_dir.resolve()
    storage_root.mkdir(parents=True, exist_ok=True)
    for upload in files:
        filename = (upload.filename or "unnamed").replace("\\", "/").split("/")[-1][:200]
        filename = re.sub(r"[\x00-\x1f\x7f]", "_", filename)
        stored_path: Path | None = None
        content = await upload.read(settings.max_upload_bytes + 1)
        await upload.close()
        parsed = parse_resume(filename, content, max_bytes=settings.max_upload_bytes)
        mime = (upload.content_type or "application/octet-stream").split(";")[0].lower()
        if parsed.status != "rejected" and mime not in MIME_TYPES.get(parsed.file_type, set()):
            results.append(UploadItemResult(filename=filename, outcome="rejected", reason_code="mime_mismatch",
                                            message="The declared MIME type is inconsistent with PDF/DOCX content."))
            continue
        if parsed.status == "rejected":
            results.append(UploadItemResult(filename=filename, outcome="rejected",
                                            reason_code=parsed.reason_code, message=parsed.message))
            continue
        try:
            # A savepoint isolates individual failures within a bulk upload.
            with db.begin_nested():
                # Serialize intake for a requisition across API workers before checking duplicates.
                db.execute(update(Requisition).where(Requisition.requisition_id == req.requisition_id)
                           .values(updated_at=Requisition.updated_at))
                duplicate = db.scalar(select(ResumeFile).join(Application).where(
                    Application.requisition_id == req.requisition_id, ResumeFile.sha256 == parsed.sha256))
                if duplicate:
                    results.append(UploadItemResult(filename=filename, outcome="duplicate",
                        duplicate_of_application_id=duplicate.application_id,
                        message="An identical resume already exists for this requisition."))
                    continue
                contact = extract_contact(parsed.text) if parsed.text else None
                candidate = db.scalar(select(Candidate).where(Candidate.email == contact.email)) if contact and contact.email else None
                if candidate:
                    existing = db.scalar(select(Application).where(
                        Application.candidate_id == candidate.candidate_id, Application.requisition_id == req.requisition_id))
                    if existing:
                        results.append(UploadItemResult(filename=filename, outcome="duplicate",
                            duplicate_of_application_id=existing.application_id,
                            message="This candidate already has an application for this requisition."))
                        continue
                if candidate is None:
                    candidate = Candidate(full_name=(contact.name if contact and contact.name else name_from_filename(filename)),
                                          email=contact.email if contact else None, phone=contact.phone if contact else None)
                    db.add(candidate)
                    db.flush()
                needs_review = parsed.status == "needs_manual_review"
                app = Application(candidate_id=candidate.candidate_id, requisition_id=req.requisition_id,
                                  current_status=(Status.NEEDS_MANUAL_REVIEW if needs_review else Status.RESUME_RECEIVED).value)
                db.add(app)
                db.flush()
                storage_key = f"{req.requisition_id}/{uuid4().hex}.{parsed.file_type}"
                stored_path = (storage_root / storage_key).resolve()
                if not stored_path.is_relative_to(storage_root):
                    raise ValueError("Unsafe internal storage reference")
                stored_path.parent.mkdir(parents=True, exist_ok=True)
                stored_path.write_bytes(content)
                db.add(ResumeFile(application_id=app.application_id, original_filename=filename,
                    storage_key=storage_key, sha256=parsed.sha256, file_size=len(content),
                    file_type=parsed.file_type, parse_status=parsed.status, reason_code=parsed.reason_code,
                    message=parsed.message, warnings=parsed.warnings, extracted_text=parsed.text or None,
                    uploaded_by=user.user_id))
                record_event(db, action="resume_uploaded", actor_id=user.user_id, application_id=app.application_id,
                    entity_type="application", entity_id=str(app.application_id),
                    details={"parse_status": parsed.status, "reason": parsed.reason_code,
                             "warnings": parsed.warnings, "status": app.current_status})
                result = UploadItemResult(filename=filename, outcome="needs_manual_review" if needs_review else "accepted",
                    application_id=app.application_id, reason_code=parsed.reason_code,
                    message=parsed.message, warnings=parsed.warnings)
            db.commit()
            results.append(result)
        except (IntegrityError, OSError):
            db.rollback()
            if stored_path and stored_path.is_file():
                stored_path.unlink()
            results.append(UploadItemResult(filename=filename, outcome="rejected", reason_code="storage_or_duplicate_conflict",
                                            message="The file could not be saved or a simultaneous upload already created this application. Retry safely."))
    return UploadResponse(requisition_id=req.requisition_id, results=results,
        accepted=sum(r.outcome == "accepted" for r in results),
        needs_manual_review=sum(r.outcome == "needs_manual_review" for r in results),
        rejected=sum(r.outcome == "rejected" for r in results),
        duplicates=sum(r.outcome == "duplicate" for r in results))
