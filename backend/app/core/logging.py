"""JSON logging with correlation context and conservative PII redaction."""
from __future__ import annotations

import contextvars
import json
import logging
import re
from datetime import datetime, timezone

correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="-")
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+[A-Za-z0-9._~-]+", re.IGNORECASE)
_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gsk_[A-Za-z0-9_-]{12,})\b")


def redact(value: str) -> str:
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _BEARER.sub("Bearer [REDACTED]", value)
    return _SECRET.sub("[REDACTED_SECRET]", value)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": correlation_id.get(),
        }
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(logging.INFO)
