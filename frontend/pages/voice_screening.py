"""Candidate-only consent-first microphone screening; no continuous streaming."""
import hashlib
from uuid import UUID, uuid4
import requests
import streamlit as st
from frontend.api_client import APIClient, APIError


def _session(value):
    return value.get('session', value)


def _request_id(sid, action):
    key = f'voice_request:{sid}:{action}'
    if key not in st.session_state:
        st.session_state[key] = str(uuid4())
    return st.session_state[key]


def _post(api, sid, action, payload, operation=None):
    operation = operation or action
    result = api.post(f'/api/screenings/{sid}/{action}', json=payload | {'request_id': _request_id(sid, operation)})
    st.session_state.pop(f'voice_request:{sid}:{operation}', None)
    return result


def _safe_error(status):
    return {401: 'This private link has expired. Ask HR for a new link.', 403: 'Access or recording consent is required. Contact HR.',
            404: 'This screening is unavailable.', 409: 'The session changed. Refresh it before retrying.',
            413: 'Record a shorter answer within the 5 MB upload limit.',
            422: 'The recording or response could not be accepted. Check it or request human assistance.',
            503: 'A local voice model is unavailable. Confirmed progress is saved. Retry or request human assistance.'}.get(status, 'The request could not be completed. Refresh or contact HR; confirmed progress is preserved.')


def _voice(api, sid, token, qid):
    response = requests.get(api.base_url + f'/api/screenings/{sid}/voice', headers={'Authorization': 'Bearer ' + token},
                            params={'question_id': qid}, timeout=(10, 180))
    if response.status_code != 200 or not response.headers.get('content-type', '').startswith('audio/'):
        raise APIError(_safe_error(response.status_code), response.status_code)
    return response.content


def _control(api, sid, action, **fields):
    result = _post(api, sid, 'control', {'action': action} | fields, 'control:' + action)
    if result.get('clarification_text'):
        st.session_state[f'clarification:{sid}'] = result['clarification_text']
    st.rerun()


def _help(api, sid, state):
    with st.expander('Pause, stop or get help'):
        st.caption('These choices do not automatically disqualify you. HR reviews the next steps.')
        if st.button('Request a human', key=f'human:{sid}'):
            _control(api, sid, 'human')
        if st.button('Request rescheduling', key=f'reschedule:{sid}'):
            _control(api, sid, 'reschedule')
        if state['consent_status'] == 'accepted':
            action = 'resume' if state['status'] == 'paused' else 'pause'
            if st.button('Resume screening' if action == 'resume' else 'Pause screening', key=f'{action}:{sid}'):
                _control(api, sid, action)
            if st.button('Stop screening', key=f'stop:{sid}'):
                _control(api, sid, 'stop')


def _consent(api, sid, state):
    st.subheader('Your choice before recording')
    st.write('This interviewer is an AI assistant. Your answers are screened against the approved job questions using models on the backend laptop. HR reviews your confirmed answers; the AI makes no hiring decisions.')
    st.write('The microphone stays inactive until you explicitly agree. Raw audio is discarded after local transcription. Original recognized text, your corrections and the factual report are retained only with your permission for HR review. You may decline or request a human without automatic disqualification.')
    st.info(state.get('retention_policy', 'Ask HR about the transcript retention policy before accepting.'))
    language = st.selectbox('Conversation language', ['en', 'ur'], index=1 if state.get('language') == 'ur' else 0, format_func=lambda v: 'English' if v == 'en' else 'اردو / Urdu', key=f'language:{sid}')
    if language == 'ur':
        st.info('Urdu recognition is supported by the local multilingual model. HR-approved question text remains in its approved language. The English Piper voice cannot speak Urdu; choose human-assisted spoken Urdu if needed. Accent and pronunciation are not assessed.')
    agree = st.checkbox('I agree to AI screening and local audio processing.', key=f'understand:{sid}')
    retain = st.checkbox('I allow the transcript and report to be stored for HR review.', key=f'retain:{sid}')
    if st.button('Agree and begin screening', disabled=not (agree and retain), key=f'accept:{sid}'):
        _post(api, sid, 'consent', {'decision': 'accepted', 'retain_transcript': retain, 'retain_audio': False, 'language': language})
        st.rerun()
    if st.button('Decline automated screening', key=f'decline:{sid}'):
        _post(api, sid, 'consent', {'decision': 'declined', 'retain_transcript': False, 'retain_audio': False, 'language': language})
        st.rerun()


def _pending(api, sid, pending):
    aid, original = pending['answer_id'], pending['transcript_text']
    st.subheader('Transcription available — please confirm')
    st.caption('Original recognized text')
    st.text(original)
    corrected = st.text_area('Correct recognition mistakes before confirming', value=pending.get('corrected_text') or original, max_chars=12000, key=f'correction:{sid}:{aid}')
    st.caption('The original recognition and your correction remain distinguishable in the authorized record.')
    if st.button('Confirm this answer', disabled=not corrected.strip(), key=f'confirm:{sid}:{aid}'):
        _post(api, sid, 'answer-correction', {'answer_id': aid, 'corrected_text': corrected.strip() if corrected.strip() != original.strip() else None, 'confirm': True}, 'confirm:' + aid)
        st.rerun()


def _question(api, sid, token, state):
    question = state.get('current_question')
    if not question:
        st.info('Preparing question. Refresh if a local model is still starting.')
        return
    qid = question['question_id']
    st.subheader('Current question')
    st.text(question['text'])
    if st.session_state.get(f'clarification:{sid}'):
        st.info(st.session_state[f'clarification:{sid}'])
    ready = state.get('language') == 'ur'
    if ready:
        st.info('Urdu text / microphone mode; human-assisted spoken Urdu is available. English Piper audio is disabled.')
    else:
        key = f'question_audio:{sid}:{qid}'
        if key not in st.session_state:
            try:
                with st.spinner('Preparing question audio…'):
                    st.session_state[key] = _voice(api, sid, token, qid)
            except (APIError, requests.RequestException):
                st.warning('Question speech is unavailable. Retry or request human assistance.')
        if key in st.session_state:
            st.caption('AI speaking — play the question, then record your answer.')
            st.audio(st.session_state[key], format='audio/wav', autoplay=True)
            ready = True
        elif st.button('Retry question audio', key=f'retry_voice:{sid}:{qid}'):
            st.rerun()
    with st.expander('Clarification and job questions'):
        if st.button('Please clarify this question', key=f'clarify:{sid}:{qid}'):
            _control(api, sid, 'clarify', question_id=qid)
        if st.button('Mark this topic for human follow-up', key=f'skip:{sid}:{qid}'):
            _control(api, sid, 'skip')
        inquiry = st.text_input('Ask about the job', max_chars=1000, key=f'job_question:{sid}:{qid}')
        if st.button('Ask HR information', disabled=not inquiry.strip(), key=f'ask:{sid}:{qid}'):
            result = _post(api, sid, 'control', {'action': 'job_question', 'text': inquiry.strip()}, 'job_question:' + qid)
            st.session_state[f'job_answer:{sid}'] = result.get('answer', 'HR can clarify this.')
        if st.session_state.get(f'job_answer:{sid}'):
            st.info(st.session_state[f'job_answer:{sid}'])
    if not ready:
        return
    st.caption('Ready to record — turn-by-turn audio, not continuous audio streaming. Allow microphone access in browser site settings. Record at most 90 seconds / 5 MB per answer.')
    recording = st.audio_input('Record your answer', sample_rate=16000, key=f'microphone:{sid}:{qid}')
    if recording is not None and st.button('Transcribe answer', key=f'transcribe:{sid}:{qid}'):
        content = recording.getvalue()
        if not content or len(content) > 5242880:
            st.warning('The recording is empty or over 5 MB. Record a shorter answer.')
            return
        operation = f'audio:{qid}:' + hashlib.sha256(content).hexdigest()
        with st.spinner('Processing answer locally…'):
            api.post(f'/api/screenings/{sid}/audio', data={'question_id': qid, 'request_id': _request_id(sid, operation)}, files={'file': ('answer.wav', content, 'audio/wav')})
        st.session_state.pop(f'voice_request:{sid}:{operation}', None)
        st.rerun()


def render(session_id, token):
    st.title('Candidate voice screening')
    try:
        UUID(str(session_id))
        if not token:
            raise ValueError('Missing access')
    except (ValueError, TypeError):
        st.error('A valid private screening link is required. Contact HR.')
        return
    api = APIClient(token)
    try:
        state = _session(api.get(f'/api/screenings/{session_id}'))
        st.write(state.get('job_title', 'Approved job screening'))
        st.caption('Status: ' + state['status'].replace('_', ' '))
        if state['status'] in {'completed', 'held', 'approved_for_interview', 'closed', 'human_requested', 'reschedule_requested', 'stopped', 'consent_declined', 'expired'}:
            st.success('Your screening or assistance request is saved for HR. No automatic hiring decision has been made.')
            return
        _help(api, session_id, state)
        if state['consent_status'] != 'accepted':
            _consent(api, session_id, state)
            return
        if not state.get('retain_transcript', False):
            st.info('Transcript-retention permission is required for automated screening. Request a human alternative.')
            return
        if state['status'] in {'paused', 'transcribing', 'preparing_followup'}:
            st.info('Screening paused.' if state['status'] == 'paused' else 'Processing locally; confirmed progress is saved.')
            if st.button('Refresh session', key=f'refresh:{session_id}'):
                st.rerun()
            return
        progress = state.get('progress', {})
        if progress.get('total'):
            st.progress(min(progress['answered'] / progress['total'], 1.0), text=f"{progress['answered']} of {progress['total']} topics addressed")
        if state.get('pending_answer'):
            _pending(api, session_id, state['pending_answer'])
        elif state.get('can_finish'):
            if st.button('Finish screening and submit for HR review', key=f'finish:{session_id}'):
                _post(api, session_id, 'finish', {})
                st.rerun()
        else:
            _question(api, session_id, token, state)
    except APIError as exc:
        st.error(_safe_error(exc.status_code))
        if exc.status_code in {401, 403} and st.button('Enter a fresh private access code'):
            st.session_state.pop('candidate_screening_context', None)
            st.rerun()
    except requests.RequestException:
        st.error('The backend is unavailable. Confirmed progress remains saved.')
