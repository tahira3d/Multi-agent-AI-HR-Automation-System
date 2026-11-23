"""Consent-aware local screening. Revision 0003_phase3, revises 0002_phase2."""
from alembic import op
import sqlalchemy as sa

revision = '0003_phase3'
down_revision = '0002_phase2'
branch_labels = None
depends_on = None


def _id(name, target=None, nullable=False, primary_key=False):
    args = [name, sa.Uuid()]
    if target:
        action = None if target == 'users.user_id' else ('RESTRICT' if target.startswith('evaluations.') else 'CASCADE')
        args.append(sa.ForeignKey(target, ondelete=action))
    return sa.Column(*args, nullable=nullable, primary_key=primary_key)


def _json(name):
    return sa.Column(name, sa.JSON(), nullable=False)


def _time(name, nullable=True):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade():
    op.create_table('screening_sessions',
        _id('screening_session_id', primary_key=True), _id('screening_id', 'screenings.screening_id'),
        _id('application_id', 'applications.application_id'), _id('candidate_id', 'candidates.candidate_id'),
        _id('requisition_id', 'requisitions.requisition_id'), _id('evaluation_id', 'evaluations.evaluation_id'), _id('created_by', 'users.user_id'),
        sa.Column('status', sa.String(48), nullable=False), sa.Column('language', sa.String(8), nullable=False),
        sa.Column('consent_status', sa.String(32), nullable=False), sa.Column('retain_transcript', sa.Boolean(), nullable=False),
        _json('context_snapshot'), _json('model_versions'), _json('issues'), sa.Column('state_version', sa.Integer(), nullable=False),
        sa.Column('lease_token', sa.String(64), nullable=True), _time('lease_expires_at'), _time('created_at', False),
        _time('consented_at'), _time('started_at'), _time('ended_at'),
        sa.UniqueConstraint('screening_id', name='uq_screening_session_legacy'), sa.UniqueConstraint('application_id', name='uq_screening_session_application'))
    for column in ['application_id', 'candidate_id', 'requisition_id', 'status']:
        op.create_index(f'ix_screening_sessions_{column}', 'screening_sessions', [column])
    op.create_table('screening_questions', _id('question_id', primary_key=True), _id('screening_session_id', 'screening_sessions.screening_session_id'),
        _id('parent_question_id', 'screening_questions.question_id', nullable=True), sa.Column('text', sa.Text(), nullable=False),
        sa.Column('topic', sa.String(100), nullable=False), sa.Column('source_reference', sa.String(240), nullable=False),
        sa.Column('question_order', sa.Integer(), nullable=False), sa.Column('required', sa.Boolean(), nullable=False),
        sa.Column('is_follow_up', sa.Boolean(), nullable=False), _time('answered_at'), sa.Column('unresolved_reason', sa.Text(), nullable=True),
        sa.UniqueConstraint('screening_session_id', 'question_order', name='uq_screening_question_order'))
    op.create_index('ix_screening_questions_screening_session_id', 'screening_questions', ['screening_session_id'])
    op.create_table('screening_answers', _id('answer_id', primary_key=True), _id('screening_session_id', 'screening_sessions.screening_session_id'),
        _id('question_id', 'screening_questions.question_id'), sa.Column('transcript_text', sa.Text(), nullable=False),
        sa.Column('corrected_text', sa.Text(), nullable=True), sa.Column('response_status', sa.String(32), nullable=False), _json('segments'),
        sa.Column('audio_sha256', sa.String(64), nullable=False), sa.Column('model_version', sa.String(120), nullable=False),
        _time('created_at', False), _time('confirmed_at'), sa.Column('audio_storage_reference', sa.String(240), nullable=True),
        sa.UniqueConstraint('question_id', name='uq_screening_answer_question'),
        sa.UniqueConstraint('screening_session_id', 'audio_sha256', name='uq_screening_audio_digest'))
    for column in ['screening_session_id', 'question_id']:
        op.create_index(f'ix_screening_answers_{column}', 'screening_answers', [column])
    op.create_table('screening_reports', _id('report_id', primary_key=True), _id('screening_session_id', 'screening_sessions.screening_session_id'),
        _json('structured_summary'), _time('created_at', False), sa.UniqueConstraint('screening_session_id', name='uq_screening_report_session'))
    op.create_index('ix_screening_reports_screening_session_id', 'screening_reports', ['screening_session_id'])
    op.create_table('screening_commands', _id('command_id', primary_key=True), _id('screening_session_id', 'screening_sessions.screening_session_id'),
        sa.Column('request_id', sa.String(128), nullable=False), sa.Column('operation', sa.String(40), nullable=False),
        sa.Column('payload_sha256', sa.String(64), nullable=False), _json('result_metadata'), _time('created_at', False),
        sa.UniqueConstraint('screening_session_id', 'request_id', name='uq_screening_command_request'))
    op.create_index('ix_screening_commands_screening_session_id', 'screening_commands', ['screening_session_id'])


def downgrade():
    for table in ['screening_commands', 'screening_reports', 'screening_answers', 'screening_questions', 'screening_sessions']:
        op.drop_table(table)
