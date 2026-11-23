"""Existing HR dashboard extension: approve plans, review reports, record decisions."""
import os
from urllib.parse import urlencode
from uuid import uuid4
import streamlit as st
from frontend.api_client import APIError, client


def _items(value):
    return value if isinstance(value, list) else value.get('items', [])


def _post(api, path, payload, operation):
    key = f'hr_screening_request:{operation}'
    if key not in st.session_state:
        st.session_state[key] = str(uuid4())
    result = api.post(path, json=payload | {'request_id': st.session_state[key]})
    st.session_state.pop(key, None)
    return result


def _link(result):
    sid = result['session']['screening_session_id']
    base = os.getenv('STREAMLIT_BASE_URL', 'http://127.0.0.1:8501').rstrip('/')
    link = base + '/?' + urlencode({'screening': sid})
    st.warning('Private session-scoped candidate link. Share directly with this candidate; this screen sends no invitation.')
    st.code(link, language=None)
    st.text_input('Private candidate access code — share separately', value=result['candidate_access_token'], type='password', key='private_candidate_code:' + result['candidate_access_token'][-12:])
    st.caption('Expires: ' + result['expires_at'])
    if st.button('Hide private link', key='hide_screening_link'):
        st.session_state.pop('screening_created', None)
        st.rerun()


def render(api=None):
    st.title('Voice screening — HR review')
    st.write('Approve questions, review consent-aware local screenings and record human decisions.')
    api = api or client()
    try:
        with st.expander('Local model readiness'):
            if st.button('Check local services'):
                st.json(api.get('/api/screenings/local-status'))
        candidates, review = st.tabs(['Approved candidates', 'Screening review'])
        with candidates:
            eligible = _items(api.get('/api/screenings/eligible'))
            if not eligible:
                st.info('No approved applications await a new screening. Use the existing shortlist review first.')
            else:
                mapping = {v['application_id']: v for v in eligible}
                aid = st.selectbox('Approved application', list(mapping), format_func=lambda key: mapping[key].get('candidate_name', key) + ' — ' + mapping[key].get('job_title', ''))
                # A prior release used this as a form key. Streamlit keeps form keys
                # read-only through hot reloads, so remove that stale browser state.
                legacy_plan_form_key = f'screening_plan:{aid}'
                if legacy_plan_form_key in st.session_state:
                    del st.session_state[legacy_plan_form_key]

                plan_state_key = f'hr_plan_data__{aid}'
                plan_form_key = f'hr_plan_form__{aid}'
                if st.button('Prepare resume-aware question plan', key='prepare_screening_plan'):
                    st.session_state[plan_state_key] = api.get(f'/api/applications/{aid}/screening/plan')
                plan = st.session_state.get(plan_state_key)
                if plan:
                    st.caption('Check job scope and unverified resume claims. Approving the plan also permits the two bounded clarification/example follow-up templates.')
                    with st.expander('Bounded follow-up templates included in this approval'):
                        for template in plan.get('followup_templates', []):
                            st.write(template)
                    with st.form(plan_form_key):
                        approved = []
                        for index, question in enumerate(plan['questions']):
                            st.caption('Source: ' + question['source_reference'])
                            text = st.text_area(f"Question {index + 1} — {question['topic']}", question['text'], max_chars=1200, key=f'plan_q:{aid}:{index}')
                            mandatory = question['topic'] in {'availability', 'notice_period'}
                            include = st.checkbox('Include this question', value=True, disabled=mandatory, key=f'include_q:{aid}:{index}')
                            if include:
                                approved.append(question | {'text': text.strip()})
                        language = st.selectbox('Initial screening language', ['en', 'ur'], index=1 if plan.get('default_language') == 'ur' else 0)
                        acknowledge = st.checkbox('I reviewed and approve these job-related questions and the bounded follow-up templates.')
                        if st.form_submit_button('Create authorized screening'):
                            if not acknowledge:
                                st.warning('Review and approve the questions and bounded follow-up templates first.')
                            else:
                                st.session_state['screening_created'] = _post(api, f'/api/applications/{aid}/screening/start', {'approved_questions': approved, 'language': language}, 'start:' + aid)
                                st.rerun()
            if st.session_state.get('screening_created'):
                _link(st.session_state['screening_created'])
        with review:
            sessions = _items(api.get('/api/screenings'))
            if not sessions:
                st.info('No screening sessions yet.')
                return
            mapping = {s['screening_session_id']: s for s in sessions}
            st.dataframe([{'Session': key, 'Job': s.get('job_title'), 'Status': s['status'], 'Consent': s['consent_status'], 'Progress': s['progress']} for key, s in mapping.items()], hide_index=True)
            sid = st.selectbox('Screening session', list(mapping), format_func=lambda key: mapping[key].get('job_title', key) + ' — ' + mapping[key]['status'])
            state = api.get(f'/api/screenings/{sid}')
            st.write('Status: ' + state['status'])
            if state.get('issues'):
                st.warning('\n'.join(state['issues']))
            if state['status'] not in {'closed', 'approved_for_interview', 'expired'} and st.button('Create fresh private access link', key=f'new_link:{sid}'):
                st.session_state['screening_created'] = api.post(f'/api/screenings/{sid}/access')
                st.rerun()
            if state['report_available']:
                st.subheader('Factual report — candidate statements remain unverified')
                st.json(api.get(f'/api/screenings/{sid}/report'))
                with st.expander('Original recognition and confirmed corrections'):
                    st.json(api.get(f'/api/screenings/{sid}/transcript'))
                if state['status'] in {'completed', 'held'}:
                    with st.form(f'screening_decision:{sid}'):
                        decision = st.selectbox('HR decision', ['approve', 'hold', 'clarify', 'close'])
                        reason = st.text_area('Reason / specific job-related clarification', max_chars=2000)
                        acknowledged = st.checkbox('I reviewed the report, confirmed answers and unresolved issues.')
                        st.caption('Approval prepares Phase 4. It schedules no interview and sends no invitation.')
                        if st.form_submit_button('Record HR decision'):
                            if not acknowledged:
                                st.warning('Review the report and acknowledge your review before deciding.')
                            elif len(reason.strip()) < 3:
                                st.warning('Provide a meaningful decision reason.')
                            else:
                                _post(api, f'/api/screenings/{sid}/hr-decision', {'decision': decision, 'reason': reason.strip(), 'review_acknowledged': acknowledged}, f'decision:{sid}:{decision}')
                                st.rerun()
            else:
                st.info('A completed report is required before an HR decision. Declined consent and assistance requests do not automatically disqualify candidates.')
            with st.expander('Workflow and approval audit history'):
                st.json(state.get('audit_history', []))
                st.json(state.get('hr_decisions', []))
            if st.button('Refresh screening state'):
                st.rerun()
    except APIError as exc:
        st.error(str(exc))
