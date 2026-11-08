"""Source proof only. PostgreSQL execution is a separate opt-in integration suite."""

import ast
import hashlib
import re
from pathlib import Path

import pytest

from backend.app import row_validation_invariant_sql as m

ROOT = Path(__file__).resolve().parents[2]
EXPECTED_CHANGED = {
    "mineagent_rd_provider_graph",
    "mineagent_rd_provider_queued_config",
    "mineagent_rd_workflow_submission_check",
    "mineagent_oes_spec_check",
    "mineagent_oes_candidate_check",
    "mineagent_oe_workflow_attachment",
}
EXPECTED_CONSTRAINTS = {
    ("research_runs", "ck_rvi_rd_run_config"),
    ("research_model_operations", "ck_rvi_rd_provider_payload"),
    ("oe_structured_specs", "ck_rvi_oe_spec_config"),
    ("oe_structured_specs", "ck_rvi_oe_spec_request"),
    ("oe_structured_specs", "ck_rvi_oe_spec_config_raw"),
    ("oe_structured_specs", "ck_rvi_oe_spec_request_raw"),
}


def strings_removed(sql):
    # The reviewed functions contain ordinary SQL string literals, including
    # escaped/doubled quotes in schema JSON. Strip them before relation checks.
    return re.sub(r"'(?:''|[^'])*'", "''", re.sub(r"--[^\n]*", "", sql))


def test_every_frozen_literal_is_pinned_without_importing_migration_code():
    for (filename, key), digest in m.PINS.items():
        value = m.literal(filename, key)
        assert hashlib.sha256(value.encode()).hexdigest() == digest
    for name in m.SOURCES:
        sql = m.original(name)
        assert sql.startswith("CREATE OR REPLACE FUNCTION @SCHEMA@." + name + "(")
    # No dynamic/evolving renderer supplies any validator/graph body.
    tree = ast.parse(Path(m.__file__).read_text())
    imports = [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert imports == ["__future__", "pathlib"]


def test_exact_pure_transitive_closure_has_no_application_relations_or_state():
    found = set()
    pending = list(m.ROOTS)
    tables = m.maintenance_tables()
    while pending:
        name = pending.pop()
        if name in found:
            continue
        found.add(name)
        sql = m.original(name)
        header, body = sql.split("AS $$", 1)
        assert " IMMUTABLE " in header
        assert "SECURITY DEFINER" not in header
        body = strings_removed(body)
        pending.extend(set(re.findall(r"\b(mineagent_\w+)\s*\(", body)) - found)
        assert not re.search(
            r"\b(INSERT|UPDATE|DELETE|EXECUTE|LOCK|PERFORM|nextval|set_config|clock_timestamp|random)\b",
            body,
            re.IGNORECASE,
        )
        for table in tables:
            assert not re.search(
                r"\b(?:FROM|JOIN)\s+(?:@SCHEMA@\.)?" + table + r"\b",
                body,
                re.IGNORECASE,
            )
    assert found == set(m.PURE)
    assert len(found) == 17


def test_only_six_guarded_row_functions_change_and_all_other_bytes_survive():
    assert set(m.CHANGED) == EXPECTED_CHANGED
    assert set(m.edits()) == EXPECTED_CHANGED
    for name in m.CHANGED:
        old, new = m.original(name), m.candidate(name)
        assert old != new
        old_masked, new_masked = old, new
        for before, after in m.edits()[name]:
            # Empty replacements are deletion sites; use the sequential edit
            # proof below rather than trying to guess their position afterward.
            old_masked = old_masked.replace(before, after, 1)
        assert old_masked == new_masked
        # Every SELECT/FOR/PERFORM statement and strict-fetch count survives.
        read_lines = lambda sql: [
            line
            for line in sql.splitlines()
            if re.search(r"\b(SELECT|PERFORM|FOR .+ IN)\b", line)
        ]
        assert read_lines(new) == read_lines(old)
        assert new.count("INTO STRICT") == old.count("INTO STRICT")
        assert new.count("RAISE EXCEPTION") == old.count("RAISE EXCEPTION")
        assert new.count("EXCEPTION WHEN") == old.count("EXCEPTION WHEN")
        assert new.split("AS $$", 1)[0] == old.split("AS $$", 1)[0]
    assert [len(m.edits()[name]) for name in m.CHANGED] == [2, 1, 1, 4, 2, 2]


def test_rd_lazy_boundaries_fallbacks_and_payload_binding_are_exact():
    graph = m.candidate("mineagent_rd_provider_graph")
    assert "IF NOT config_checked THEN" in graph
    assert ">32\n THEN RAISE EXCEPTION" in graph
    assert graph.index("FOR op IN SELECT") < graph.index("config_valid:=")
    assert m.run_guard("run", "run.config") in graph
    assert (
        "ELSE (CASE WHEN run.config->>'schema_version'='mineagent.factor-research.v3' THEN mineagent_rd_workflow_config(run.config) ELSE mineagent_rd_provider_config(run.config) END) END)"
        in graph
    )
    assert "mineagent_rd_provider_payload('binding',binding)" in graph
    assert "mineagent_rd_provider_payload(op.request->>'kind',op.request)" not in graph
    assert (
        "AND protocol IN ('mineagent.rdagent-provider-chat.v1','mineagent.rdagent-provider-embedding.v1') LOOP"
        in graph
    )
    assert "op.protocol IS DISTINCT FROM op.request->>'schema_version'" in graph
    assert "mineagent_rd_canonical_json(op.request,false)" in graph
    assert "mineagent_rd_canonical_json(op.request,true)" in graph
    workflow = m.candidate("mineagent_rd_workflow_submission_check")
    assert m.run_guard("r", "cfg", workflow_only=True) in workflow
    assert "ELSE mineagent_rd_workflow_config(cfg) END) IS NOT TRUE" in workflow
    assert "SELECT * INTO STRICT r FROM research_runs" in workflow
    assert "PERFORM mineagent_analysis_plan_check(p.id)" in workflow
    assert "PERFORM mineagent_rd_provider_queued_config(r.id)" in workflow


def test_oe_missing_rows_seed_candidate_workflow_and_raw_maxima_survive():
    spec = m.candidate("mineagent_oes_spec_check")
    assert "IF NOT FOUND THEN RAISE EXCEPTION 'Structured search spec missing'" in spec
    assert "r.config IS DISTINCT FROM c" in spec
    assert (
        "mineagent_structured_raw_commitment(v.program_canonical,v.program,v.program_sha256,65536)"
        in spec
    )
    candidate = m.candidate("mineagent_oes_candidate_check")
    assert "PERFORM @SCHEMA@.mineagent_oes_spec_check(c.run_id)" in candidate
    assert "s.owner_id IS DISTINCT FROM c.owner_id" in candidate
    assert "mineagent_oes_contract('candidate',j)" in candidate
    assert (
        "mineagent_structured_raw_commitment(c.candidate_canonical,j,c.candidate_sha256,32768)"
        in candidate
    )
    attachment = m.candidate("mineagent_oe_workflow_attachment")
    assert "SELECT * INTO STRICT sp FROM oe_structured_specs" in attachment
    assert (
        "cfg->>'schema_version' IS DISTINCT FROM 'mineagent.oe-structured-provider-run.v2'"
        in attachment
    )
    assert "sp.request->'workflow_context' IS DISTINCT FROM ctx" in attachment
    assert "mineagent_oes_contract(" not in attachment


def test_fresh_checks_are_first_immediate_exact_and_downgrade_restores_first():
    upgrade, downgrade = m.render_upgrade_sql(), m.render_downgrade_sql()
    assert {(table, name) for table, name, _ in m.CONSTRAINTS} == EXPECTED_CONSTRAINTS
    assert upgrade.count("ADD CONSTRAINT") == 6
    assert "NOT VALID" not in upgrade
    assert "VALIDATE CONSTRAINT" not in upgrade
    assert upgrade.rindex("ADD CONSTRAINT") < upgrade.index(
        "CREATE OR REPLACE FUNCTION"
    )
    assert upgrade.count("CREATE OR REPLACE FUNCTION") == 6
    for _, _, expression in m.CONSTRAINTS:
        assert expression.endswith("IS TRUE")
        assert not re.search(r"\b(FROM|JOIN|SELECT)\b", expression)
    assert ",config_sha256,1048576) IS TRUE" in upgrade
    assert ",request_sha256,65536) IS TRUE" in upgrade
    assert "ELSE true END) IS TRUE" in m.CONSTRAINTS[0][2]
    assert "ELSE true END) IS TRUE" in m.CONSTRAINTS[1][2]
    assert downgrade.startswith("\n".join(m.original(name) for name in m.CHANGED))
    assert downgrade.rindex("CREATE OR REPLACE FUNCTION") < downgrade.index(
        "DROP CONSTRAINT"
    )
    assert downgrade.count("DROP CONSTRAINT") == 6
    for forbidden in (
        "DROP FUNCTION",
        "DROP TRIGGER",
        "DISABLE TRIGGER",
        "SECURITY DEFINER",
        "GRANT ",
        "CREATE TEMP",
        "SET CONSTRAINTS",
        "UPDATE ",
    ):
        assert forbidden not in upgrade + downgrade


def test_maintenance_and_catalog_preflight_are_fail_closed():
    sql = m.render_candidate_sql()
    assert (
        sql.index("pg_try_advisory_xact_lock")
        < sql.index("LOCK TABLE")
        < sql.index("p.prosrc=")
        < sql.index("ADD CONSTRAINT")
    )
    assert "IN ACCESS EXCLUSIVE MODE NOWAIT" in sql
    assert len(m.maintenance_tables()) > 100
    assert "transaction_isolation') <> 'read committed'" in sql
    assert "server_encoding')<>'UTF8'" in sql
    assert sql.count("Row invariant pinned function differs:") == len(m.SOURCES)
    assert "AND NOT p.prosecdef" in sql
    assert "Exact032f candidate schema required" in sql
    assert "AND tgtype=31" in sql
    assert "NOT NULL column differs: oe_structured_candidates.owner_id" in sql
    for direction in ("upgrade", "downgrade"):
        assert not re.search(r"\bCOMMIT\s*;", m.render_candidate_sql(direction))
    with pytest.raises(ValueError):
        m.render_candidate_sql("unknown")
    with pytest.raises(ValueError):
        m.replace_once("x x", "x", "y")


def test_scope_has_no_head_wiring_and_no_new_function_signature():
    from backend.app.core.db import REQUIRED_SCHEMA_REVISION

    assert REQUIRED_SCHEMA_REVISION == m.PREDECESSOR
    assert not list(m.VERSIONS.glob("*row_validation_invariant*"))
    assert (
        set(
            re.findall(
                r"CREATE OR REPLACE FUNCTION @SCHEMA@\.(\w+)\(", m.render_upgrade_sql()
            )
        )
        == EXPECTED_CHANGED
    )


def test_function_argument_defaults_and_optimizer_properties_are_pinned():
    for name in m.SOURCES:
        source = m.original(name)
        assertion = m.function_assertion(source)
        assert "pg_get_expr(p.proargdefaults,0) IS NOT DISTINCT FROM" in assertion
        assert "p.proargnames IS NOT DISTINCT FROM" in assertion
        assert "p.prosupport=0" in assertion
        if "DEFAULT 0" in source.split("AS $$", 1)[0]:
            assert "p.pronargdefaults=1" in assertion
            assert (
                "pg_get_expr(p.proargdefaults,0) IS NOT DISTINCT FROM '0'" in assertion
            )
        else:
            assert "p.pronargdefaults=0" in assertion
            assert (
                "pg_get_expr(p.proargdefaults,0) IS NOT DISTINCT FROM NULL" in assertion
            )
