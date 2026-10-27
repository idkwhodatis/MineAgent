"""Construction checks only; actual deferred PostgreSQL tests remain required."""

import pytest

from backend.app.openevolve_operational_ddl import SQL, TABLES, render_sql


@pytest.mark.parametrize(
    "schema", ["", "public;drop", "a.b", "Public", None, 1, "a" * 64]
)
def test_schema_closed(schema):
    with pytest.raises(ValueError, match="Exact SQL schema"):
        render_sql(schema)


def test_emitted_schema_and_closed_rules():
    sql = render_sql("proof_024")
    assert "@S@" not in sql
    assert "@RULES@" not in sql
    assert "mineagent.openevolve-operational-bootstrap.v1" in sql
    assert "additionalProperties" in sql
    assert "CREATE FUNCTION proof_024.mineagent_oe_operational_launch" in sql
    assert "SET search_path=proof_024,pg_catalog" in sql


def test_all_mutable_dependencies_have_deferred_checks():
    sql = render_sql()
    assert len(TABLES) == len(set(TABLES)) == 18
    for table in TABLES:
        assert f"ON public.{table} DEFERRABLE INITIALLY DEFERRED" in sql
    assert sql.count("CREATE CONSTRAINT TRIGGER") == len(TABLES)


def test_float_bootstrap_hashes_actual_text_never_jsonb_reserialization():
    assert "sha256(convert_to(l.bootstrap_canonical,'UTF8'))" in SQL
    assert "l.bootstrap_canonical::jsonb IS DISTINCT FROM b" in SQL
    assert "canonical_json(b," not in SQL
    assert "canonical_json(l.bootstrap," not in SQL
    assert "p.execution_sha256 IS DISTINCT FROM s.execution_sha256" in SQL


def test_controller_three_distinct_identities_and_two_hashes():
    assert "(l.owner_id,l.run_id,l.attempt_id,l.generation,l.id)" in SQL
    assert "e.bootstrap_sha256 IS DISTINCT FROM l.input_inventory_sha256" in SQL
    assert "p->>'bootstrap_sha256' IS DISTINCT FROM l.bootstrap_sha256" in SQL
    assert "intent->>'id' IS DISTINCT FROM a.id::text" in SQL  # native only
    launch = SQL.split("CREATE FUNCTION @S@.mineagent_oe_operational_upload")[0]
    assert "p->>'id'" not in launch  # controller intent UUID is independent


def test_upload_prefix_exact_chunks_and_charge_before_completion():
    assert "actual_chunks<>count_chunks+1" in SQL
    assert "least(65536,sz-off)" in SQL
    assert "generate_series(0,sz-1,65536)" in SQL
    assert "peak:=2*total+2*1048576+2*4096*(count_files+count_chunks+1)" in SQL
    assert "actual_chunks<>count_chunks" in SQL
    assert "GROUP BY epoch_id,save_ordinal HAVING count(*)>1" in SQL
    assert (
        "min(save_ordinal)<>1 OR max(save_ordinal)>4 OR count(*)<>max(save_ordinal)"
        in SQL
    )
    assert "GROUP BY epoch_id HAVING count(*)>1" not in SQL


def test_pending_hold_never_synthesizes_completion():
    assert SQL.count("b.state IN ('reserved','uncertain')") == 2
    assert (
        SQL.count(
            "NOT EXISTS(SELECT 1 FROM research_budget_settlements WHERE reservation_id=b.id)"
        )
        == 2
    )
    assert (
        "AND NOT EXISTS(SELECT 1 FROM research_process_admissions WHERE id=p.admission_id)"
        in SQL
    )
    assert "INSERT INTO" not in SQL
    assert "UPDATE " not in SQL


def test_evaluation_full_binding_keeps_artifacts_and_envelope_separate():
    assert "'manifest_sha256',i.manifest_sha256" in SQL
    assert "'native_artifacts_sha256',e.input_manifest_sha256" in SQL
    assert "'manifest_object_id',i.manifest_object_id" in SQL
    assert "'execution_plan_sha256',p.execution_sha256" in SQL
    assert "'source_sha256',p.source_sha256" in SQL
    assert "intent->'inputs' IS DISTINCT FROM binding" in SQL
    assert "o.size_bytes IS DISTINCT FROM i.manifest_size_bytes" in SQL


def test_exact_preparation_and_fixed_native_budget():
    assert (
        "e.logical_ticket_id,e.physical_ordinal,e.reservation_id,e.process_admission_id"
        in SQL
    )
    assert "p.logical_ticket_id,1,p.reservation_id,p.admission_id" in SQL
    assert "'oe-native:'||p.id::text||':1'" in SQL
    assert '"wall_milliseconds":90000,"storage_bytes":268500992' in SQL
    assert '"evaluator_executions":1,"process_slots":1' in SQL
    assert "interval '1 second'>(l.bootstrap->>'not_after')" in SQL


def test_null_safe_validation_and_no_expression_case_ambiguity():
    assert SQL.count("EXCEPTION WHEN OTHERS THEN RETURN false;") == 4
    assert "IS DISTINCT FROM CASE" not in SQL
    assert "IS NOT TRUE" in SQL
    assert "RETURN NULL;" in SQL


def test_upload_settlement_needs_full_charge_publication_and_actual_stop_projection():
    assert "z.outcome='conservative_unknown' AND z.charge=b.amounts" in SQL
    assert "mineagent_oe_process_valid(l.admission_id,true) IS TRUE" in SQL
    assert "p.intent_id=u.checkpoint_intent_id" in SQL


def test_parameter_free_contract_uses_tuple_array_not_object():
    assert (
        "b#>'{native_inputs,strategy_contract,parameters}' IS DISTINCT FROM '[]'::jsonb"
        in SQL
    )


def test_reverse_process_consumers_cannot_bypass_preparation():
    assert "l.admission_id=a.id AND l.id=a.logical_operation_id" in SQL
    assert (
        "p.admission_id=a.id AND p.id=a.logical_operation_id AND e.process_admission_id=a.id"
        in SQL
    )
    assert (
        "strategy_search_epochs e LEFT JOIN search_controller_launches l ON l.epoch_id=e.id WHERE l.id IS NULL"
        in SQL
    )


def test_checkpoint_cut_binds_control_but_unique_identity_is_save_ordinal():
    assert "WHERE id=i.control_transition_id" in SQL
    assert "control_row.epoch_id,control_row.frontier,control_row.state_sha256" in SQL
    assert "control_row.sequence NOT BETWEEN 1 AND 5" in SQL
    assert "GROUP BY frontier" not in SQL


def test_final_save_may_repeat_same_control_cursor_without_overwriting_history():
    assert "GROUP BY control_transition_id" not in SQL
    assert "GROUP BY epoch_id,save_ordinal" in SQL
    assert "count(*)<>max(save_ordinal)" in SQL
    assert "DELETE FROM" not in SQL


def test_all_budget_dimensions_are_exact_and_save_ordinal_is_required():
    from packages.research_runtime.budget_contracts import BudgetVector

    assert len(BudgetVector.model_fields) == 8
    for field in BudgetVector.model_fields:
        assert f"'{field}'," in SQL
        assert f'"{field}":' in SQL
    assert "'cost_microunits',0" in SQL
    assert '"cost_microunits":0' in SQL
    assert "search_checkpoint_intents WHERE save_ordinal IS NULL" in SQL
