import streamlit as st
from frontend.api_client import APIError, fetch_all, show_error


def choose_requisition(key):
    try:
        jobs = fetch_all("/api/requisitions")
    except APIError as exc:
        show_error(exc)
        return None
    if not jobs:
        st.info("Create a job requisition to begin.")
        return None
    by_id = {str(job["requisition_id"]): job for job in jobs}
    options = list(by_id)
    previous = st.session_state.get("selected_requisition_id")
    selected = st.selectbox("Job requisition", options,
        index=options.index(previous) if previous in options else 0,
        format_func=lambda item: f"{by_id[item]['job_title']} | rubric v{by_id[item].get('rubric_version', 1)}",
        key=key)
    st.session_state["selected_requisition_id"] = selected
    return by_id[selected]


def score_of(item):
    value = item.get("overall_score", item.get("total_score"))
    return float(value) if value is not None else None


def criterion_rows(evaluation):
    findings = evaluation.get("criterion_findings") or {}
    explanations = evaluation.get("criterion_explanations") or {}
    return [{"criterion": key, "score": score, "finding": findings.get(key, ""),
             "explanation": explanations.get(key, "")}
            for key, score in (evaluation.get("criterion_scores") or {}).items()]


def render_evaluation(evaluation):
    score = score_of(evaluation)
    columns = st.columns(4)
    columns[0].metric("Evidence score", f"{score:.2f} / 100" if score is not None else "Unavailable")
    columns[1].metric("Rubric", str(evaluation.get("rubric_version", "")))
    columns[2].metric("Evaluation", str(evaluation.get("version", "")))
    columns[3].metric("Model", str(evaluation.get("model_version", "")))
    st.caption(f"Evaluated: {evaluation.get('created_at', '')}")
    st.caption("Missing evidence indicates uncertainty. It does not establish lack of competence.")
    rows = criterion_rows(evaluation)
    if rows:
        st.dataframe(rows, hide_index=True, use_container_width=True)
    st.subheader("Supporting resume evidence")
    for i, item in enumerate(evaluation.get("evidence") or []):
        with st.expander(f"{item['criterion']} | {item.get('evidence_type', 'explicit')}", expanded=i < 3):
            st.text(item["source_text"])
            st.write(item["explanation"])
            st.caption(f"Source offsets in redacted text: {item.get('source_start')} to {item.get('source_end')}")
    for item in evaluation.get("missing_or_unclear_items", []):
        st.info(item)
    for warning in evaluation.get("parsing_warnings", []):
        st.warning(warning)
    st.subheader("Suggested HR review")
    st.write(evaluation.get("recommendation_for_human_review", ""))
