"""Durable human-approved local screening. The model has no workflow tools."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import func, select, update
from app.api.deps import accessible_application
from app.core.screening_config import get_voice_settings
from app.core.status import Status, assert_transition
from app.database.models import Application, Approval, Evaluation, Requisition, ResumeFile, Screening
from app.database.screening_models import ScreeningAnswer, ScreeningCommand, ScreeningQuestion, ScreeningReport, ScreeningSession
from app.services.audit_service import record_event

PROTECTED_TOPIC = re.compile(r'\b(your age|how old|date of birth|marital|pregnan\w*|religion|ethnicity|your race|racial background|caste|sexual orientation|disability|medical condition|national origin)\b', re.I)
TERMINAL = {'completed', 'approved_for_interview', 'closed', 'human_requested', 'reschedule_requested', 'stopped', 'consent_declined', 'expired'}
CONSENT_TEXT_VERSION = 'local-screening-v1'


def _now():
    return datetime.now(timezone.utc)


def _aware(value):
    return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value


def _uuid(value):
    try:
        return value if isinstance(value, UUID) else UUID(str(value))
    except (ValueError, TypeError):
        raise HTTPException(422, 'Invalid screening identifier.') from None


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _redact(value, limit=1200):
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[email omitted]', str(value or ''))
    return re.sub(r'(?<!\w)\+?\d[\d ()-]{8,}\d(?!\w)', '[phone omitted]', text)[:limit]


class ScreeningService:
    def __init__(self, db, user=None, runtime=None, candidate_session_id=None):
        self.db, self.user, self.runtime = db, user, runtime
        self.candidate_session_id = _uuid(candidate_session_id) if candidate_session_id else None
        self.max_followups = get_voice_settings().screening_max_followups

    def _recruiter(self):
        if not self.user or self.user.role != 'recruiter' or not self.user.is_active:
            raise HTTPException(403, 'An authorized recruiter is required.')

    def _application(self, application_id, mutation=False):
        if mutation:
            self._recruiter()
        if not self.user:
            raise HTTPException(403, 'Recruiter authentication is required.')
        app = accessible_application(self.db, _uuid(application_id), self.user)
        if mutation and app.requisition.created_by != self.user.user_id:
            raise HTTPException(404, 'Application not found.')
        return app

    def _session(self, session_id, lock=False):
        query = select(ScreeningSession).where(ScreeningSession.screening_session_id == _uuid(session_id)).execution_options(populate_existing=True)
        session = self.db.scalar(query.with_for_update() if lock else query)
        if session is None:
            raise HTTPException(404, 'Screening session not found.')
        if self.candidate_session_id:
            if session.screening_session_id != self.candidate_session_id:
                raise HTTPException(403, 'This token is scoped to another screening.')
        else:
            self._application(session.application_id)
        retained_since = _aware(session.ended_at or session.started_at or session.created_at)
        if retained_since < _now() - timedelta(days=get_voice_settings().screening_retention_days):
            raise HTTPException(410, 'The disclosed screening retention period has expired. Contact HR for another arrangement.')
        return session

    def _actor(self, session):
        if not self.candidate_session_id:
            self._application(session.application_id, mutation=True)

    def _cas(self, session):
        """Atomic optimistic claim works on SQLite as well as PostgreSQL."""
        version = session.state_version
        result = self.db.execute(update(ScreeningSession).where(ScreeningSession.screening_session_id == session.screening_session_id,
            ScreeningSession.state_version == version).values(state_version=version + 1).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            self.db.rollback()
            raise HTTPException(409, 'Screening state changed. Reload and retry the same request.')
        session.state_version = version + 1

    def _audit(self, session, action, details=None):
        record_event(self.db, action=action, actor_id=getattr(self.user, 'user_id', None), application_id=session.application_id,
            entity_type='screening', entity_id=str(session.screening_session_id), details=details or {})

    def _checkpoint(self, session):
        try:
            from app.workflows.screening_graph import sync_screening_checkpoint
            sync_screening_checkpoint(session.screening_session_id, session.application_id, session.status, session.state_version)
        except Exception:
            # A committed answer remains authoritative and reconstructs the gate on the next read.
            logging.getLogger(__name__).warning('Screening checkpoint reconciliation deferred.')

    def _commit(self, session):
        self.db.commit()
        self._checkpoint(session)

    def _command(self, session, request_id, operation, payload):
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 128:
            raise HTTPException(422, 'A request ID of 8–128 characters is required.')
        digest = _hash(payload)
        previous = self.db.scalar(select(ScreeningCommand).where(ScreeningCommand.screening_session_id == session.screening_session_id,
            ScreeningCommand.request_id == request_id))
        if previous:
            if previous.operation != operation or previous.payload_sha256 != digest:
                raise HTTPException(409, 'This request ID was used for a different operation.')
            return previous, True
        command = ScreeningCommand(screening_session_id=session.screening_session_id, request_id=request_id,
            operation=operation, payload_sha256=digest, result_metadata={})
        self.db.add(command)
        return command, False

    def _questions(self, session):
        return list(self.db.scalars(select(ScreeningQuestion).where(ScreeningQuestion.screening_session_id == session.screening_session_id).order_by(ScreeningQuestion.question_order)))

    def _current(self, session):
        return next((q for q in self._questions(session) if q.answered_at is None and q.unresolved_reason is None), None)

    def _answer(self, question_id):
        return self.db.scalar(select(ScreeningAnswer).where(ScreeningAnswer.question_id == question_id))

    @staticmethod
    def _question_dict(q):
        return {'question_id': str(q.question_id), 'text': q.text, 'topic': q.topic, 'order': q.question_order,
                'is_follow_up': q.is_follow_up, 'required': q.required, 'source_reference': q.source_reference,
                'answered': q.answered_at is not None, 'unresolved_reason': q.unresolved_reason}

    @staticmethod
    def _answer_dict(a):
        return {'answer_id': str(a.answer_id), 'question_id': str(a.question_id), 'transcript_text': a.transcript_text,
                'corrected_text': a.corrected_text, 'response_status': a.response_status, 'segments': a.segments,
                'created_at': a.created_at.isoformat(), 'confirmed_at': a.confirmed_at.isoformat() if a.confirmed_at else None,
                'model_version': a.model_version}

    def _view(self, session):
        questions = self._questions(session)
        current = self._current(session)
        pending = self._answer(current.question_id) if current else None
        if pending and pending.response_status != 'pending_confirmation':
            pending = None
        return {'screening_session_id': str(session.screening_session_id), 'application_id': str(session.application_id),
                'candidate_id': str(session.candidate_id), 'requisition_id': str(session.requisition_id),
                'job_title': session.context_snapshot.get('job_title', ''), 'status': session.status, 'language': session.language,
                'consent_status': session.consent_status, 'retain_transcript': session.retain_transcript, 'audio_stored': False,
                'consent_notice_version': CONSENT_TEXT_VERSION, 'created_at': session.created_at.isoformat(),
                'current_question': self._question_dict(current) if current else None,
                'pending_answer': self._answer_dict(pending) if pending else None,
                'progress': {'answered': sum(q.answered_at is not None or q.unresolved_reason is not None for q in questions), 'total': len(questions)},
                'can_finish': session.status == 'active' and current is None, 'issues': session.issues,
                'model_versions': session.model_versions, 'state_version': session.state_version,
                'report_available': self.db.scalar(select(ScreeningReport.report_id).where(ScreeningReport.screening_session_id == session.screening_session_id)) is not None}

    def _recover(self, session):
        if session.status in {'transcribing', 'preparing_followup'} and (not session.lease_expires_at or _aware(session.lease_expires_at) <= _now()):
            self._cas(session)
            for answer in self.db.scalars(select(ScreeningAnswer).where(ScreeningAnswer.screening_session_id == session.screening_session_id, ScreeningAnswer.response_status == 'processing')):
                answer.response_status = 'failed'
            session.status, session.lease_token, session.lease_expires_at = 'active', None, None
            session.issues = [*session.issues, 'An interrupted model operation was recovered. Retry the pending question.'][-30:]
            self._audit(session, 'screening.recovered')
            self._commit(session)

    def get(self, session_id):
        session = self._session(session_id)
        self._recover(session)
        self._checkpoint(session)
        return self._view(session)

    def list_owned(self):
        self._recruiter()
        cutoff = _now() - timedelta(days=get_voice_settings().screening_retention_days)
        return [self._view(s) for s in self.db.scalars(select(ScreeningSession).join(Requisition, Requisition.requisition_id == ScreeningSession.requisition_id).where(Requisition.created_by == self.user.user_id,
            func.coalesce(ScreeningSession.ended_at, ScreeningSession.started_at, ScreeningSession.created_at) >= cutoff).order_by(ScreeningSession.created_at.desc()).limit(100))]

    def _eligible(self, application_id):
        app = self._application(application_id, mutation=True)
        if app.current_status not in {Status.APPROVED_FOR_SCREENING, Status.SCREENING_PENDING}:
            raise HTTPException(409, 'HR must approve this application for screening first.')
        evaluation = self.db.scalar(select(Evaluation).where(Evaluation.application_id == app.application_id, Evaluation.is_active.is_(True)))
        if not evaluation or evaluation.rubric_version != app.requisition.rubric_version:
            raise HTTPException(409, 'The current resume evaluation and job rubric require HR review.')
        approval = self.db.scalar(select(Approval).where(Approval.application_id == app.application_id,
            Approval.evaluation_id == evaluation.evaluation_id, Approval.approval_type == 'shortlist', Approval.decision == 'approve'))
        if approval is None:
            raise HTTPException(409, 'An explicit HR shortlist approval for the active evaluation is required.')
        return app, evaluation

    def _snapshot(self, app, evaluation):
        req = app.requisition
        evidence = [{'source_reference': f'agent1:evidence:{index}', 'quote': _redact(item.get('source_text', ''), 600),
                     'criterion': str(item.get('criterion', ''))[:100]}
                    for index, item in enumerate((evaluation.evidence or [])[:12]) if isinstance(item, dict)]
        projects = []
        if evaluation.resume_file_id:
            resume = self.db.get(ResumeFile, evaluation.resume_file_id)
            if resume:
                from app.services.redaction import redact_resume_text
                from app.agents.resume_agent import prepare_resume_text
                prepared, _ = prepare_resume_text(redact_resume_text(resume.extracted_text or '', known_names=[app.candidate.full_name]).text)
                terms = [str(skill).casefold() for skill in req.required_skills or []]
                for index, line in enumerate(prepared.splitlines(), 1):
                    if (re.search(r'\b(project|built|implemented|developed)\b', line, re.I)
                            and any(term in line.casefold() for term in terms) and not PROTECTED_TOPIC.search(line)):
                        projects.append({'quote': _redact(line.strip(), 400), 'source_reference': f'resume:{resume.file_id}:line:{index}'})
                        if len(projects) == 2:
                            break
        return {'job_title': _redact(req.job_title, 200), 'job_description': _redact(req.job_description, 6000),
                'required_skills': [_redact(s, 100) for s in (req.required_skills or [])[:20]],
                'work_arrangement': _redact(req.work_arrangement, 500), 'evidence': evidence,
                'approved_screening_questions': [_redact(q, 1200) for q in (req.screening_questions or [])[:7]],
                'missing_or_unclear': [_redact(q, 500) for q in (evaluation.missing_or_unclear or [])[:12]],
                'criterion_scores': evaluation.criterion_scores, 'criterion_explanations': evaluation.criterion_explanations,
                'approved_criteria': (evaluation.rubric_snapshot or {}).get('criteria', []), 'resume_projects': projects,
                'evaluation_id': str(evaluation.evaluation_id),
                'rubric_version': evaluation.rubric_version, 'agent1_model_version': evaluation.model_version}

    def plan(self, application_id):
        app, evaluation = self._eligible(application_id)
        context = self._snapshot(app, evaluation)
        questions = [{'text': text, 'topic': 'job_requirement', 'source_reference': f'requisition:screening_question:{i}', 'required': True}
                     for i, text in enumerate(context['approved_screening_questions']) if text and not PROTECTED_TOPIC.search(text)]
        for item in context['evidence']:
            if item['quote']:
                questions.append({'text': f'Your resume states: "{item["quote"][:180]}". Describe your own contribution and a concrete job-related example.',
                                  'topic': 'resume_claim', 'source_reference': item['source_reference'], 'required': True})
                break
        if context['resume_projects'] and len(questions) < 8:
            project = context['resume_projects'][0]
            questions.append({'text': f'Your resume describes: "{project["quote"][:200]}". How did you implement it, and what was your own contribution?',
                              'topic': 'resume_project', 'source_reference': project['source_reference'], 'required': True})
        for index, note in enumerate(context['missing_or_unclear'][:1]):
            if note and not PROTECTED_TOPIC.search(note) and len(questions) < 8:
                questions.append({'text': 'Please clarify this job-related information that was not established by the resume: ' + note[:350],
                                  'topic': 'missing_information', 'source_reference': f'agent1:missing:{index}', 'required': True})
        if context['criterion_scores'] and len(questions) < 8:
            weakest = min(context['criterion_scores'], key=context['criterion_scores'].get)
            criterion = next((c for c in context['approved_criteria'] if c.get('key') == weakest), {})
            label = _redact(criterion.get('label') or weakest.replace('_', ' '), 120)
            if not PROTECTED_TOPIC.search(label):
                questions.append({'text': f'For this role, give a concrete example relevant to {label}. Describe only your own work; you may mark missing details for human follow-up.',
                                  'topic': 'criterion_' + weakest[:60], 'source_reference': 'agent1:criterion:' + weakest[:80], 'required': True})
        if not questions:
            scope = ', '.join(context['required_skills'][:4]) or 'the approved job responsibilities'
            questions.append({'text': f'Please describe your experience relevant to {scope}.', 'topic': 'job_requirement', 'source_reference': 'requisition:required_skills', 'required': True})
        questions.extend([{'text': 'When would you be available to start this role?', 'topic': 'availability', 'source_reference': 'standard:availability', 'required': True},
                          {'text': 'What is your current notice period, if any?', 'topic': 'notice_period', 'source_reference': 'standard:notice_period', 'required': True}])
        return {'application_id': str(app.application_id), 'questions': questions, 'context': context, 'requires_hr_approval': True,
                'default_language': get_voice_settings().screening_default_language,
                'maximum_followups': self.max_followups, 'followup_templates': [
                    'Could you clarify your answer to: {approved question}',
                    'Please give a concrete example relevant to: {approved question}',
                    'If JWT/authentication is mentioned within an approved API topic: What was your involvement, if any, with token expiration and authorization?',
                    'If FastAPI is mentioned within an approved API topic: What did you personally do, if anything, for request validation and error handling?',
                    'If PostgreSQL is mentioned within an approved database topic: What was your involvement, if any, with transactions and database access?']}

    def start(self, application_id, approved_questions, language='en', request_id=None):
        app, evaluation = self._eligible(application_id)
        questions = [q.model_dump() if hasattr(q, 'model_dump') else dict(q) for q in approved_questions]
        if language not in {'en', 'ur'} or not 1 <= len(questions) <= 12:
            raise HTTPException(422, 'Approve one to twelve questions and select English or Urdu.')
        if not {'availability', 'notice_period'}.issubset({q.get('topic') for q in questions if q.get('required', True)}):
            raise HTTPException(409, 'The approved plan must include mandatory availability and notice-period topics.')
        seen = set()
        for question in questions:
            text = str(question.get('text', '')).strip()
            if not 5 <= len(text) <= 1200 or PROTECTED_TOPIC.search(text) or text.casefold() in seen:
                raise HTTPException(422, 'Questions must be bounded, unique and job related without protected personal topics.')
            seen.add(text.casefold())
            question['text'] = text
        existing = self.db.scalar(select(ScreeningSession).where(ScreeningSession.application_id == app.application_id))
        payload = {'questions': questions, 'language': language}
        if existing:
            command = self.db.scalar(select(ScreeningCommand).where(ScreeningCommand.screening_session_id == existing.screening_session_id, ScreeningCommand.operation == 'start'))
            if existing.evaluation_id != evaluation.evaluation_id or not command or command.payload_sha256 != _hash(payload):
                raise HTTPException(409, 'A session already exists with a different approved plan or evaluation.')
            return self._view(existing)
        legacy = Screening(application_id=app.application_id, call_status='pending', consent_status='requested', answers=[])
        self.db.add(legacy)
        self.db.flush()
        session = ScreeningSession(screening_id=legacy.screening_id, application_id=app.application_id, candidate_id=app.candidate_id,
            requisition_id=app.requisition_id, evaluation_id=evaluation.evaluation_id, created_by=self.user.user_id, language=language,
            status='consent_requested', consent_status='requested', retain_transcript=False, context_snapshot=self._snapshot(app, evaluation),
            model_versions={'agent1': evaluation.model_version, 'report': 'extractive-v1', 'workflow': 'phase3-v1'}, issues=[], state_version=0)
        self.db.add(session)
        self.db.flush()
        for i, q in enumerate(questions):
            self.db.add(ScreeningQuestion(screening_session_id=session.screening_session_id, text=q['text'], topic=q.get('topic', 'job_requirement'),
                source_reference=q.get('source_reference', 'hr_approved'), required=q.get('required', True), question_order=(i + 1) * 100, is_follow_up=False))
        self._command(session, request_id, 'start', payload)
        if app.current_status != Status.SCREENING_PENDING:
            assert_transition(app.current_status, Status.SCREENING_PENDING)
            app.current_status = Status.SCREENING_PENDING
        self._audit(session, 'screening.started', {'question_count': len(questions), 'language': language})
        self._commit(session)
        return self._view(session)

    def consent(self, session_id, status, retain_transcript=False, request_id=None, language=None):
        session = self._session(session_id, lock=True)
        self._actor(session)
        language = language or session.language
        if status not in {'accepted', 'declined', 'human_requested', 'reschedule_requested', 'stopped'} or language not in {'en', 'ur'}:
            raise HTTPException(422, 'Invalid consent or language selection.')
        command, duplicate = self._command(session, request_id, 'consent', {'status': status, 'retain_transcript': retain_transcript, 'language': language})
        if duplicate:
            return self._view(session)
        if session.status != 'consent_requested':
            raise HTTPException(409, 'Consent has already been recorded. Contact HR to change the arrangement.')
        if status == 'accepted' and not retain_transcript:
            raise HTTPException(409, 'Automated screening requires transcript-retention consent; human assistance is available.')
        self._cas(session)
        session.language, session.consent_status = language, status
        session.retain_transcript = status == 'accepted' and retain_transcript
        session.consented_at = _now()
        session.status = 'active' if status == 'accepted' else ('consent_declined' if status == 'declined' else status)
        session.started_at = _now() if status == 'accepted' else None
        session.ended_at = None if status == 'accepted' else _now()
        legacy = self.db.get(Screening, session.screening_id)
        legacy.call_status, legacy.consent_status = session.status, status
        command.result_metadata = {'consent_notice_version': CONSENT_TEXT_VERSION, 'retain_transcript': session.retain_transcript, 'audio_stored': False}
        self._audit(session, 'screening.consent_recorded', command.result_metadata | {'status': status})
        self._commit(session)
        return self._view(session)

    def _require_active(self, session):
        if session.consent_status != 'accepted' or not session.retain_transcript:
            raise HTTPException(403, 'Accepted audio-processing and transcript-retention consent is required.')
        self._recover(session)
        if session.status != 'active':
            raise HTTPException(409, 'Screening is not ready for an answer.')
        app = self.db.get(Application, session.application_id)
        current = self.db.scalar(select(Evaluation.evaluation_id).where(Evaluation.application_id == session.application_id, Evaluation.is_active.is_(True)))
        if (app.current_status != Status.SCREENING_PENDING or current != session.evaluation_id
                or app.requisition.rubric_version != session.context_snapshot.get('rubric_version')):
            raise HTTPException(409, 'Screening authorization changed. HR must review the application.')

    def _voice(self):
        if self.runtime is None:
            from app.integrations.local_voice import get_voice_runtime
            self.runtime = get_voice_runtime()
        return self.runtime

    def submit_audio(self, session_id, question_id, audio, request_id):
        session = self._session(session_id, lock=True)
        self._actor(session)
        if session.consent_status != 'accepted' or not session.retain_transcript:
            raise HTTPException(403, 'Accepted consent is required before processing audio.')
        digest = hashlib.sha256(audio).hexdigest()
        command, duplicate = self._command(session, request_id, 'audio', {'question_id': str(question_id), 'audio_sha256': digest})
        if duplicate and command.result_metadata.get('phase') == 'complete':
            return self._view(session)
        self._require_active(session)
        question = self._current(session)
        if not question or question.question_id != _uuid(question_id):
            raise HTTPException(409, 'Only the current unanswered question accepts audio.')
        if not audio or len(audio) > get_voice_settings().voice_max_audio_bytes:
            raise HTTPException(422, 'Record a nonempty answer within the upload limit.')
        answer = self._answer(question.question_id)
        repeated = self.db.scalar(select(ScreeningAnswer).where(ScreeningAnswer.screening_session_id == session.screening_session_id, ScreeningAnswer.audio_sha256 == digest))
        if repeated and repeated.question_id != question.question_id:
            raise HTTPException(409, 'This recording was already used for another question.')
        if answer and answer.response_status not in {'failed', 'unclear'}:
            raise HTTPException(409, 'Confirm the pending transcript before continuing.')
        self._cas(session)
        if answer is None:
            answer = ScreeningAnswer(screening_session_id=session.screening_session_id, question_id=question.question_id,
                audio_sha256=digest, transcript_text='', segments=[], response_status='processing')
            self.db.add(answer)
        else:
            answer.audio_sha256, answer.response_status = digest, 'processing'
        self.db.flush()
        answer_id = answer.answer_id
        lease = str(uuid4())
        session.status, session.lease_token, session.lease_expires_at = 'transcribing', lease, _now() + timedelta(minutes=3)
        command.result_metadata = {'phase': 'processing', 'answer_id': str(answer_id)}
        language = session.language
        self._commit(session)
        try:
            recognized = self._voice().transcribe(audio, language=language)
            text = str(recognized.get('text') or '').strip()[:12000]
            segments = [{'start': max(0.0, float(s.get('start', 0))), 'end': max(0.0, float(s.get('end', 0))),
                         'text': str(s.get('text', ''))[:1500]} for s in recognized.get('segments', [])[:200] if isinstance(s, dict)]
            session = self._session(session_id, lock=True)
            if session.lease_token != lease or session.status != 'transcribing':
                raise HTTPException(409, 'The session changed during transcription; the obsolete result was discarded.')
            self._cas(session)
            answer = self.db.get(ScreeningAnswer, answer_id)
            answer.transcript_text, answer.segments = text, segments
            answer.response_status = 'pending_confirmation' if text else 'unclear'
            answer.model_version = str(recognized.get('model_version', 'unreported'))[:120]
            session.model_versions = session.model_versions | {'stt': answer.model_version}
            session.status, session.lease_token, session.lease_expires_at = 'active', None, None
            command = self.db.scalar(select(ScreeningCommand).where(ScreeningCommand.screening_session_id == session.screening_session_id, ScreeningCommand.request_id == request_id))
            command.result_metadata = {'phase': 'complete' if text else 'retry', 'answer_id': str(answer_id)}
            self._audit(session, 'screening.transcription_ready', {'answer_id': str(answer_id), 'unclear': not bool(text)})
            self._commit(session)
            return self._view(session)
        except HTTPException:
            self.db.rollback()
            raise
        except Exception as exc:
            self.db.rollback()
            session = self._session(session_id, lock=True)
            if session.lease_token == lease:
                self._cas(session)
                self.db.get(ScreeningAnswer, answer_id).response_status = 'failed'
                session.status, session.lease_token, session.lease_expires_at = 'active', None, None
                self._audit(session, 'screening.transcription_failed')
                self._commit(session)
            from app.integrations.local_voice import LocalVoiceError
            if isinstance(exc, LocalVoiceError) and exc.code in {'empty_audio', 'invalid_audio', 'audio_too_long', 'audio_too_large', 'unclear_audio'}:
                raise HTTPException(413 if exc.code == 'audio_too_large' else 422, exc.message) from None
            raise HTTPException(503, 'Local speech recognition failed. Retry this recording or request human assistance; confirmed progress is saved.') from None

    def confirm_answer(self, session_id, answer_id, corrected_text=None, request_id=None):
        session = self._session(session_id, lock=True)
        self._actor(session)
        command, duplicate = self._command(session, request_id, 'confirm', {'answer_id': str(answer_id), 'corrected_text': corrected_text})
        if duplicate:
            self._recover(session)
            return self._view(session)
        self._require_active(session)
        question = self._current(session)
        answer = self.db.get(ScreeningAnswer, _uuid(answer_id))
        if not question or not answer or answer.question_id != question.question_id or answer.screening_session_id != session.screening_session_id or answer.response_status != 'pending_confirmation':
            raise HTTPException(409, 'Only the pending transcript for this question can be confirmed.')
        effective = corrected_text.strip() if isinstance(corrected_text, str) else answer.transcript_text.strip()
        if not effective or len(effective) > 12000:
            raise HTTPException(422, 'Confirm a nonempty transcript of at most 12,000 characters.')
        self._cas(session)
        answer.corrected_text = effective if corrected_text is not None else None
        answer.response_status, answer.confirmed_at, question.answered_at = 'confirmed', _now(), _now()
        adaptive = not question.is_follow_up and question.topic not in {'availability', 'notice_period'} and sum(q.is_follow_up for q in self._questions(session)) < self.max_followups
        question_id, text, topic, order = question.question_id, question.text, question.topic, question.question_order
        context = {'approved_scope': session.context_snapshot.get('job_title', ''), 'topic': topic,
                   'answered_topics': [q.topic for q in self._questions(session) if q.answered_at]}
        lease = str(uuid4())
        if adaptive:
            session.status, session.lease_token, session.lease_expires_at = 'preparing_followup', lease, _now() + timedelta(minutes=3)
        command.result_metadata = {'confirmed': True, 'answer_id': str(answer.answer_id)}
        self._audit(session, 'screening.answer_confirmed', {'answer_id': str(answer.answer_id), 'corrected': corrected_text is not None})
        self._commit(session)
        if not adaptive:
            return self._view(session)
        decision, failure = {'action': 'none'}, False
        try:
            decision = self._voice().choose_followup(context=context, question=text, answer=effective)
            if not isinstance(decision, dict) or decision.get('action') not in {'none', 'clarify', 'example'}:
                raise ValueError('Invalid action')
        except Exception:
            failure = True
        session = self._session(session_id, lock=True)
        if session.lease_token != lease or session.status != 'preparing_followup':
            return self._view(session)
        self._cas(session)
        if failure:
            session.issues = [*session.issues, 'Ollama was unavailable; the approved fixed-question baseline continued. HR should review unclear answers.'][-30:]
        elif decision['action'] in {'clarify', 'example'}:
            prefix = 'Could you clarify your answer to: ' if decision['action'] == 'clarify' else 'Please give a concrete example relevant to: '
            wording = prefix + text
            scope = (text + ' ' + session.context_snapshot.get('job_description', '')).casefold()
            if re.search(r'\b(jwt|authentication)\b', effective, re.I) and any(t in scope for t in ('api', 'backend', 'authenticat')):
                wording = 'You mentioned authentication. What was your involvement, if any, with token expiration and authorization?'
            elif re.search(r'\bfastapi\b', effective, re.I) and any(t in scope for t in ('api', 'backend', 'python')):
                wording = 'You mentioned FastAPI. What did you personally do, if anything, for request validation and error handling?'
            elif re.search(r'\bpostgresql\b', effective, re.I) and any(t in scope for t in ('database', 'postgresql', 'sql')):
                wording = 'You mentioned PostgreSQL. What was your involvement, if any, with transactions and database access?'
            self.db.add(ScreeningQuestion(screening_session_id=session.screening_session_id, parent_question_id=question_id,
                text=wording, topic=topic, source_reference=f'approved-question:{question_id};answer:{answer_id}',
                question_order=order + 1, required=True, is_follow_up=True))
            self._audit(session, 'screening.followup_selected', {'action': decision['action'], 'parent_question_id': str(question_id)})
        if not failure and decision.get('model_version'):
            session.model_versions = session.model_versions | {'llm': str(decision['model_version'])[:120]}
        session.status, session.lease_token, session.lease_expires_at = 'active', None, None
        self._commit(session)
        return self._view(session)

    def questions(self, session_id):
        return [self._question_dict(q) for q in self._questions(self._session(session_id))]

    def transcript(self, session_id):
        session = self._session(session_id)
        if not session.retain_transcript:
            return []
        return [{'question': self._question_dict(q), 'answer': self._answer_dict(answer)}
                for q in self._questions(session) if (answer := self._answer(q.question_id)) is not None]

    def question_voice(self, session_id, question_id):
        session = self._session(session_id)
        self._require_active(session)
        current = self._current(session)
        if not current or current.question_id != _uuid(question_id):
            raise HTTPException(409, 'Only the current approved question can be spoken.')
        text, language = current.text, session.language
        self.db.commit()
        try:
            runtime = self._voice()
            result = runtime.synthesize(text, language=language)
            if hasattr(runtime, 'status'):
                version = runtime.status().get('versions', {}).get('piper-tts')
                if version and version != 'not-installed':
                    session.model_versions = session.model_versions | {'tts': 'piper-tts/' + version + ':en_US-lessac-medium'}
                    self.db.commit()
            return result
        except Exception:
            raise HTTPException(503, 'Local question speech is unavailable. The English Piper voice cannot speak Urdu; retry English playback or request human assistance.') from None

    def finish(self, session_id, request_id):
        session = self._session(session_id, lock=True)
        self._actor(session)
        command, duplicate = self._command(session, request_id, 'finish', {})
        if duplicate:
            return self._view(session)
        self._require_active(session)
        questions = self._questions(session)
        if any(q.required and not q.answered_at and not q.unresolved_reason for q in questions):
            raise HTTPException(409, 'Address every required topic or mark it unresolved for HR before finishing.')
        self._cas(session)
        session.ended_at, session.status = _now(), 'completed'
        rows, evidence, unanswered = [], [], []
        for q in questions:
            answer = self._answer(q.question_id)
            confirmed = answer is not None and answer.response_status == 'confirmed'
            statement = (answer.corrected_text if answer.corrected_text is not None else answer.transcript_text) if confirmed else None
            row = {'question_id': str(q.question_id), 'question': q.text, 'topic': q.topic, 'source_reference': q.source_reference,
                   'is_follow_up': q.is_follow_up, 'candidate_statement': statement, 'transcript_reference': str(answer.answer_id) if confirmed else None,
                   'unresolved_reason': q.unresolved_reason or (None if confirmed else 'No confirmed answer.')}
            rows.append(row)
            if confirmed:
                evidence.append({'answer_id': str(answer.answer_id), 'question_id': str(q.question_id), 'quote': statement,
                                 'topic': q.topic, 'claim_status': 'candidate_statement_not_independently_verified'})
            else:
                unanswered.append({'question_id': str(q.question_id), 'reason': row['unresolved_reason']})
        report = {'candidate_id': str(session.candidate_id), 'application_id': str(session.application_id),
            'requisition_id': str(session.requisition_id), 'screening_session_id': str(session.screening_session_id),
            'screening_date': session.ended_at.isoformat(), 'duration_seconds': max(0, (session.ended_at - _aware(session.started_at)).total_seconds()) if session.started_at else 0,
            'consent_status': session.consent_status, 'consent_notice_version': CONSENT_TEXT_VERSION,
            'conversation_language': session.language, 'audio_stored': False, 'duration_basis': 'wall clock including pauses and local processing',
            'questions_and_answers': rows, 'evidence': evidence, 'relevant_experience_statements': [e for e in evidence if e['topic'] not in {'availability', 'notice_period'}],
            'job_requirement_responses': [r for r in rows if r['topic'] == 'job_requirement'],
            'availability': [e for e in evidence if e['topic'] == 'availability'], 'notice_period': [e for e in evidence if e['topic'] == 'notice_period'],
            'unanswered_questions': unanswered, 'resume_claims_needing_clarification': [
                {'note': note, 'source_reference': f'evaluation:{session.evaluation_id}:missing:{i}', 'status': 'agent1_uncertainty_for_hr_not_a_verified_candidate_fact'}
                for i, note in enumerate(session.context_snapshot.get('missing_or_unclear', []))],
            'candidate_questions_for_hr': [c.result_metadata for c in self.db.scalars(select(ScreeningCommand).where(ScreeningCommand.screening_session_id == session.screening_session_id, ScreeningCommand.operation == 'candidate_question'))],
            'summary': f'{len(evidence)} candidate responses confirmed; {len(unanswered)} questions need HR follow-up. Statements are not independently verified.',
            'suggested_human_follow_up_actions': ['Review each quoted response against the approved job requirements.', 'Clarify unanswered topics and the missing information recorded by Agent 1.'],
            'issues': session.issues, 'model_versions': session.model_versions, 'human_review_required': True}
        self.db.add(ScreeningReport(screening_session_id=session.screening_session_id, structured_summary=report))
        app = self.db.get(Application, session.application_id)
        assert_transition(app.current_status, Status.SCREENING_COMPLETED_HR_REVIEW)
        app.current_status = Status.SCREENING_COMPLETED_HR_REVIEW
        legacy = self.db.get(Screening, session.screening_id)
        legacy.call_status, legacy.summary = 'completed', report['summary']
        command.result_metadata = {'completed': True}
        self._audit(session, 'screening.completed', {'confirmed_answers': len(evidence), 'unanswered': len(unanswered)})
        self._commit(session)
        return self._view(session)

    def report(self, session_id):
        session = self._session(session_id)
        report = self.db.scalar(select(ScreeningReport).where(ScreeningReport.screening_session_id == session.screening_session_id))
        if report is None:
            raise HTTPException(404, 'A completed screening report is not available yet.')
        return report.structured_summary | {'report_id': str(report.report_id), 'created_at': report.created_at.isoformat()}

    def hr_decision(self, session_id, decision, reason, reviewed_report, request_id):
        self._recruiter()
        session = self._session(session_id, lock=True)
        app = self._application(session.application_id, mutation=True)
        if decision not in {'approve', 'hold', 'close', 'clarification'} or not isinstance(reason, str) or not 3 <= len(reason.strip()) <= 2000:
            raise HTTPException(422, 'Choose a valid HR decision and provide a reason of 3–2000 characters.')
        command, duplicate = self._command(session, request_id, 'hr_decision', {'decision': decision, 'reason': reason, 'reviewed_report': reviewed_report})
        if duplicate:
            return self._view(session)
        if not reviewed_report or session.status not in {'completed', 'held'} or app.current_status != Status.SCREENING_COMPLETED_HR_REVIEW:
            raise HTTPException(409, 'HR must review the completed report before deciding.')
        report = self.db.scalar(select(ScreeningReport).where(ScreeningReport.screening_session_id == session.screening_session_id))
        if report is None:
            raise HTTPException(409, 'A completed report is required.')
        self._cas(session)
        target, status = {'approve': (Status.APPROVED_FOR_INTERVIEW, 'approved_for_interview'), 'close': (Status.CLOSED, 'closed'),
                          'clarification': (Status.SCREENING_PENDING, 'active'), 'hold': (Status.SCREENING_COMPLETED_HR_REVIEW, 'held')}[decision]
        if decision == 'clarification':
            if PROTECTED_TOPIC.search(reason) or len(reason) > 1000:
                raise HTTPException(422, 'HR clarification must remain job related and at most 1,000 characters.')
            questions = self._questions(session)
            self.db.add(ScreeningQuestion(screening_session_id=session.screening_session_id, text='Please clarify this job-related point requested by HR: ' + reason.strip(),
                topic='hr_clarification', source_reference=f'hr-reviewer:{self.user.user_id}', question_order=max(q.question_order for q in questions) + 100, required=True, is_follow_up=False))
            session.ended_at = None
        if app.current_status != target:
            assert_transition(app.current_status, target)
            app.current_status = target
        session.status = status
        approval = Approval(application_id=session.application_id, evaluation_id=None, reviewed_by=self.user.user_id,
            approval_type='screening', decision='clarify' if decision == 'clarification' else decision, reason=reason.strip())
        self.db.add(approval)
        self.db.flush()
        command.result_metadata = {'approval_id': str(approval.approval_id), 'reviewer_id': str(self.user.user_id), 'decision': decision,
            'reason': reason.strip(), 'reviewed_at': _now().isoformat(), 'reviewed_report_id': str(report.report_id)}
        if decision == 'clarification':
            command.result_metadata = command.result_metadata | {'reviewed_report': report.structured_summary}
            self.db.delete(report)
        self._audit(session, 'screening.hr_decision', {'approval_id': str(approval.approval_id), 'decision': decision})
        self._commit(session)
        return self._view(session)

    def control(self, session_id, action, request_id, reason=''):
        session = self._session(session_id, lock=True)
        self._actor(session)
        command, duplicate = self._command(session, request_id, 'control', {'action': action, 'reason': reason})
        if duplicate:
            return self._view(session)
        if action not in {'pause', 'resume', 'stop', 'human', 'reschedule', 'clarify', 'skip'}:
            raise HTTPException(422, 'Unknown screening action.')
        if session.consent_status != 'accepted':
            raise HTTPException(403, 'Use the consent choices before beginning the interview.')
        if session.status in TERMINAL:
            raise HTTPException(409, 'This screening has ended.')
        if action == 'resume' and session.status != 'paused':
            raise HTTPException(409, 'Only a paused screening can resume.')
        if action in {'pause', 'skip', 'clarify'}:
            self._require_active(session)
        self._cas(session)
        if action in {'stop', 'human', 'reschedule'}:
            session.status = {'stop': 'stopped', 'human': 'human_requested', 'reschedule': 'reschedule_requested'}[action]
            session.ended_at, session.lease_token, session.lease_expires_at = _now(), None, None
        elif action in {'pause', 'resume'}:
            session.status = 'paused' if action == 'pause' else 'active'
        elif action == 'skip':
            question = self._current(session)
            if question is None:
                raise HTTPException(409, 'There is no pending question.')
            question.unresolved_reason = 'Candidate requested human follow-up instead of answering.'
        else:
            session.issues = [*session.issues, 'Candidate requested clarification of an approved question.'][-30:]
        command.result_metadata = {'action': action}
        self._audit(session, 'screening.control', command.result_metadata)
        self._commit(session)
        result = self._view(session)
        if action == 'clarify' and result['current_question']:
            result['clarification_text'] = 'Please explain in your own words. You can request a human. The approved question is: ' + result['current_question']['text']
        return result

    def candidate_question(self, session_id, question, request_id):
        session = self._session(session_id, lock=True)
        self._actor(session)
        if not isinstance(question, str) or not 3 <= len(question.strip()) <= 1000:
            raise HTTPException(422, 'Ask a job-related question of 3–1000 characters.')
        command, duplicate = self._command(session, request_id, 'candidate_question', {'question': question})
        if duplicate:
            return command.result_metadata
        self._require_active(session)
        self._cas(session)
        text, context = question.casefold(), session.context_snapshot
        answer, source = 'HR can clarify this; the approved job information does not give a confirmed answer.', 'human_follow_up_required'
        if any(term in text for term in ('salary', 'benefit', 'compensation', 'policy', 'interview date')):
            pass
        elif any(term in text for term in ('remote', 'hybrid', 'arrangement', 'onsite', 'office')) and context.get('work_arrangement'):
            answer, source = 'The approved job information states: ' + context['work_arrangement'], 'requisition:work_arrangement'
        elif any(term in text for term in ('technolog', 'stack', 'skills', 'tools')) and context.get('required_skills'):
            answer, source = 'The approved required skills are: ' + ', '.join(context['required_skills']), 'requisition:required_skills'
        elif any(term in text for term in ('next', 'after screening', 'process')):
            answer, source = 'HR reviews the screening responses and decides the next step. This assistant does not schedule interviews.', 'approved_phase3_workflow'
        elif any(term in text for term in ('responsibilit', 'job description', 'what will i do')) and context.get('job_description'):
            answer, source = 'The approved job description states: ' + context['job_description'][:2000], 'requisition:job_description'
        command.result_metadata = {'question': question.strip(), 'answer': answer, 'source_reference': source, 'requires_hr_follow_up': source == 'human_follow_up_required'}
        self._commit(session)
        return command.result_metadata
