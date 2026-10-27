"""024 SQL construction/preservation checks; not PostgreSQL acceptance."""

import ast
import re
from pathlib import Path

import pytest

from backend.app import openevolve_shared_ddl as ddl


@pytest.mark.parametrize(
    "schema", ["", "Public", "public;drop", "a.b", 'a"b', None, 1, "a" * 64]
)
def test_schema_identifier_closed(schema):
    with pytest.raises(ValueError):
        ddl.render_sql(schema)


def historical_function(name):
    module = ast.parse(
        Path("backend/alembic/versions/020_rd_controller.py").read_text()
    )
    sql = next(
        n.value.value
        for n in module.body
        if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "UPGRADE_SQL"
    )
    match = re.search(
        r"CREATE (?:OR REPLACE )?FUNCTION (?:@SCHEMA@\.)?" + name + r"\(", sql
    )
    raw = sql[
        match.start() : sql.index("END $$;", match.start()) + len("END $$;")
    ].replace("@SCHEMA@", "@S@")
    return re.sub(
        r"CREATE (?:OR REPLACE )?FUNCTION (?:@S@\.)?",
        "CREATE OR REPLACE FUNCTION @S@.",
        raw,
        count=1,
    )


@pytest.mark.parametrize(
    ("name", "stored"),
    [
        ("mineagent_rd_shared_consistent", ddl.PRIOR_RD_SHARED),
        ("mineagent_rd_controller_consistent", ddl.PRIOR_RD_CONTROLLER),
    ],
)
def test_historical020_bodies_retained_exactly(name, stored):
    assert ddl._complete_mutation_budget(ddl._safe_expressions(stored.strip())) == (
        ddl._complete_mutation_budget(ddl._safe_expressions(historical_function(name)))
    )


def test_controller_only_narrow_global_child_exclusion_changes():
    before = ddl._safe_expressions(ddl.PRIOR_RD_CONTROLLER)
    after = ddl.controller_consistency_sql()
    split = " FOR a IN SELECT * FROM research_process_admissions"
    assert after.split(split)[1] == before.split(split)[1]
    assert (
        "child_admission.profile='mineagent.openevolve-orchestrator.v1' AND child_admission.role='openevolve_orchestrator'"
        in after
    )
    assert "child_admission.ipc_policy='host_ticket'" in after
    assert "mineagent_rd_controller_kernel" in after


def test_shared_rd_spec_and_operational_bootstrap_checks_unchanged():
    before = ddl.PRIOR_RD_SHARED
    after = ddl.shared_consistency_sql()
    begin = " FOR r IN SELECT * FROM research_runs WHERE owner_id=owner_uuid AND engine='rdagent' LOOP"
    protected = before[before.index(begin) : before.index(" RETURN NULL;")]
    assert protected in after
    assert (
        "pa.profile NOT IN ('mineagent.rdagent-orchestrator.v1','mineagent.rdagent-orchestrator.v2')"
        in after
    )


def test_only_exact_mutation_consumers_and_equal_scope():
    sql = ddl.shared_consistency_sql()
    assert "rr.engine='rdagent' AND EXISTS" in sql
    assert "rr.engine='openevolve' AND EXISTS" in sql
    assert (
        "FROM factor_generation_starts st WHERE st.mutation_reservation_id=br.id" in sql
    )
    assert (
        "FROM search_iteration_intents st WHERE st.mutation_reservation_id=br.id" in sql
    )
    assert (
        sql.count(
            "(st.owner_id,st.run_id,st.attempt_id,st.generation)=(br.owner_id,br.run_id,br.attempt_id,br.generation)"
        )
        == 2
    )
    assert "br.amounts IS DISTINCT FROM" in sql


def test_profile_gate_preserves_all_existing_and_two_exact_new():
    profiles = set(
        re.findall(
            r"'mineagent\.(?:factor-|rdagent-|qlib-|openevolve-)[^']+'", ddl.PROFILE_SQL
        )
    )
    assert profiles == {
        "'mineagent.factor-prefix-candidate.v2'",
        "'mineagent.factor-numeric-hdf.v1'",
        "'mineagent.rdagent-orchestrator.v1'",
        "'mineagent.rdagent-orchestrator.v2'",
        "'mineagent.qlib-direct-analysis.v1'",
        "'mineagent.openevolve-native-evaluator.v1'",
        "'mineagent.openevolve-orchestrator.v1'",
    }
    assert "ipc_policy='none' AND ticket_sha256 IS NULL" in ddl.PROFILE_SQL
    assert "ipc_policy='host_ticket' AND ticket_sha256 ~" in ddl.PROFILE_SQL
    assert "schema_version='mineagent.process-admission.v1'" in ddl.PROFILE_SQL


def test_sql_boundaries_and_no_bind_parameter_interpretation_required():
    sql = ddl.render_sql("tenant_024")
    assert "@S@" not in sql and "@OE_KINDS@" not in sql
    assert "SET search_path=tenant_024,pg_catalog" in sql
    clean = re.sub(r"'(?:''|[^'])*'", "STRING", sql)
    clean = re.sub(r"--[^\n]*", "", clean)
    balance = 0
    for char in clean:
        balance += (char == "(") - (char == ")")
        assert balance >= 0
    assert balance == 0
    assert clean.count("$$") % 2 == 0
    assert "WHEN TG_OP=" not in ddl.RUN_SQL


def test_run_model_context_and_lock_are_engine_specific():
    sql = ddl.render_sql()
    assert "strategy_version_id IS NOT NULL" in ddl.RUN_SQL
    assert (
        "purpose='factor_research' AND engine='rdagent' AND strategy_version_id IS NULL"
        in ddl.RUN_SQL
    )
    assert (
        "owner_id::text=config->>'owner_id' AND id::text=config->>'run_id'"
        in ddl.RUN_SQL
    )
    assert (
        "request_sha256=encode(sha256(convert_to(public.mineagent_rd_canonical_json(config,false),'UTF8')),'hex')"
        in sql
    )
    assert (
        "mineagent.neutral-model.v1" in sql and "mineagent.rdagent-embedding.v1" in sql
    )
    assert "mineagent_oe_request(op.request,op.protocol) IS NOT TRUE" in sql
    assert "cx.context_sha256 IS DISTINCT FROM" in sql
    assert "IF actual_engine IN ('rdagent','openevolve') THEN" in sql
    assert "FOR UPDATE NOWAIT" in ddl.LOCK_SQL
    assert "pg_advisory" not in ddl.LOCK_SQL


def test_object_kinds_closed_and_run_scoped():
    assert len(ddl.PRIOR_KINDS) == 7
    assert len(set(ddl.OE_KINDS)) == 16
    assert not (set(ddl.PRIOR_KINDS) & set(ddl.OE_KINDS))
    sql = ddl.render_sql()
    assert "rr.engine<>'openevolve'" in sql
    assert "OE object publication requires exact search run" in sql
    assert "LIKE" not in ddl.PROFILE_SQL
    assert "mineagent_analysis_raw_json" not in sql
    assert "mineagent_analysis_raw_commitment" not in sql


def test_exact_replacements_reject_changed_predecessor():
    with pytest.raises(ValueError):
        ddl._once("missing", "old", "new")
    with pytest.raises(ValueError):
        ddl._once("old old", "old", "new")


def test_emitted_case_expressions_are_parenthesized_without_changing_statement_case():
    sql = ddl.render_sql()
    assert "IS DISTINCT FROM CASE" not in sql
    assert ":=CASE WHEN" not in sql
    assert "CASE p.kind WHEN 'launch_intent'" in sql
    assert "END CASE;" in sql
    assert (
        "IS DISTINCT FROM (CASE WHEN child IS NULL THEN 'null'::jsonb ELSE 'true'::jsonb END))"
        in sql
    )


def test_mutation_budget_compares_all_eight_dimensions_exactly():
    import json

    from packages.research_runtime.budget_contracts import BudgetVector

    sql = ddl.shared_consistency_sql()
    match = re.search(r"br.amounts IS DISTINCT FROM '(.*?)'::jsonb", sql)
    assert match is not None
    assert json.loads(match[1]) == BudgetVector(mutation_slots=1).model_dump()
    assert len(json.loads(match[1])) == 8
    assert '"cost_microunits":0' in match[1]
    assert "br.amounts @>" not in sql
    assert ddl._complete_mutation_budget(sql) == sql
