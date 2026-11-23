"""Start with: streamlit run frontend/app.py."""

# ruff: noqa: E402 -- imports depend on the project root when launched by Streamlit
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
import streamlit as st
from frontend.api_client import APIClient, APIError, client, show_error
from frontend.pages import dashboard, create_job, upload_resumes, candidate_rankings, candidate_review
from frontend.pages import interview_report, voice_screening, screening_review

load_dotenv(ROOT / ".env", override=False)
st.set_page_config(
    page_title="Hiring Workspace",
    page_icon=":material/group:",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(
    """
<style>
.block-container {padding-top: 2rem; max-width: 1440px;}
[data-testid="stMetric"] {background: rgba(127,127,127,.07); border-radius: 12px; padding: 1rem;}
[data-testid="stSidebar"] {border-right: 1px solid rgba(127,127,127,.2);}
</style>
""",
    unsafe_allow_html=True,
)


def sign_in():
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.title("Hiring Workspace")
        st.write("A secure workspace for evidence-based resume review.")
        if st.session_state.get("login_notice"):
            st.info(st.session_state.pop("login_notice"))
        with st.form("sign_in", clear_on_submit=True):
            email = st.text_input("Work email", max_chars=254)
            password = st.text_input("Password", type="password", max_chars=200)
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            if not email.strip() or not password:
                st.error("Enter your email and password.")
            else:
                try:
                    result = APIClient().post(
                        "/api/auth/login", json={"email": email.strip(), "password": password}
                    )
                    token = result["access_token"]
                    st.session_state["current_user"] = APIClient(token).get("/api/auth/me")
                    st.session_state["access_token"] = token
                    st.rerun()
                except APIError as exc:
                    st.error(str(exc))
        st.caption(
            "Accounts are provisioned by an administrator. AI recommendations always require HR review."
        )


candidate_id = st.query_params.get("screening")
candidate_token = st.query_params.get("access")
if candidate_id and candidate_token:
    st.session_state["candidate_screening_context"] = (candidate_id, candidate_token)
    del st.query_params["access"]
if candidate_id:

    def candidate_entry():
        context = st.session_state.get("candidate_screening_context")
        if context and context[0] == candidate_id:
            voice_screening.render(*context)
            return
        st.title("Private voice screening")
        st.write("Enter the expiring access code supplied privately by HR. No candidate account is required.")
        with st.form("candidate_access"):
            code = st.text_input("Private screening access code", type="password")
            if st.form_submit_button("Open my screening") and code.strip():
                st.session_state["candidate_screening_context"] = (candidate_id, code.strip())
                st.rerun()

    st.navigation(
        [st.Page(candidate_entry, title="Private voice screening", default=True)], position="hidden"
    ).run()
    st.stop()


if not st.session_state.get("access_token"):
    # Always use explicit navigation, even before login, to suppress legacy
    # auto-discovered pages and keep every direct URL behind the login gate.
    st.navigation(
        [st.Page(sign_in, title="Sign in", default=True, url_path="signin")], position="hidden"
    ).run()
    st.stop()
try:
    st.session_state["current_user"] = client().get("/api/auth/me")
except APIError as exc:
    show_error(exc)
    st.stop()

user = st.session_state["current_user"]
with st.sidebar:
    st.title("Hiring Workspace")
    st.write(user.get("full_name") or user.get("name") or user["email"])
    st.caption(user["role"].title())
    st.caption("Candidates progress only after an authorized HR decision.")
    if st.button("Sign out", use_container_width=True):
        notice = "You have signed out."
        try:
            client().post("/api/auth/logout")
        except APIError:
            notice = "Signed out locally. Server token revocation could not be confirmed."
        st.session_state.clear()
        st.session_state["login_notice"] = notice
        st.rerun()

review_page = st.Page(
    candidate_review.render, title="Candidate review", icon=":material/fact_check:", url_path="review"
)
st.session_state["_review_page"] = review_page
st.navigation(
    [
        st.Page(
            dashboard.render, title="Overview", icon=":material/dashboard:", default=True, url_path="overview"
        ),
        st.Page(create_job.render, title="Job requisitions", icon=":material/work:", url_path="jobs"),
        st.Page(
            upload_resumes.render, title="Resume uploads", icon=":material/upload_file:", url_path="uploads"
        ),
        st.Page(
            candidate_rankings.render,
            title="Candidate rankings",
            icon=":material/leaderboard:",
            url_path="rankings",
        ),
        review_page,
        st.Page(
            screening_review.render, title="Voice screening", icon=":material/mic:", url_path="screenings"
        ),
        st.Page(
            interview_report.render,
            title="Interview report",
            icon=":material/description:",
            url_path="interview-report",
        ),
    ]
).run()
