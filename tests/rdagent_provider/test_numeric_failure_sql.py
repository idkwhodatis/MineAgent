"""Construction checks only; actual 027 PostgreSQL activation remains required."""

from backend.app.rdagent_numeric_failure_sql import (
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_failure_sql_uses_actual_mapped_tables_and_fields():
    from backend.app.models import Base
    from backend.app.rdagent_numeric_failure_models import FactorNumericFailureFeedback

    assert FactorNumericFailureFeedback.__tablename__ in TABLES
    assert set(TABLES) <= set(Base.metadata.tables)
    for table, fields in {
        "research_runs": "owner_id active_attempt_id generation status",
        "execution_attempts": "run_id generation lease_expires_at status",
        "factor_numeric_failure_feedbacks": "evaluation_outcome_id candidate_admission_id final_decision_call_id",
        "factor_native_publications": "admission_id",
        "factor_execution_snapshot_publications": "evaluation_id",
        "factor_upstream_call_outcomes": "controller_launch_id status member_count",
        "research_process_receipts": "admission_id payload_sha256 kind",
    }.items():
        assert set(fields.split()) <= set(Base.metadata.tables[table].c.keys())


def test_failure_delta_never_unlocks_valid_or_adoption_or_snapshot():
    sql = render_upgrade_sql()
    assert "NEW.status<>'execution_failed'" in sql
    assert "result.snapshot_id IS NOT NULL" in sql
    assert (
        "mineagent_numeric_rejection_stopped(f.candidate_admission_id) IS NOT TRUE"
        in sql
    )
    assert "factor_generation_outcomes WHERE start_id=src.start_id" in sql
    assert "rd_source_phase_closed ON @SCHEMA@.factor_generation_outcomes" not in sql
    assert (
        "rd_numeric_failure_immutable" in sql and "FOR UPDATE" not in sql
    )  # shared run lock owns serialization
    assert "mineagent_rd_source_run_lock()" in sql
    assert "a.lease_expires_at>clock_timestamp()" in sql
    assert "Legacy evaluation result requires exact observed failure consumer" in sql


def test_deferred_reverse_graph_rechecks_publication_and_receipt_mutations():
    sql = render_upgrade_sql()
    for table in TABLES:
        assert (
            f"AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED"
            in sql
        )
    down = render_downgrade_sql()
    assert "Numeric failure evidence prevents downgrade" in down
    assert "mineagent_rd_source_only_phase()" in down
    assert "DROP TABLE" not in down  # assembler owns the new table literal
