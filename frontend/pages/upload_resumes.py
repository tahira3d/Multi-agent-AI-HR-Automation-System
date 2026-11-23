import streamlit as st
from frontend.api_client import APIError, client, fetch_all, show_error
from frontend.ui import choose_requisition


def render():
    st.title("Resume uploads")
    st.write("Use synthetic or authorized PDF/DOCX resumes. Each file is validated independently.")
    job = choose_requisition("upload_job_select")
    if not job:
        return
    req_id = str(job["requisition_id"])
    with st.form(f"upload_{req_id}", clear_on_submit=True):
        files = st.file_uploader("PDF or DOCX resumes", type=["pdf", "docx"], accept_multiple_files=True)
        submitted = st.form_submit_button("Validate and upload", type="primary")
    if submitted and files:
        try:
            with st.spinner("Validating and extracting text..."):
                result = client().post(f"/api/requisitions/{req_id}/resumes",
                    files=[("files", (f.name, f.getvalue(), f.type or "application/octet-stream")) for f in files])
            st.session_state["upload_result"] = (req_id, result)
        except APIError as exc:
            show_error(exc)
    saved = st.session_state.get("upload_result")
    if saved and saved[0] == req_id:
        result = saved[1]
        columns = st.columns(4)
        for col, label, key in zip(columns, ["Accepted", "Manual review", "Rejected", "Duplicates"],
                                   ["accepted", "needs_manual_review", "rejected", "duplicates"]):
            col.metric(label, result.get(key, 0))
        st.dataframe([{"File": r["filename"], "Result": r["outcome"], "Application": str(r.get("application_id") or ""),
                       "Details": r.get("message") or r.get("reason_code") or "",
                       "Warnings": "; ".join(r.get("warnings") or [])} for r in result["results"]],
                      hide_index=True, use_container_width=True)
    st.subheader("Run GroqCloud resume evaluations")
    st.caption("Unconfigured or unavailable GroqCloud returns an error. No scores are fabricated.")
    try:
        applications = fetch_all(f"/api/requisitions/{req_id}/applications")
    except APIError as exc:
        show_error(exc)
        return
    mapping = {str(a["application_id"]): a for a in applications}
    selected = st.multiselect("Applications to evaluate", list(mapping),
        format_func=lambda key: f"{mapping[key]['candidate_name']} | {mapping[key]['current_status']}",
        key=f"evaluate_{req_id}")
    force = st.checkbox("Create a new evaluation version", value=False)
    if st.button("Evaluate selected applications", type="primary", disabled=not selected):
        for app_id in selected:
            try:
                with st.spinner(f"Evaluating {mapping[app_id]['candidate_name']}..."):
                    response = client().post(f"/api/applications/{app_id}/evaluate",
                        json={"force_reevaluation": force, "expected_rubric_version": job["rubric_version"]})
                evaluation = response.get("evaluation")
                if not evaluation:
                    st.error(response.get("error") or "No completed evaluation was returned.")
                else:
                    st.success(f"{mapping[app_id]['candidate_name']}: score {evaluation['overall_score']:.2f}. HR review required.")
            except APIError as exc:
                show_error(exc)
