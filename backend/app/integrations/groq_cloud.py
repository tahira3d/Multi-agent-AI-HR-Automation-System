"""GroqCloud structured outputs with a bounded, privacy-safe retry boundary."""
from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from typing import Any

from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, OpenAI,
    ContentFilterFinishReasonError, LengthFinishReasonError, RateLimitError,
)
from pydantic import ValidationError
from app.core.config import GROQ_BASE_URL, get_settings
from app.schemas.evaluation import LLMEvaluation, SourceReferencedEvaluation

logger = logging.getLogger(__name__)


class ScoringError(Exception):
    """The public message is safe to show to an authorized recruiter."""


class ProviderUnavailableError(ScoringError):
    pass


class ProviderAuthenticationError(ProviderUnavailableError):
    pass


class ProviderRateLimitError(ProviderUnavailableError):
    pass


GROQ_SETTING_NAMES = ("GROQ_API_KEY", "GROQ_BASE_URL", "GROQ_MODEL")


class GroqConfigurationError(ProviderUnavailableError):
    def __init__(self, missing_settings):
        self.missing_settings = tuple(name for name in GROQ_SETTING_NAMES if name in missing_settings)
        super().__init__("GroqCloud is not configured. Set: " + ", ".join(self.missing_settings))


class InvalidEvaluationError(ScoringError):
    pass


class GroqEvidenceProvider:
    def __init__(self, settings: Any = None, *, client: Any = None,
                 sleeper: Callable[[float], None] = time.sleep):
        cfg = settings or get_settings()
        key = cfg.groq_api_key
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        required = {"GROQ_API_KEY": key, "GROQ_BASE_URL": cfg.groq_base_url, "GROQ_MODEL": cfg.groq_model}
        missing = [name for name, value in required.items() if not str(value or "").strip()]
        if missing:
            raise GroqConfigurationError(missing)
        endpoint = str(cfg.groq_base_url).rstrip("/")
        if endpoint != GROQ_BASE_URL:
            raise ProviderUnavailableError("GROQ_BASE_URL must be https://api.groq.com/openai/v1.")
        self.model = str(cfg.groq_model).strip()
        self.max_retries = min(max(int(cfg.groq_max_retries), 0), 5)
        self.sleeper = sleeper
        self._closed = False
        self.client = client if client is not None else OpenAI(
            base_url=endpoint, api_key=key, timeout=float(cfg.groq_timeout_seconds), max_retries=0,
        )

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self.client.close()

    def generate(self, messages: list[dict[str, str]], *,
                 validator: Callable[[LLMEvaluation], None] | None = None) -> tuple[LLMEvaluation, str]:
        if self._closed:
            raise ProviderUnavailableError("The Groq client is closed; restart the evaluation service.")
        last_invalid = False
        last_rate_limited = False
        request_messages = [dict(message) for message in messages]
        for attempt in range(self.max_retries + 1):
            delay = min(2.0 ** attempt, 8.0)
            try:
                completion = self.client.beta.chat.completions.parse(
                    model=self.model, messages=list(request_messages), response_format=SourceReferencedEvaluation,
                    temperature=0, max_tokens=6000,
                )
                if not completion.choices:
                    raise InvalidEvaluationError("Groq returned no evaluation.")
                message = completion.choices[0].message
                if message.refusal or message.parsed is None:
                    raise InvalidEvaluationError("Groq did not return a usable structured evaluation.")
                output = (message.parsed.to_evaluation() if isinstance(message.parsed, SourceReferencedEvaluation)
                          else LLMEvaluation.model_validate(message.parsed))
                if validator:
                    validator(output)
                return output, str(getattr(completion, "model", "") or self.model)
            except (ValidationError, InvalidEvaluationError, LengthFinishReasonError, ContentFilterFinishReasonError) as exc:
                last_invalid, last_rate_limited = True, False
                reason = {ValidationError: "schema_validation", InvalidEvaluationError: "evidence_validation",
                          LengthFinishReasonError: "response_truncated", ContentFilterFinishReasonError: "content_filter"}.get(type(exc), "structured_validation")
                logger.warning("Groq structured evaluation rejected; reason=%s; attempt=%d", reason, attempt + 1)
                request_messages.append({"role": "system", "content": (
                    "The previous output failed validation. Return every approved criterion exactly once with valid scores. "
                    "For each evidence entry use integer source_line_start and source_line_end from provided line labels. "
                    "For [L0001] use 1 for both fields. Use a single line or a small contiguous range. "
                    "Do not rewrite quotations, combine nonadjacent lines, or reference excluded text. "
                    "For not_found return score 0 and an empty evidence list. Explain uncertainty for human review."
                )})
            except RateLimitError as exc:
                last_invalid, last_rate_limited = False, True
                logger.warning("Groq rate limit reached; attempt=%d", attempt + 1)
                try:
                    retry_after = float(exc.response.headers.get("retry-after", ""))
                except (TypeError, ValueError):
                    retry_after = -1.0
                if math.isfinite(retry_after) and retry_after >= 0:
                    if retry_after > 8:
                        # Do not retry earlier than requested or block the API beyond the existing delay bound.
                        raise ProviderRateLimitError("GroqCloud rate limit exceeded. Retry later; no score was saved.") from None
                    delay = retry_after
            except (APIConnectionError, APITimeoutError):
                last_invalid, last_rate_limited = False, False
                logger.warning("Groq temporarily unavailable; attempt=%d", attempt + 1)
            except APIStatusError as exc:
                if exc.status_code in {401, 403}:
                    logger.warning("Groq authentication or access failed; status=%d", exc.status_code)
                    raise ProviderAuthenticationError(
                        "GroqCloud authentication failed. Check GROQ_API_KEY and model access."
                    ) from None
                if exc.status_code not in {408, 409, 429, 498} and exc.status_code < 500:
                    logger.warning("Groq request failed; status=%d", exc.status_code)
                    raise ProviderUnavailableError(
                        "Groq rejected the request. Check model access and structured-output support."
                    ) from None
                last_invalid, last_rate_limited = False, exc.status_code == 429
                logger.warning("Groq temporarily unavailable; status=%d attempt=%d", exc.status_code, attempt + 1)
            if attempt < self.max_retries:
                self.sleeper(delay)
        if last_invalid:
            raise InvalidEvaluationError(
                "Groq could not provide a valid, traceable evaluation after bounded retries. Human review is required."
            ) from None
        if last_rate_limited:
            raise ProviderRateLimitError("GroqCloud rate limit exceeded. Retry later; no score was saved.") from None
        raise ProviderUnavailableError(
            "GroqCloud is temporarily unavailable after bounded retries. Retry later; no score was saved."
        ) from None
