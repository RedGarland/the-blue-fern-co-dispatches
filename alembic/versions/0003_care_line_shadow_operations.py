from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_care_line_shadow_operations"
down_revision = "0002_universal_entity_resolution"
branch_labels = None
depends_on = None


TRIGGERS = (
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_ingestion_executions_append_only_update
    BEFORE UPDATE ON shadow_ingestion_executions
    BEGIN
        SELECT RAISE(ABORT, 'shadow ingestion executions are append-only');
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS trg_shadow_ingestion_executions_append_only_delete
    BEFORE DELETE ON shadow_ingestion_executions
    BEGIN
        SELECT RAISE(ABORT, 'shadow ingestion executions are append-only');
    END
    """,
)


def upgrade() -> None:
    op.create_table(
        "shadow_ingestion_runs",
        sa.Column("shadow_run_id", sa.String(length=80), primary_key=True),
        sa.Column("producer", sa.String(length=80), nullable=False),
        sa.Column("input_manifest_hash", sa.String(length=128), nullable=False),
        sa.Column("date_from", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("date_to", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("adapter_version", sa.String(length=80), nullable=False),
        sa.Column("resolver_version", sa.String(length=80), nullable=False),
        sa.Column("configuration_hash", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("input_file_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eligible_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("excluded_record_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("match_candidate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ambiguous_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unresolved_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("automatic_matchable_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_shadow_ingestion_runs_producer"), "shadow_ingestion_runs", ["producer"], unique=False)
    op.create_index(op.f("ix_shadow_ingestion_runs_input_manifest_hash"), "shadow_ingestion_runs", ["input_manifest_hash"], unique=False)

    op.create_table(
        "shadow_ingestion_executions",
        sa.Column("execution_id", sa.String(length=80), primary_key=True),
        sa.Column("shadow_run_id", sa.String(length=80), sa.ForeignKey("shadow_ingestion_runs.shadow_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("host_label", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("command_options_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("report_path", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("error_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(op.f("ix_shadow_ingestion_executions_shadow_run_id"), "shadow_ingestion_executions", ["shadow_run_id"], unique=False)

    op.create_table(
        "shadow_ingestion_record_results",
        sa.Column("record_result_id", sa.String(length=80), primary_key=True),
        sa.Column("shadow_run_id", sa.String(length=80), sa.ForeignKey("shadow_ingestion_runs.shadow_run_id", ondelete="CASCADE"), nullable=False),
        sa.Column("producer_record_id", sa.String(length=160), nullable=False),
        sa.Column("source_file", sa.String(length=1024), nullable=False, server_default=""),
        sa.Column("input_record_hash", sa.String(length=128), nullable=False),
        sa.Column("result_type", sa.String(length=40), nullable=False),
        sa.Column("candidate_id", sa.String(length=80), sa.ForeignKey("candidate_events.candidate_id", ondelete="SET NULL"), nullable=True),
        sa.Column("exclusion_reason", sa.String(length=120), nullable=True),
        sa.Column("warning_codes_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("result_type IN ('created', 'existing', 'updated', 'excluded', 'withdrawn', 'error')", name=op.f("ck_shadow_ingestion_record_results_shadow_record_results_valid_type")),
        sa.UniqueConstraint("shadow_run_id", "producer_record_id", "input_record_hash", "result_type", name="uq_shadow_record_results_stable"),
    )
    op.create_index(op.f("ix_shadow_ingestion_record_results_shadow_run_id"), "shadow_ingestion_record_results", ["shadow_run_id"], unique=False)
    op.create_index(op.f("ix_shadow_ingestion_record_results_producer_record_id"), "shadow_ingestion_record_results", ["producer_record_id"], unique=False)
    op.create_index(op.f("ix_shadow_ingestion_record_results_candidate_id"), "shadow_ingestion_record_results", ["candidate_id"], unique=False)

    for trigger_sql in TRIGGERS:
        op.execute(trigger_sql)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_shadow_ingestion_executions_append_only_delete")
    op.execute("DROP TRIGGER IF EXISTS trg_shadow_ingestion_executions_append_only_update")
    op.drop_index(op.f("ix_shadow_ingestion_record_results_candidate_id"), table_name="shadow_ingestion_record_results")
    op.drop_index(op.f("ix_shadow_ingestion_record_results_producer_record_id"), table_name="shadow_ingestion_record_results")
    op.drop_index(op.f("ix_shadow_ingestion_record_results_shadow_run_id"), table_name="shadow_ingestion_record_results")
    op.drop_table("shadow_ingestion_record_results")
    op.drop_index(op.f("ix_shadow_ingestion_executions_shadow_run_id"), table_name="shadow_ingestion_executions")
    op.drop_table("shadow_ingestion_executions")
    op.drop_index(op.f("ix_shadow_ingestion_runs_input_manifest_hash"), table_name="shadow_ingestion_runs")
    op.drop_index(op.f("ix_shadow_ingestion_runs_producer"), table_name="shadow_ingestion_runs")
    op.drop_table("shadow_ingestion_runs")
