"""Resume validation and text extraction (FR-02).

Never invents content: anything unreadable becomes `needs_manual_review` or `rejected`
with a machine-readable reason code and a human-readable message.

Outcome vocabulary
- parsed               text extracted, ready for scoring
- needs_manual_review  accepted but a human must look (scanned/empty/encrypted/corrupt)
- rejected             not accepted (unsupported type, too large, empty, content/extension mismatch)
"""
from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePath

from app.services.injection_guard import scan_for_injection

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
MIN_TEXT_CHARS = 50  # below this a PDF is treated as scanned / image-only

MESSAGES = {
    "unsupported_type": "Only PDF and DOCX resumes are supported.",
    "empty_file": "The file is empty.",
    "file_too_large": "The file exceeds the maximum allowed size.",
    "content_mismatch": "The file content does not match its extension.",
    "password_protected": "The PDF is password-protected; ask the candidate for an unlocked copy.",
    "unreadable": "The file is corrupt or could not be read.",
    "scanned_or_image_only": "No selectable text found (likely a scan). OCR or manual review is required.",
    "empty_document": "The document contains no readable text.",
}


@dataclass
class ParseResult:
    status: str  # parsed | needs_manual_review | rejected
    text: str = ""
    reason_code: str | None = None
    message: str | None = None
    warnings: list[str] = field(default_factory=list)
    sha256: str = ""
    page_count: int | None = None
    file_type: str | None = None


def _fail(status: str, code: str, sha: str, file_type: str | None = None) -> ParseResult:
    return ParseResult(
        status=status,
        reason_code=code,
        message=MESSAGES[code],
        sha256=sha,
        file_type=file_type,
    )


def parse_resume(filename: str, content: bytes, *, max_bytes: int = 5 * 1024 * 1024) -> ParseResult:
    sha = hashlib.sha256(content).hexdigest()
    ext = PurePath(filename or "").suffix.lower()

    if ext not in ALLOWED_EXTENSIONS:
        return _fail("rejected", "unsupported_type", sha)
    file_type = ext.lstrip(".")
    if len(content) == 0:
        return _fail("rejected", "empty_file", sha, file_type)
    if len(content) > max_bytes:
        return _fail("rejected", "file_too_large", sha, file_type)

    if ext == ".pdf":
        result = _parse_pdf(content, sha)
    else:
        result = _parse_docx(content, sha)
    result.file_type = file_type

    if result.status == "parsed":
        flags = scan_for_injection(result.text)
        if flags:
            result.warnings.append(
                "possible_prompt_injection: " + ", ".join(flags)
                + " (treated as candidate content, not instructions)"
            )
    return result


def _parse_pdf(content: bytes, sha: str) -> ParseResult:
    if not content.lstrip().startswith(b"%PDF-"):
        return _fail("rejected", "content_mismatch", sha, "pdf")
    try:
        import pymupdf

        with pymupdf.open(stream=content, filetype="pdf") as document:
            if document.needs_pass and not document.authenticate(""):
                return _fail("needs_manual_review", "password_protected", sha, "pdf")
            if len(document) > 100:
                return _fail("needs_manual_review", "unreadable", sha, "pdf")
            pages = [page.get_text("text", sort=True) for page in document]
    except Exception:
        return _fail("needs_manual_review", "unreadable", sha, "pdf")

    text = "\n".join(pages).strip()
    if len(text) < MIN_TEXT_CHARS:
        result = _fail("needs_manual_review", "scanned_or_image_only", sha, "pdf")
        result.page_count = len(pages)
        return result
    return ParseResult(status="parsed", text=text, sha256=sha, page_count=len(pages))


def _parse_docx(content: bytes, sha: str) -> ParseResult:
    if not zipfile.is_zipfile(io.BytesIO(content)):
        return _fail("rejected", "content_mismatch", sha, "docx")
    try:
        from docx import Document

        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            members = archive.infolist()
            if len(members) > 2000 or sum(item.file_size for item in members) > 30 * 1024 * 1024:
                return _fail("needs_manual_review", "unreadable", sha, "docx")
            if any("vbaproject" in item.filename.lower() for item in members):
                return _fail("rejected", "content_mismatch", sha, "docx")

        document = Document(io.BytesIO(content))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    except Exception:
        return _fail("needs_manual_review", "unreadable", sha, "docx")

    text = "\n".join(parts).strip()
    if not text:
        return _fail("needs_manual_review", "empty_document", sha, "docx")
    return ParseResult(status="parsed", text=text, sha256=sha)
