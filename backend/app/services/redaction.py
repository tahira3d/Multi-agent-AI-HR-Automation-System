"""PII / protected-attribute redaction applied before resume text reaches an LLM.

Data minimisation (brief section 13): the scoring agent only needs job-related evidence,
so names, contact details, personal identifiers and protected-attribute lines are removed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"(https?://\S+|www\.\S+|\b(?:linkedin|github|gitlab|facebook|instagram|twitter)\.com/\S*)", re.I)
# Pakistani CNIC (12345-1234567-1) and other long digit IDs
_CNIC = re.compile(r"\b\d{5}-\d{7}-\d\b")
_PHONE = re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,4}\)?[\s\-.]?)\d{3,4}[\s\-.]?\d{3,4}(?!\w)")
# Whole lines that state protected / irrelevant attributes
_PROTECTED_LINE = re.compile(
    r"^[ \t]*(date of birth|dob|d\.o\.b|age|gender|sex|marital status|nationality|religion|"
    r"father'?s? name|husband'?s? name|spouse|children|caste|ethnicity|domicile|photo)[ \t]*[:\-].*$",
    re.I | re.M,
)


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)


def redact_resume_text(text: str, known_names: list[str] | None = None) -> RedactionResult:
    counts: dict[str, int] = {}

    def sub(pattern: re.Pattern[str], repl: str, label: str, value: str) -> str:
        new, n = pattern.subn(repl, value)
        if n:
            counts[label] = counts.get(label, 0) + n
        return new

    out = text
    out = sub(_PROTECTED_LINE, "[REDACTED PERSONAL ATTRIBUTE]", "protected_attribute_lines", out)
    out = sub(_EMAIL, "[EMAIL]", "emails", out)
    out = sub(_URL, "[URL]", "urls", out)
    out = sub(_CNIC, "[ID]", "national_ids", out)
    def redact_phone(match):
        if sum(c.isdigit() for c in match.group()) < 10:
            return match.group()
        counts["phones"] = counts.get("phones", 0) + 1
        return "[PHONE]"
    out = _PHONE.sub(redact_phone, out)

    for name in known_names or []:
        name = name.strip()
        if not name:
            continue
        # Deterministic order: full name first, then each name part (longest first).
        parts = sorted({p for p in re.split(r"\s+", name) if len(p) >= 2}, key=lambda p: (-len(p), p))
        for token in [name, *parts]:
            pattern = re.compile(r"\b" + re.escape(token) + r"\b", re.I)
            out = sub(pattern, "[CANDIDATE]", "names", out)

    # "First Last" must read the same as a single-token name after redaction.
    out = re.sub(r"(\[CANDIDATE\])(?:\s+\[CANDIDATE\])+", r"\1", out)

    return RedactionResult(text=out, counts=counts)
