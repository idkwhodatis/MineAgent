"""Extended group exact source and commit-routing proof; no native claims."""

import re

from backend.app import row_validation_oe_032g_base_sql as base
from backend.app import row_validation_oe_032g_sql as m


def test_exact_ten_content_checks_are_separate_and_all_come_first():
    assert len(m.CONSTRAINTS) == 10
    assert len(m.CHANGED) == 7
    assert len(set(m.CHANGED)) == 7
    assert len(base.CONSTRAINTS) == 4
    assert len(base.CHANGED) == 3
    upgrade, downgrade = m.render_upgrade_sql(), m.render_downgrade_sql()
    assert upgrade.count("ADD CONSTRAINT") == 14
    assert upgrade.count("CREATE OR REPLACE FUNCTION") == 7
    assert upgrade.rindex("ADD CONSTRAINT") < upgrade.index(
        "CREATE OR REPLACE FUNCTION"
    )
    assert downgrade.rindex("CREATE OR REPLACE FUNCTION") < downgrade.index(
        "DROP CONSTRAINT"
    )
    assert downgrade.count("DROP CONSTRAINT") == 14
    assert "NOT VALID" not in upgrade
    assert "DROP FUNCTION" not in downgrade
    assert "CREATE TRIGGER" not in upgrade
    for _, name, expression in m.CONSTRAINTS:
        assert name.startswith("ck_rvi_oec_")
        assert expression.endswith("IS TRUE")
        assert not re.search(r"\b(FROM|JOIN|SELECT)\b", expression)
    for forbidden in (
        "checkpoint",
        "sampling",
        "iteration",
        "worker",
        "terminal",
        "structured_strategy_versions",
    ):
        assert all(forbidden not in table for table, _, _ in m.CONSTRAINTS)


def test_exact_removed_content_predicates_preserve_every_other_byte_and_missing_guard():
    assert [len(v) for v in m.edits().values()] == [1, 2, 2, 4, 1]
    for name in m.EXTRA_CHANGED:
        before = base.candidate(name) if name in base.CHANGED else m.original(name)
        after = m.candidate(name)
        for expression in m.edits()[name]:
            assert before.count(expression) == 1
            before = before.replace(expression, "", 1)
        assert before == after
        assert "IF NOT FOUND THEN RAISE EXCEPTION" in after
        source = m.original(name)
        assert [
            line
            for line in source.splitlines()
            if re.search(r"\b(SELECT|PERFORM)\b", line)
        ] == [
            line
            for line in after.splitlines()
            if re.search(r"\b(SELECT|PERFORM)\b", line)
        ]
        assert source.count("RAISE EXCEPTION") == after.count("RAISE EXCEPTION")
    outcome = m.candidate("mineagent_oes_outcome_check")
    assert (
        "mineagent_oes_measurement_matches(result,s.config->'search'->'objective',m)"
        in outcome
    )
    assert "mineagent_structured_stopped(a.id,true)" in outcome
    assert "bs.charge=b.amounts AND bs.outcome='conservative_unknown'" in outcome
    execution = m.candidate("mineagent_oes_execution_check")
    assert "(raw->'program')::text" in execution
    assert "(raw->'snapshot')::text" in execution
    assert "(raw->'rows')::text" in execution
    assert (
        "octet_length(e.strategy_input_canonical) IS DISTINCT FROM e.strategy_input_size_bytes"
        in execution
    )


def test_epoch_relational_schema_dispatch_is_byte_identical():
    old = m.original("mineagent_oes_epoch_check")
    new = m.candidate("mineagent_oes_epoch_check")
    branch = next(
        line
        for line in old.splitlines()
        if "IF (CASE WHEN s.config->>'schema_version'" in line
    )
    assert branch in new
    assert "mineagent.oe-structured-provider-run.v2" in branch
    assert "mineagent_oe_workflow_shape('bootstrap',j)" in branch
    assert "mineagent_rd_schema(j,rule,coalesce(rule->'$defs','{}'::jsonb),0)" in branch
    assert (
        new.count("CASE WHEN s.config->>'schema_version'")
        == old.count("CASE WHEN s.config->>'schema_version'")
        == 2
    )


def test_pinned_commit_routing_reaches_all_five_roots_before_terminal_return():
    graph = m.original("mineagent_oes_graph_check")
    controller = m.original("mineagent_oes_controller_check")
    assert controller.split("BEGIN\n", 1)[1].startswith(
        " PERFORM @SCHEMA@.mineagent_oes_epoch_check(epoch_id);\n"
    )
    terminal = graph.index("IF r.status IS DISTINCT FROM 'succeeded'")
    for table, function in (
        ("epochs", "controller"),
        ("control_states", "control"),
        ("candidates", "candidate"),
        ("executions", "execution"),
        ("engine_outcomes", "outcome"),
    ):
        route = f"FOR item IN SELECT id FROM @SCHEMA@.oe_structured_{table} WHERE run_id=rid LOOP\n  PERFORM @SCHEMA@.mineagent_oes_{function}_check(item.id);"
        assert graph.index(route) < terminal
    append = base.original("mineagent_oes_append")
    assert "IF TG_OP<>'INSERT' THEN RAISE EXCEPTION" in append
    consistent = m.original("mineagent_oes_consistent")
    assert "PERFORM @SCHEMA@.mineagent_oes_graph_check(target)" in consistent
    assert "ELSIF v ? 'run_id' THEN rid:=(v->>'run_id')::uuid" in consistent
    sql = m.render_preflight_sql()
    assert sql.count("Content invariant committed spec scope differs:") == 5
    assert sql.count("Content invariant append/commit routing differs:") == 5
    assert len(m.scope_foreign_keys()) == 5


def test_only_one_extra_pure_function_and_no_public_bypass_or_shared_program_change():
    assert set(m.PURE) - set(base.PURE) == {"mineagent_structured_contract"}
    body = m.original("mineagent_structured_contract")
    assert " IMMUTABLE " in body
    assert set(re.findall(r"\b(mineagent_\w+)\(", body)) == {
        "mineagent_structured_contract",
        "mineagent_rd_schema",
    }
    for name in m.CHANGED:
        assert (
            "(cid uuid)" in m.candidate(name)
            or "(rid uuid)" in m.candidate(name)
            or "(eid uuid)" in m.candidate(name)
            or "(oid uuid)" in m.candidate(name)
            or "(sid uuid)" in m.candidate(name)
        )
    for direction in ("upgrade", "downgrade"):
        sql = m.render_candidate_sql(direction)
        assert "SECURITY DEFINER" not in sql
        assert "SET CONSTRAINTS" not in sql
        assert not re.search(r"\bCOMMIT\s*;", sql)


def test_exact_current_schema_pin_and_oe_only_scope():
    from backend.app import row_validation_invariant_sql as old

    assert base.PREDECESSOR == "032g_schema_keyset_cost"
    assert old.PREDECESSOR == "032f_shared_validation_cost"
    assert base.original("mineagent_rd_schema") != old.original("mineagent_rd_schema")
    assert set(base.edits()) == set(base.CHANGED)
    for table, _, _ in base.CONSTRAINTS + m.CONSTRAINTS:
        assert table.startswith("oe_structured_")
    upgrade = m.render_upgrade_sql()
    assert not re.search(
        r"CREATE OR REPLACE FUNCTION (?:@SCHEMA@\.)?mineagent_rd_", upgrade
    )
    assert "alembic_version" not in upgrade
    for name in m.CHANGED:
        assert m.original(name) == old.original(name) if name in old.SOURCES else True
    assert len(base.maintenance_tables()) == 187
    assert "ARRAY['032g_schema_keyset_cost']" in m.render_preflight_sql()
    assert "ck_rvi_rd_" not in upgrade
