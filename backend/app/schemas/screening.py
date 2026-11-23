"""Validated Phase 3 API inputs. No provider credentials are part of these schemas."""
from typing import Literal
from uuid import UUID
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from app.core.screening_config import get_voice_settings


class RequestBody(BaseModel):
    model_config = ConfigDict(extra='forbid', populate_by_name=True)
    request_id: str = Field(min_length=8, max_length=128)


class ApprovedQuestion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(min_length=5, max_length=1200)
    topic: str = Field(default='job_requirement', min_length=1, max_length=100)
    source_reference: str = Field(default='hr_approved', max_length=240)
    required: bool = True


class StartBody(RequestBody):
    approved_questions: list[ApprovedQuestion] = Field(min_length=1, max_length=12)
    language: Literal['en', 'ur'] = Field(default_factory=lambda: get_voice_settings().screening_default_language)


class ConsentBody(RequestBody):
    status: Literal['accepted', 'declined', 'human_requested', 'reschedule_requested', 'stopped'] = Field(validation_alias=AliasChoices('decision', 'status'))
    retain_transcript: bool = False
    retain_audio: Literal[False] = False
    language: Literal['en', 'ur'] | None = None


class CorrectionBody(RequestBody):
    answer_id: UUID
    corrected_text: str | None = Field(default=None, max_length=12000)
    confirm: Literal[True] = True


class ControlBody(RequestBody):
    action: Literal['pause', 'resume', 'stop', 'human', 'reschedule', 'clarify', 'skip', 'job_question']
    reason: str = Field(default='', max_length=2000)
    text: str = Field(default='', max_length=1000)
    question_id: UUID | None = None


class DecisionBody(RequestBody):
    decision: Literal['approve', 'hold', 'close', 'clarify', 'clarification']
    reason: str = Field(min_length=3, max_length=2000)
    reviewed_report: bool = Field(default=False, validation_alias=AliasChoices('review_acknowledged', 'reviewed_report'))
