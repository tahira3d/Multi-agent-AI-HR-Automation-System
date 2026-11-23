"""Private deterministic renderers for persisted interview evaluations."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any

from docx import Document
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.database.models import GeneratedReport, InterviewEvaluation, User
from app.services.audit_service import record_event
from app.services.interview_scoring_service import as_dict

MIME = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "json": "application/json",
}


def _payload(evaluation: InterviewEvaluation) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "label": "SIMULATED — decision support only",
        "evaluation": as_dict(evaluation),
        "method": "Scores use only cited, confirmed candidate statements. Delivery and protected characteristics are not scored.",
    }


def _render_pdf(payload: dict[str, Any]) -> bytes:
    buffer = io.BytesIO()
    page = canvas.Canvas(buffer, pagesize=letter)
    _, height = letter
    text = page.beginText(42, height - 42)
    text.setFont("Helvetica", 10)
    lines = [
        "SIMULATED INTERVIEW REPORT — HUMAN REVIEW REQUIRED",
        "",
        "Summary: "
        + json.dumps(
            {
                k: payload["evaluation"][k]
                for k in (
                    "overall_interview_score",
                    "evidence_coverage",
                    "confidence",
                    "recommendation_for_human_review",
                )
            }
        ),
        "",
        "Score breakdown:",
    ]
    for item in payload["evaluation"]["criteria"]:
        lines.append(
            f"{item['label']}: {item['status']}; level {item['anchor_level']}; score {item['score']}"
        )
        for evidence in item["evidence"]:
            lines.append("  Turn " + evidence["turn_id"] + ": " + evidence["quote"][:400])
    lines += ["", "Method and fairness: " + payload["method"]]
    for line in lines:
        for piece in [line[i : i + 105] for i in range(0, max(1, len(line)), 105)]:
            text.textLine(piece)
            if text.getY() < 48:
                page.drawText(text)
                page.showPage()
                text = page.beginText(42, height - 42)
                text.setFont("Helvetica", 10)
    page.drawText(text)
    page.save()
    return buffer.getvalue()


def _render_docx(payload: dict[str, Any]) -> bytes:
    doc = Document()
    doc.add_heading("SIMULATED INTERVIEW REPORT", 0)
    doc.add_paragraph("Decision support only — human review required.")
    doc.add_heading("Summary", 1)
    doc.add_paragraph(
        json.dumps(
            {
                k: payload["evaluation"][k]
                for k in (
                    "overall_interview_score",
                    "evidence_coverage",
                    "confidence",
                    "recommendation_for_human_review",
                )
            }
        )
    )
    doc.add_heading("Evidence by criterion", 1)
    for item in payload["evaluation"]["criteria"]:
        doc.add_heading(item["label"], 2)
        doc.add_paragraph(f"{item['status']}; level={item['anchor_level']}; score={item['score']}")
        for evidence in item["evidence"]:
            doc.add_paragraph(f"Turn {evidence['turn_id']}: {evidence['quote']}")
    doc.add_heading("Method and fairness", 1)
    doc.add_paragraph(payload["method"])
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


def get_or_create(db: Session, evaluation: InterviewEvaluation, fmt: str, user: User) -> GeneratedReport:
    existing = db.scalar(
        select(GeneratedReport)
        .where(GeneratedReport.evaluation_id == evaluation.evaluation_id, GeneratedReport.format == fmt)
        .order_by(GeneratedReport.version.desc())
    )
    if existing:
        return existing
    payload = _payload(evaluation)
    content = (
        json.dumps(payload, sort_keys=True, indent=2).encode()
        if fmt == "json"
        else (_render_pdf(payload) if fmt == "pdf" else _render_docx(payload))
    )
    report_id = __import__("uuid").uuid4()
    folder = get_settings().storage_dir / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{report_id}.{fmt}"
    path.write_bytes(content)
    report = GeneratedReport(
        report_id=report_id,
        evaluation_id=evaluation.evaluation_id,
        format=fmt,
        version=1,
        storage_reference=str(path),
        sha256=hashlib.sha256(content).hexdigest(),
        generated_by=user.user_id,
    )
    db.add(report)
    record_event(
        db,
        action="report.generated",
        actor_id=user.user_id,
        application_id=evaluation.application_id,
        entity_type="report",
        entity_id=str(report_id),
        details={"format": fmt, "sha256": report.sha256},
    )
    db.commit()
    db.refresh(report)
    return report
