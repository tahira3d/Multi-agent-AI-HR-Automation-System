import streamlit as st
from frontend.api_client import APIError, client, fetch_all, show_error
from frontend.ui import choose_requisition, render_evaluation


def render():
    st.title("Candidate review")
    job = choose_requisition("review_job_select")
    if not job:
        return
    try:
        applications = fetch_all(f"/api/requisitions/{job['requisition_id']}/applications")
    except APIError as exc:
        show_error(exc)
        return
    if not applications:
        st.info("No applications exist for this job.")
        return
    mapping = {a["application_id"]: a for a in applications}
    options = list(mapping)
    previous = st.session_state.get("selected_application_id")
    app_id = st.selectbox("Candidate application", options,
        index=options.index(previous) if previous in options else 0,
        format_func=lambda key: f"{mapping[key]['candidate_name']} | {mapping[key]['current_status']}",
        key="review_application_select")
    st.session_state["selected_application_id"] = app_id
    application = mapping[app_id]
    st.subheader(application["candidate_name"])
    st.write(f"Workflow status: {application['current_status']}")
    evaluation = None
    try:
        evaluation = client().get(f"/api/applications/{app_id}/evaluation")
    except APIError as exc:
        if exc.status_code == 404:
            st.info("No completed evaluation exists. Evaluate a parsed resume, or arrange manual review.")
        else:
            show_error(exc)
    if evaluation:
        render_evaluation(evaluation)
        stale = evaluation["rubric_version"] != job["rubric_version"]
        if stale:
            st.warning("Reevaluate with the current rubric before making a decision.")
        if st.session_state["current_user"]["role"] == "recruiter" and application["current_status"] == "Scored \u2014 HR Review" and not stale:
            st.subheader("Your shortlist decision")
            st.caption("Approval marks the Phase 3 handoff only. No candidate is contacted.")
            with st.form(f"decision_{app_id}_{evaluation['evaluation_id']}"):
                decision = st.radio("Decision", ["approve", "decline", "hold"])
                reason = st.text_area("Reason | required for decline or hold", max_chars=2000)
                confirm = st.checkbox("I reviewed the job-related evidence and uncertainties.")
                submit = st.form_submit_button("Record HR decision", type="primary")
            if submit:
                if not confirm or (decision != "approve" and not reason.strip()):
                    st.error("Confirm your review and enter a reason for declining or holding.")
                else:
                    try:
                        client().post(f"/api/applications/{app_id}/shortlist-decision",
                            json={"evaluation_id": evaluation["evaluation_id"], "decision": decision, "reason": reason.strip()})
                        st.session_state["review_notice"] = "Decision saved with reviewer identity and timestamp."
                        st.rerun()
                    except APIError as exc:
                        show_error(exc)
        else:
            st.info("This application is not awaiting a current-rubric decision by an HR recruiter.")
    if st.session_state.get("review_notice"):
        st.success(st.session_state.pop("review_notice"))
    st.subheader("Application history")
    try:
        history = fetch_all(f"/api/applications/{app_id}/history")
    except APIError as exc:
        show_error(exc)
        return
    for event in history:
        with st.expander(f"{event['created_at']} | {event['action']}"):
            st.write(f"Reviewer / actor: {event.get('actor_id') or 'System'}")
            st.json(event.get("details") or {})
            if event.get("reason"):
                st.write(event["reason"])
