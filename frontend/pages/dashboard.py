import streamlit as st
from frontend.api_client import APIError, client, show_error


def render():
    st.title("Hiring overview")
    st.write("Track requisitions, resume evidence, and decisions awaiting your review.")
    try:
        data = client().get("/api/dashboard")
    except APIError as exc:
        show_error(exc)
        return
    columns = st.columns(3)
    if data.get("evaluation_available") is False:
        st.warning("Resume evaluation is unavailable until the AI provider is configured. You can create jobs and upload resumes.")
    metrics = [("Job requisitions", "total_requisitions"), ("Resumes received", "total_resumes"),
               ("Evaluated applications", "total_evaluated"), ("Awaiting HR review", "awaiting_review"),
               ("Approved for screening", "approved_for_screening")]
    for i, (label, key) in enumerate(metrics):
        columns[i % 3].metric(label, data.get(key, 0))
    st.subheader("Recent activity")
    if data.get("recent_activity"):
        st.dataframe(data["recent_activity"], hide_index=True, use_container_width=True)
    else:
        st.info("No activity is available for your requisitions.")
    st.caption("Phase 1/2 never contacts candidates or conducts screening calls.")
