"""Agent 3 review page. The API, not Streamlit state, remains authoritative."""

from __future__ import annotations

from urllib.parse import urlencode

import streamlit as st

from frontend.api_client import APIError, client, fetch_all, show_error
from frontend.ui import choose_requisition


def _applications_for_job(requisition_id: str) -> list[dict]:
    return fetch_all(f"/api/requisitions/{requisition_id}/applications")


def _render_evaluation(evaluation: dict) -> None:
    st.caption("SIMULATED — decision-support recommendation. A human makes every progression decision.")
    columns = st.columns(4)
    score = evaluation.get("overall_interview_score")
    columns[0].metric(
        "Interview score", f"{score:.1f} / 100" if score is not None else "Insufficient evidence"
    )
    columns[1].metric("Evidence coverage", f"{evaluation.get('evidence_coverage', 0):.0%}")
    columns[2].metric("Confidence", f"{evaluation.get('confidence', 0):.0%}")
    columns[3].metric("Rubric version", str(evaluation.get("interview_rubric_version", "")))
    st.subheader("Evidence by criterion")
    for item in evaluation.get("criteria", []):
        title = f"{item.get('label', item.get('criterion', 'Criterion'))} — {item.get('status', 'unknown')}"
        with st.expander(title, expanded=True):
            st.write(item.get("rationale", ""))
            st.caption(f"Anchor level: {item.get('anchor_level')} | Score: {item.get('score')}")
            for evidence in item.get("evidence", []):
                st.markdown(f"**Candidate turn {evidence.get('turn_id', 'unknown')}**")
                st.write(evidence.get("quote", ""))
    if evaluation.get("review_flags"):
        st.warning("Why this needs attention: " + ", ".join(evaluation["review_flags"]))
    if evaluation.get("followup_questions_for_human"):
        st.subheader("Suggested job-related follow-up questions")
        for question in evaluation["followup_questions_for_human"]:
            st.write("- " + question)


def _downloads(api, application_id: str) -> None:
    st.subheader("Download deterministic report")
    reports = api.get(f"/api/applications/{application_id}/interview-reports").get("items", [])
    hashes = {report["format"]: report.get("sha256", "") for report in reports}
    for fmt, label, mime in (
        ("pdf", "PDF", "application/pdf"),
        ("docx", "DOCX", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("json", "JSON", "application/json"),
    ):
        cache_key = f"interview_report:{application_id}:{fmt}"
        if st.button(f"Prepare {label}", key=f"prepare:{cache_key}"):
            st.session_state[cache_key] = api.download(
                f"/api/applications/{application_id}/interview-report?" + urlencode({"format": fmt})
            )
            st.rerun()
        content = st.session_state.get(cache_key)
        if content:
            st.download_button(
                f"Download {label}",
                content,
                file_name=f"interview-report.{fmt}",
                mime=mime,
                key=f"download:{cache_key}",
            )
            if hashes.get(fmt):
                st.caption(f"SHA-256: {hashes[fmt]}")


def render() -> None:
    st.title("Interview report — Agent 3")
    st.write("Review consented simulated-call evidence, then retain human control over the final decision.")
    job = choose_requisition("interview_report_job_select")
    if not job:
        return
    try:
        applications = _applications_for_job(str(job["requisition_id"]))
    except APIError as exc:
        show_error(exc)
        return
    if not applications:
        st.info("No applications exist for this requisition.")
        return
    choices = {item["application_id"]: item for item in applications}
    application_id = st.selectbox(
        "Candidate application",
        list(choices),
        format_func=lambda key: (
            f"{choices[key].get('candidate_name', 'Candidate')} | {choices[key].get('current_status', '')}"
        ),
    )
    application = choices[application_id]
    api = client()
    evaluation = None
    try:
        evaluation = api.get(f"/api/applications/{application_id}/interview-evaluation")
    except APIError as exc:
        if exc.status_code != 404:
            show_error(exc)
    if evaluation is None:
        st.info(
            "No interview evaluation exists. It can only be created after HR has reviewed a completed, consented screening."
        )
        if st.button("Generate interview evaluation", type="primary"):
            try:
                api.post(f"/api/applications/{application_id}/interview-evaluate")
                st.rerun()
            except APIError as exc:
                show_error(exc)
        return
    _render_evaluation(evaluation)
    _downloads(api, application_id)
    if st.session_state.get("current_user", {}).get("role") == "recruiter":
        st.subheader("Human score override")
        scored = [item for item in evaluation.get("criteria", []) if item.get("anchor_level") is not None]
        if scored:
            with st.form(f"interview_override:{application_id}"):
                by_key = {item["criterion"]: item for item in scored}
                criterion = st.selectbox(
                    "Criterion", list(by_key), format_func=lambda key: by_key[key].get("label", key)
                )
                level = st.selectbox(
                    "Replacement anchor level", [0, 1, 2, 3, 4], index=int(by_key[criterion]["anchor_level"])
                )
                reason = st.text_area("Override reason", max_chars=2000)
                if st.form_submit_button("Record reasoned override"):
                    if len(reason.strip()) < 3:
                        st.error("A meaningful override reason is required.")
                    else:
                        try:
                            api.post(
                                f"/api/applications/{application_id}/interview-score-review",
                                json={"criterion": criterion, "new_level": level, "reason": reason.strip()},
                            )
                            st.success("Override recorded; the original AI result remains preserved.")
                        except APIError as exc:
                            show_error(exc)
        st.subheader("Final HR decision — gate 4")
        if application.get("current_status") == "Interview Scored - HR Review":
            with st.form(f"final_decision:{application_id}"):
                decision = st.radio("Decision", ["approve", "hold", "close"], horizontal=True)
                reason = st.text_area("Decision reason", max_chars=2000, key=f"final_reason:{application_id}")
                acknowledged = st.checkbox("I reviewed the cited evidence, flags, report, and any overrides.")
                if st.form_submit_button("Record final human decision"):
                    if not acknowledged or len(reason.strip()) < 3:
                        st.error("Acknowledge review and provide a meaningful reason.")
                    else:
                        try:
                            api.post(
                                f"/api/applications/{application_id}/final-decision",
                                json={"decision": decision, "reason": reason.strip()},
                            )
                            st.success("Final human decision recorded. No candidate was contacted.")
                            st.rerun()
                        except APIError as exc:
                            show_error(exc)
        else:
            st.info("Final gate becomes available only at Interview Scored - HR Review.")
