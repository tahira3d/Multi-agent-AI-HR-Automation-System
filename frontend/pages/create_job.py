import re
import pandas as pd
import streamlit as st
from frontend.api_client import APIError, client, show_error
from frontend.ui import choose_requisition

DEFAULT_CRITERIA = [
    {"key": "required_skills", "label": "Required skills", "weight": 40.0, "justification": "Required role skills"},
    {"key": "relevant_experience", "label": "Relevant experience", "weight": 25.0, "justification": "Relevant work responsibilities"},
    {"key": "role_projects", "label": "Role projects", "weight": 20.0, "justification": "Applied project evidence"},
    {"key": "education_certifications", "label": "Education / certifications", "weight": 10.0, "justification": "Only job-related qualifications"},
    {"key": "other_job_related", "label": "Other job evidence", "weight": 5.0, "justification": "Explicitly job-related evidence"},
]


def lines(value):
    return [part.strip() for part in value.splitlines() if part.strip()]


def render():
    st.title("Job requisitions")
    edit = st.radio("Action", ["Create a requisition", "Edit a requisition"], horizontal=True)
    job = choose_requisition("edit_job_select") if edit.startswith("Edit") else {}
    if job is None:
        return
    identity = str(job.get("requisition_id", "new"))
    if job:
        st.caption(f"Current rubric version: {job['rubric_version']}. Changes retain previous versions.")
    with st.form(f"job_form_{identity}"):
        title = st.text_input("Job title", value=job.get("job_title", ""), max_chars=120)
        description = st.text_area("Approved job description", value=job.get("job_description", ""), height=200, max_chars=20000)
        left, right = st.columns(2)
        required = left.text_area("Required skills | one per line", value="\n".join(job.get("required_skills") or []))
        preferred = right.text_area("Preferred skills | one per line", value="\n".join(job.get("preferred_skills") or []))
        experience = left.number_input("Minimum relevant experience (years)", min_value=0.0, max_value=50.0,
                                      step=0.5, value=float(job.get("min_experience_years") or 0))
        location = right.text_input("Work location", value=job.get("location") or "", max_chars=200)
        arrangement = st.text_input("Work arrangement", value=job.get("work_arrangement") or "remote", max_chars=120)
        questions = st.text_area("Screening questions for Phase 3 | one per line", value="\n".join(job.get("screening_questions") or []))
        st.subheader("Job-specific evaluation rubric")
        st.caption("Use only job-related criteria. Weights must total 100%.")
        rubric = st.data_editor(pd.DataFrame((job.get("rubric") or {}).get("criteria", DEFAULT_CRITERIA)),
            num_rows="dynamic", hide_index=True, use_container_width=True, key=f"rubric_{identity}",
            column_config={"weight": st.column_config.NumberColumn("Weight (%)", min_value=0.1, max_value=100.0, required=True)})
        save = st.form_submit_button("Save approved requisition", type="primary")
    if not save:
        return
    criteria = []
    try:
        for item in rubric.fillna("").to_dict("records"):
            row = {"key": str(item["key"]).strip(), "label": str(item["label"]).strip(),
                   "weight": float(item["weight"]), "justification": str(item["justification"]).strip()}
            if not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", row["key"]) or not 2 <= len(row["label"]) <= 80:
                raise ValueError("Use stable lowercase keys and labels of 2 to 80 characters.")
            criteria.append(row)
        if not criteria or abs(sum(c["weight"] for c in criteria) - 100) > 0.000001:
            raise ValueError("Weights must total 100%.")
    except (ValueError, KeyError, TypeError) as exc:
        st.error(str(exc))
        return
    payload = {"job_title": title.strip(), "job_description": description.strip(), "required_skills": lines(required),
               "preferred_skills": lines(preferred), "min_experience_years": experience, "location": location.strip(),
               "work_arrangement": arrangement, "screening_questions": lines(questions), "rubric": {"criteria": criteria}}
    try:
        if job:
            payload["expected_rubric_version"] = job["rubric_version"]
            saved = client().patch(f"/api/requisitions/{identity}", json=payload)
        else:
            saved = client().post("/api/requisitions", json=payload)
        st.session_state["selected_requisition_id"] = str(saved["requisition_id"])
        st.success(f"Saved with rubric version {saved['rubric_version']}.")
    except APIError as exc:
        show_error(exc)
