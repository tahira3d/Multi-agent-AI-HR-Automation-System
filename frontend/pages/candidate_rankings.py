import streamlit as st
from frontend.api_client import APIError, fetch_all, show_error
from frontend.ui import choose_requisition, criterion_rows, render_evaluation, score_of


def render():
    st.title("Candidate rankings")
    st.write("Prioritize human review using job-related evidence. Scores never advance or reject candidates.")
    job = choose_requisition("rank_job_select")
    if not job:
        return
    try:
        rankings = fetch_all(f"/api/requisitions/{job['requisition_id']}/rankings")
    except APIError as exc:
        show_error(exc)
        return
    if not rankings:
        st.info("Upload resumes and run an evaluation to create a review queue.")
        return
    statuses = sorted({r["current_status"] for r in rankings})
    statuses = st.multiselect("Workflow status", statuses, default=statuses)
    descending = st.toggle("Highest score first", value=True)
    filtered = sorted([r for r in rankings if r["current_status"] in statuses],
                      key=lambda r: score_of(r) or 0, reverse=descending)
    rows = []
    for item in filtered:
        row = {"Candidate": item["candidate_name"], "Score": score_of(item), "Status": item["current_status"],
               "Rubric": item["rubric_version"], "Model": item["model_version"], "Application": item["application_id"]}
        row.update({c["criterion"]: c["score"] for c in criterion_rows(item)})
        rows.append(row)
    if not rows:
        st.info("No applications match these filters.")
        return
    st.dataframe(rows, hide_index=True, use_container_width=True)
    if any(r.get("rubric_outdated") for r in filtered):
        st.warning("Older rubric versions are present. Reevaluate pending applications before deciding.")
    by_id = {r["application_id"]: r for r in filtered}
    app_id = st.selectbox("Inspect an application", list(by_id),
                         format_func=lambda key: by_id[key]["candidate_name"])
    st.session_state["selected_application_id"] = app_id
    if st.button("Open HR review", type="primary"):
        st.session_state.pop("review_application_select", None)
        st.session_state.pop("review_job_select", None)
        st.switch_page(st.session_state["_review_page"])
    with st.expander("Evidence preview", expanded=True):
        render_evaluation(by_id[app_id])
