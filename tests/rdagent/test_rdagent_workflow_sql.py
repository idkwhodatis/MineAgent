"""Literal construction checks, not PostgreSQL graph acceptance."""

import json
import re

from backend.app.rdagent_workflow_sql import (
    TABLES,
    render_downgrade_sql,
    render_upgrade_sql,
)


def test_v3_schema_is_exact_and_old_provider_predicate_is_not_replaced():
    sql = render_upgrade_sql()
    assert sql == render_upgrade_sql()
    assert "@CONFIG_SCHEMA@" not in sql
    raw = re.search(r"rule jsonb:='(.+)'::jsonb;", sql).group(1)
    rule = json.loads(raw.replace("''", "'"))
    assert rule["additionalProperties"] is False
    assert set(rule["required"]) == set(rule["properties"])
    assert "workflow_prompt_layout" not in rule["properties"]
    assert "controller_profile" not in rule["properties"]
    assert "mineagent_rd_controller_selection(v) IS NULL" in sql
    assert "v-ARRAY['workflow_prompt_layout','controller_profile'],rule" in sql
    assert "v ? 'workflow_prompt_layout'" in sql
    assert "IS DISTINCT FROM '\"generation_user.v1\"'::jsonb" in sql
    assert sql.count("'workflow_parent','workflow_prompt_layout']") == 2
    assert (
        rule["properties"]["schema_version"]["const"] == "mineagent.factor-research.v3"
    )
    assert rule["properties"]["model_profile"]["const"] == "workflow.rdagent.v1"
    assert "CREATE OR REPLACE" not in sql
    assert "CREATE FUNCTION @SCHEMA@.mineagent_rd_provider_config" not in sql
    assert "mineagent_rd_provider_config(base) IS NOT TRUE" in sql


def test_resolved_order_hash_owner_and_no_hidden_manual_are_checked():
    sql = render_upgrade_sql()
    for fragment in (
        "v#>'{steer_snapshot,versions}' IS DISTINCT FROM '[]'::jsonb",
        "v->>'workflow_inputs_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(w)",
        "b->>'input_sha256' IS DISTINCT FROM mineagent_execution_metadata_sha(s)",
        "jsonb_agg(value->'ref' ORDER BY ord)",
        "octet_length(mineagent_rd_canonical_json(w,true))>32768",
        "encode(sha256(convert_to(x->>'text','UTF8')),'hex')",
    ):
        assert fragment in sql


def test_exact_new_child_graph_and_append_boundary():
    sql = render_upgrade_sql()
    for fragment in (
        "cfg->>'workflow_child_run_id' IS DISTINCT FROM r.id::text",
        "st.child_run_id,st.input_revision_id",
        "cfg#>'{workflow_inputs,selection}' IS DISTINCT FROM sel",
        "FROM factor_research_submissions WHERE run_id=r.id",
        "FROM factor_seeded_submissions WHERE run_id=r.id",
        "FROM outbox_events WHERE run_id=r.id AND generation=1",
        "IF TG_OP<>'INSERT'",
        "FOR UPDATE NOWAIT",
        "FROM execution_attempts WHERE run_id=r.id",
        "Workflow receipt must precede execution authority",
        "Workflow child requires its distinct immutable receipt",
    ):
        assert fragment in sql


def test_later_parent_requires_immediate_feedback_and_prior_real_child():
    sql = render_upgrade_sql()
    for fragment in (
        "ordinal=st.ordinal-2 AND kind='rd_factor_generation'",
        "ordinal=st.ordinal-1 AND kind='experiment_feedback_projection'",
        "fc.terminal_canonical::jsonb->>'rd_checkpoint_id'",
        "fc.terminal_canonical::jsonb->>'state' IS DISTINCT FROM 'succeeded'",
        "rc.terminal_canonical::jsonb->>'source_sha256'",
        "fv.source_provenance",
        "x.parent_factor_version_id IS DISTINCT FROM",
        "Workflow first source must retain its exact parent family",
    ):
        assert fragment in sql
    assert "current_input_revision_id" not in sql
    assert "disabled" not in sql
    assert "pg_read" not in sql


def test_every_relevant_mutation_is_deferred_and_downgrade_refuses_history():
    sql = render_upgrade_sql()
    assert len(TABLES) == len(set(TABLES))
    assert {"factor_research_submissions", "factor_seeded_submissions"} <= set(TABLES)
    assert sql.count("CREATE CONSTRAINT TRIGGER rd_workflow_submission_graph ") == len(
        TABLES
    )
    for table in TABLES:
        assert (
            f"AFTER INSERT OR UPDATE OR DELETE ON @SCHEMA@.{table} DEFERRABLE INITIALLY DEFERRED"
            in sql
        )
    down = render_downgrade_sql()
    assert down.index("Workflow child history cannot be downgraded") < down.index(
        "DROP TRIGGER"
    )
    assert "CASCADE" not in down
    assert down.count("DROP TRIGGER rd_workflow_submission_graph ") == len(TABLES)
