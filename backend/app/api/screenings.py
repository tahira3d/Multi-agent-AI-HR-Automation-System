"""Session-scoped candidate voice APIs and recruiter-only review gates."""
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import bearer, get_current_user, require
from app.core.config import get_settings
from app.core.permissions import Permission
from app.core.screening_config import get_voice_settings
from app.core.status import Status
from app.database.connection import get_db
from app.database.models import Application, Approval, AuditEvent, Candidate, Evaluation, Requisition, User
from app.database.screening_models import ScreeningCommand, ScreeningSession
from app.schemas.screening import RequestBody, StartBody, ConsentBody, CorrectionBody, ControlBody, DecisionBody


def private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'


router = APIRouter(prefix='/api', tags=['Local voice screening'], dependencies=[Depends(private_response)])


def issue_candidate_access(session_id):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expiry = now + timedelta(minutes=get_voice_settings().screening_token_minutes)
    payload = {'sub': str(session_id), 'sid': str(session_id), 'scope': 'screening:session',
               'aud': 'candidate-screening', 'iat': int(now.timestamp()), 'exp': int(expiry.timestamp()), 'jti': str(uuid4())}
    token = jwt.encode(payload, settings.require_jwt_secret(), algorithm=settings.jwt_algorithm)
    return token, expiry.isoformat()


def _service(db, user=None, candidate_session_id=None):
    from app.services.screening_service import ScreeningService
    try:
        return ScreeningService(db, user=user, candidate_session_id=candidate_session_id)
    except ValueError:
        raise HTTPException(503, 'Local screening configuration is unavailable. Check the documented local model settings.') from None


def access(screening_id: UUID, credentials=Depends(bearer), db: Session = Depends(get_db)):
    if credentials is None:
        raise HTTPException(401, 'A valid screening access token is required.')
    settings = get_settings()
    try:
        claims = jwt.decode(credentials.credentials, settings.require_jwt_secret(), algorithms=[settings.jwt_algorithm],
            audience='candidate-screening', options={'require': ['sub', 'sid', 'scope', 'aud', 'iat', 'exp', 'jti']})
        if claims['scope'] != 'screening:session' or claims['sid'] != str(screening_id) or claims['sub'] != str(screening_id):
            raise HTTPException(403, 'This candidate token cannot access another screening session.')
        service = _service(db, candidate_session_id=screening_id)
        service.get(screening_id)
        return service
    except jwt.InvalidTokenError:
        # Ordinary HR tokens follow the existing account, role and ownership checks.
        user = get_current_user(credentials, db)
        service = _service(db, user=user)
        service.get(screening_id)
        return service


@router.get('/screenings/local-status')
def local_status(user: User = Depends(require(Permission.APPROVE_SCREENING))):
    from app.integrations.local_voice import get_voice_runtime
    try:
        result = get_voice_runtime().status()
        import httpx
        settings = get_voice_settings()
        try:
            with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                response = client.get(settings.ollama_base_url.rstrip('/') + '/api/tags')
                names = [model.get('name') for model in response.json().get('models', [])] if response.status_code == 200 else []
                result['ollama_service_available'] = response.status_code == 200
                result['ollama_model_present'] = settings.ollama_model in names
        except (httpx.HTTPError, ValueError, TypeError):
            result['ollama_service_available'] = False
            result['ollama_model_present'] = False
        return result
    except (ValueError, RuntimeError):
        raise HTTPException(503, 'Local voice configuration is invalid. Check the documented local model settings.') from None


@router.get('/screenings/eligible')
def eligible(db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    rows = db.execute(select(Application, Candidate.full_name, Requisition.job_title)
        .join(Candidate, Candidate.candidate_id == Application.candidate_id)
        .join(Requisition, Requisition.requisition_id == Application.requisition_id)
        .join(Evaluation, (Evaluation.application_id == Application.application_id) & Evaluation.is_active.is_(True))
        .join(Approval, (Approval.evaluation_id == Evaluation.evaluation_id) & (Approval.approval_type == 'shortlist') & (Approval.decision == 'approve'))
        .where(Requisition.created_by == user.user_id, Application.current_status == Status.APPROVED_FOR_SCREENING,
               Evaluation.rubric_version == Requisition.rubric_version,
               ~select(ScreeningSession.screening_session_id).where(ScreeningSession.application_id == Application.application_id).exists())
        .order_by(Application.created_at.desc()).limit(100)).all()
    return {'items': [{'application_id': str(app.application_id), 'candidate_id': str(app.candidate_id),
                      'candidate_name': name, 'requisition_id': str(app.requisition_id), 'job_title': title,
                      'current_status': app.current_status} for app, name, title in rows]}


@router.get('/screenings')
def screening_list(db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    return {'items': _service(db, user=user).list_owned()}


@router.get('/applications/{application_id}/screening/plan')
def screening_plan(application_id: UUID, db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    return _service(db, user=user).plan(application_id)


@router.post('/applications/{application_id}/screening/start')
def screening_start(application_id: UUID, body: StartBody, db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    session = _service(db, user=user).start(application_id, body.approved_questions, language=body.language, request_id=body.request_id)
    token, expiry = issue_candidate_access(session['screening_session_id'])
    return {'session': session, 'candidate_access_token': token, 'expires_at': expiry}


@router.post('/screenings/{screening_id}/access')
def renew_access(screening_id: UUID, db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    session = _service(db, user=user).get(screening_id)
    token, expiry = issue_candidate_access(screening_id)
    return {'session': session, 'candidate_access_token': token, 'expires_at': expiry}


@router.get('/screenings/{screening_id}')
def screening_get(screening_id: UUID, service=Depends(access)):
    result = service.get(screening_id)
    result['retention_policy'] = f'Raw audio is discarded after processing. Authorized transcripts and reports are retained for {get_voice_settings().screening_retention_days} days, subject to authorized removal.'
    if service.user is not None:
        events = service.db.scalars(select(AuditEvent).where(AuditEvent.entity_type == 'screening', AuditEvent.entity_id == str(screening_id)).order_by(AuditEvent.seq).limit(200)).all()
        result['audit_history'] = [{'action': event.action, 'actor_id': str(event.actor_id) if event.actor_id else None,
                                   'created_at': event.created_at, 'details': event.details} for event in events]
        decisions = service.db.scalars(select(ScreeningCommand).where(ScreeningCommand.screening_session_id == screening_id,
            ScreeningCommand.operation == 'hr_decision').order_by(ScreeningCommand.created_at).limit(100)).all()
        result['hr_decisions'] = [command.result_metadata for command in decisions]
    return result


@router.post('/screenings/{screening_id}/consent')
def consent(screening_id: UUID, body: ConsentBody, service=Depends(access)):
    return service.consent(screening_id, body.status, retain_transcript=body.retain_transcript,
                           request_id=body.request_id, language=body.language)


@router.post('/screenings/{screening_id}/audio')
def audio(screening_id: UUID, question_id: UUID = Form(...), request_id: str = Form(..., min_length=8, max_length=128),
          file: UploadFile = File(...), service=Depends(access)):
    state = service.get(screening_id)
    if state['consent_status'] != 'accepted' or not state['retain_transcript']:
        raise HTTPException(403, 'Accepted audio-processing and transcript-retention consent is required.')
    maximum = get_voice_settings().voice_max_audio_bytes
    try:
        content = file.file.read(maximum + 1)
    finally:
        file.file.close()
    if len(content) > maximum:
        raise HTTPException(413, 'Record a shorter answer within the configured audio size limit.')
    return service.submit_audio(screening_id, question_id, content, request_id)


@router.get('/screenings/{screening_id}/voice')
def question_voice(screening_id: UUID, question_id: UUID = Query(...), service=Depends(access)):
    content = service.question_voice(screening_id, question_id)
    return Response(content, media_type='audio/wav', headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


@router.get('/screenings/{screening_id}/questions')
def questions(screening_id: UUID, service=Depends(access)):
    return {'items': service.questions(screening_id)}


@router.get('/screenings/{screening_id}/transcript')
def transcript(screening_id: UUID, service=Depends(access)):
    return {'items': service.transcript(screening_id)}


@router.post('/screenings/{screening_id}/answer-correction')
def correction(screening_id: UUID, body: CorrectionBody, service=Depends(access)):
    return service.confirm_answer(screening_id, body.answer_id, corrected_text=body.corrected_text, request_id=body.request_id)


@router.post('/screenings/{screening_id}/control')
def control(screening_id: UUID, body: ControlBody, service=Depends(access)):
    if body.action == 'job_question':
        return service.candidate_question(screening_id, body.text, body.request_id)
    state = service.get(screening_id)
    if state['consent_status'] != 'accepted' and body.action in {'human', 'reschedule', 'stop'}:
        status = {'human': 'human_requested', 'reschedule': 'reschedule_requested', 'stop': 'stopped'}[body.action]
        return service.consent(screening_id, status, False, body.request_id)
    return service.control(screening_id, body.action, body.request_id, reason=body.reason)


@router.post('/screenings/{screening_id}/finish')
def finish(screening_id: UUID, body: RequestBody, service=Depends(access)):
    return service.finish(screening_id, body.request_id)


@router.get('/screenings/{screening_id}/report')
def report(screening_id: UUID, service=Depends(access)):
    return service.report(screening_id)


@router.post('/screenings/{screening_id}/hr-decision')
def decision(screening_id: UUID, body: DecisionBody, db: Session = Depends(get_db), user: User = Depends(require(Permission.APPROVE_SCREENING))):
    return _service(db, user=user).hr_decision(screening_id, 'clarification' if body.decision == 'clarify' else body.decision,
        body.reason, body.reviewed_report, body.request_id)
