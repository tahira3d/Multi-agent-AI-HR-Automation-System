"""Add Agent 3 reports and fairness findings without altering legacy records."""
from alembic import op
import sqlalchemy as sa

revision = "0005_phase4_interviews_fairness"
down_revision = "0004_auth_lockout_refresh"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("interview_evaluations",
        sa.Column("evaluation_id", sa.Uuid(), primary_key=True),
        sa.Column("application_id", sa.Uuid(), sa.ForeignKey("applications.application_id", ondelete="CASCADE"), nullable=False),
        sa.Column("screening_session_id", sa.Uuid(), sa.ForeignKey("screening_sessions.screening_session_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"), sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rubric_snapshot", sa.JSON(), nullable=False), sa.Column("criterion_results", sa.JSON(), nullable=False),
        sa.Column("overall_score", sa.Float(), nullable=True), sa.Column("evidence_coverage", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"), sa.Column("review_flags", sa.JSON(), nullable=False),
        sa.Column("comparison", sa.JSON(), nullable=False), sa.Column("followup_questions", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.String(40), nullable=False), sa.Column("model_version", sa.String(120), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("screening_session_id", "version", name="uq_interview_eval_version"))
    op.create_index("ix_interview_evaluations_application_id", "interview_evaluations", ["application_id"])
    op.create_index("ix_interview_evaluations_screening_session_id", "interview_evaluations", ["screening_session_id"])
    op.create_table("interview_score_overrides",
        sa.Column("override_id", sa.Uuid(), primary_key=True), sa.Column("evaluation_id", sa.Uuid(), sa.ForeignKey("interview_evaluations.evaluation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("criterion", sa.String(120), nullable=False), sa.Column("original_level", sa.Integer(), nullable=False), sa.Column("new_level", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False), sa.Column("reviewer_id", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_id", "criterion", name="uq_interview_override_criterion"))
    op.create_index("ix_interview_score_overrides_evaluation_id", "interview_score_overrides", ["evaluation_id"])
    op.create_table("generated_reports",
        sa.Column("report_id", sa.Uuid(), primary_key=True), sa.Column("evaluation_id", sa.Uuid(), sa.ForeignKey("interview_evaluations.evaluation_id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False), sa.Column("format", sa.String(8), nullable=False), sa.Column("storage_reference", sa.String(300), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("generated_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("evaluation_id", "format", "version", name="uq_generated_report_version"))
    op.create_index("ix_generated_reports_evaluation_id", "generated_reports", ["evaluation_id"])
    op.create_table("fairness_runs", sa.Column("run_id", sa.Uuid(), primary_key=True), sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.user_id"), nullable=False), sa.Column("stage", sa.String(40), nullable=False), sa.Column("metrics", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("fairness_findings", sa.Column("finding_id", sa.Uuid(), primary_key=True), sa.Column("run_id", sa.Uuid(), sa.ForeignKey("fairness_runs.run_id", ondelete="CASCADE"), nullable=False), sa.Column("severity", sa.String(20), nullable=False), sa.Column("metric", sa.String(80), nullable=False), sa.Column("cohort", sa.String(160), nullable=False), sa.Column("value", sa.Float(), nullable=False), sa.Column("threshold", sa.Float(), nullable=False), sa.Column("acknowledged_by", sa.Uuid(), sa.ForeignKey("users.user_id")), sa.Column("acknowledgement_reason", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_fairness_findings_run_id", "fairness_findings", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_fairness_findings_run_id", table_name="fairness_findings"); op.drop_table("fairness_findings"); op.drop_table("fairness_runs")
    op.drop_index("ix_generated_reports_evaluation_id", table_name="generated_reports"); op.drop_table("generated_reports")
    op.drop_index("ix_interview_score_overrides_evaluation_id", table_name="interview_score_overrides"); op.drop_table("interview_score_overrides")
    op.drop_index("ix_interview_evaluations_screening_session_id", table_name="interview_evaluations"); op.drop_index("ix_interview_evaluations_application_id", table_name="interview_evaluations"); op.drop_table("interview_evaluations")
